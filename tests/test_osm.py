"""OpenStreetMap source: query building, caching, failover and the pipeline type.

A local HTTP server plays Overpass, so nothing here touches the network.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from disscube.pipeline import PipelineError, load, plan, run
from disscube.sources import osm

# A straight road across the middle of the 3 km grid below (EPSG:31983 ≈ lon -46.3, lat -23.6 at the origin).
BBOX = [-46.30, -23.60, -46.27, -23.57]
ROAD = {"type": "way", "id": 11, "tags": {"highway": "trunk", "ref": "BR-1", "surface": "asphalt"},
        "geometry": [{"lat": -23.585, "lon": -46.31}, {"lat": -23.585, "lon": -46.26}]}


class _Overpass:
    """A fake Overpass server. ``script`` holds one status per request; the last one repeats."""

    def __init__(self, elements=None, script=(200,), remark=None):
        self.requests: list[dict] = []
        self.script = list(script)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"query": urllib.parse.parse_qs(body.decode())["data"][0],
                                       "agent": self.headers.get("User-Agent")})
                status = outer.script[min(len(outer.requests) - 1, len(outer.script) - 1)]
                payload = {"osm3s": {"timestamp_osm_base": "2026-09-30T00:00:00Z"},
                           "elements": [ROAD] if elements is None else elements}
                if remark:
                    payload = {"remark": remark, "elements": []}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/interpreter"
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def overpass():
    servers = []

    def make(**kw):
        servers.append(_Overpass(**kw))
        return servers[-1]

    yield make
    for s in servers:
        s.close()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("DISSCUBE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("OSM_CONTACT", "tests@example.org")
    monkeypatch.setattr(osm, "_sleep", lambda s: None)


# --- query building -------------------------------------------------------------------------

def test_statements_are_split_cleaned_and_checked():
    assert osm.statements_of('way["highway"]; way["waterway"] ;') == ['way["highway"]', 'way["waterway"]']
    assert osm.statements_of(['way["a"]', 'way["b"]']) == ['way["a"]', 'way["b"]']
    for bad in ("", "  ;  ", 'node["place"]', 'rel["route"]'):
        with pytest.raises(ValueError):
            osm.statements_of(bad)


def test_bbox_is_added_to_each_statement_and_padded():
    assert osm.padded([-46.3, -23.6, -46.27, -23.57], 0.1) == pytest.approx([-46.4, -23.7, -46.17, -23.47])
    assert osm.padded([-179.9, -89.95, 179.9, 89.95], 1) == [-180.0, -90.0, 180.0, 90.0]
    q = osm.build_query(['way["a"]', 'way["b"]'], [1, 2, 3, 4])
    assert 'way["a"](2,1,4,3);way["b"](2,1,4,3);' in q and q.endswith("out geom;")
    assert "[date:" not in q


def test_date_gives_a_snapshot_query_and_must_be_iso():
    assert '[date:"2019-01-01T00:00:00Z"]' in osm.build_query(['way["a"]'], [1, 2, 3, 4], "2019-01-01")
    assert '[date:"2019-07-01T12:30:00Z"]' in osm.build_query(['way["a"]'], [1, 2, 3, 4], "2019-07-01T12:30:00Z")
    with pytest.raises(ValueError, match="ISO 8601"):
        osm.build_query(['way["a"]'], [1, 2, 3, 4], "last year")


def test_geojson_keeps_lines_and_a_few_tags():
    fc = osm.to_geojson({"elements": [ROAD, {"type": "node", "id": 1, "lat": 0, "lon": 0},
                                      {"type": "way", "id": 2, "geometry": [{"lat": 0, "lon": 0}]}]})
    assert [f["properties"]["osm_id"] for f in fc["features"]] == [11]
    props = fc["features"][0]["properties"]
    assert props == {"osm_id": 11, "highway": "trunk", "ref": "BR-1"}  # `surface` is not kept
    assert fc["features"][0]["geometry"]["coordinates"][0] == [-46.31, -23.585]


# --- fetching --------------------------------------------------------------------------------

def test_fetch_caches_the_answer_and_identifies_itself(overpass):
    srv = overpass()
    path, info = osm.fetch('way["highway"]', BBOX, margin=0.05, endpoints=[srv.url])
    assert len(srv.requests) == 1 and srv.requests[0]["agent"] == "disscube (tests@example.org)"
    assert info["features"] == 1 and info["license"] == "ODbL" and info["osm_base"] == "2026-09-30T00:00:00Z"
    assert info["bbox"] == pytest.approx([-46.35, -23.65, -46.22, -23.52])

    again, info2 = osm.fetch('way["highway"]', BBOX, margin=0.05, endpoints=[srv.url])
    assert again == path and info2 == info and len(srv.requests) == 1  # no second request

    osm.fetch('way["highway"]', BBOX, margin=0.06, endpoints=[srv.url])  # another margin is another request
    osm.fetch('way["highway"]', BBOX, margin=0.05, date="2015-01-01", endpoints=[srv.url])
    assert len(srv.requests) == 3


def test_failover_to_the_next_server_and_retry_rounds(overpass):
    broken, ok = overpass(script=(504,)), overpass()
    osm.fetch('way["highway"]', BBOX, endpoints=[broken.url, ok.url])
    assert len(broken.requests) == 1 and len(ok.requests) == 1

    flaky = overpass(script=(503, 503, 200))
    osm.fetch('way["waterway"]', BBOX, endpoints=[flaky.url])  # fails twice, then answers
    assert len(flaky.requests) == 3


def test_gives_up_after_all_rounds_and_says_why(overpass):
    down = overpass(script=(504,))
    with pytest.raises(osm.OsmError, match="did not answer.*HTTP 504"):
        osm.fetch('way["highway"]', BBOX, endpoints=[down.url])
    assert len(down.requests) == osm.ROUNDS


def test_406_stops_at_once_and_points_to_osm_contact(overpass):
    srv = overpass(script=(406,))
    with pytest.raises(osm.OsmError, match="OSM_CONTACT"):
        osm.fetch('way["highway"]', BBOX, endpoints=[srv.url, srv.url])
    assert len(srv.requests) == 1


def test_server_side_remark_counts_as_a_failure(overpass):
    srv = overpass(remark="runtime error: Query timed out", script=(200,))
    with pytest.raises(osm.OsmError, match="Query timed out"):
        osm.fetch('way["highway"]', BBOX, endpoints=[srv.url])


def test_empty_answer_is_an_error_and_is_not_cached(overpass):
    srv = overpass(elements=[])
    for _ in range(2):
        with pytest.raises(osm.OsmError, match="no ways"):
            osm.fetch('way["highway"]', BBOX, endpoints=[srv.url])
    assert len(srv.requests) == 2 and not list(osm.default_cache_dir().glob("*.geojson"))


def test_endpoint_must_be_http():
    with pytest.raises(ValueError, match="http"):
        osm.fetch('way["highway"]', BBOX, endpoints=["file:///etc/passwd"])


# --- in a pipeline ---------------------------------------------------------------------------

def _pipeline(tmp_path, srv, extra="", source_extra=""):
    path = tmp_path / "osm.toml"
    path.write_text(f"""
schema = 1
[grid]
name = "g"
crs = "EPSG:31983"
bbox = [333000, 7388000, 336000, 7391000]
resolution = 300
{extra}
[[source]]
id = "road"
type = "osm"
query = 'way["highway"]["ref"~"BR-1"]'
endpoints = ["{srv.url}"]
{source_extra}
[[derive]]
target = "dist_road"
source = "road"
operator = "distance"
""", encoding="utf-8")
    return path


def test_pipeline_fetches_registers_and_derives_a_distance(tmp_path, overpass):
    srv = overpass()
    report = run(_pipeline(tmp_path, srv), workspace=tmp_path / "ws")
    assert report.derived and len(srv.requests) == 1
    prov = json.loads((tmp_path / "ws" / "raw" / "road.provenance.json").read_text())
    assert prov["type"] == "osm" and prov["format"] == "vector" and prov["features"] == 1
    assert prov["checksum"].startswith("sha256:") and prov["statements"] == ['way["highway"]["ref"~"BR-1"]']
    assert prov["attribution"] == "© OpenStreetMap contributors" and Path(prov["pipeline"]["file"]).name == "osm.toml"
    assert (tmp_path / "ws" / "raw" / "road.geojson").exists()


def test_same_pipeline_in_another_workspace_reuses_the_cache_and_the_spec_hash(tmp_path, overpass):
    from disscube import CubeClient

    srv = overpass()
    path = _pipeline(tmp_path, srv)
    run(path, workspace=tmp_path / "a")
    run(path, workspace=tmp_path / "b")
    assert len(srv.requests) == 1

    def spec_hash(ws):
        cube = CubeClient(str(ws / "catalog.db"), str(ws / "store"))
        return [d.spec_hash for d in cube.catalog.search_derived_variables() if d.name == "dist_road"]

    assert spec_hash(tmp_path / "a") == spec_hash(tmp_path / "b") != []


def test_years_give_one_dated_snapshot_each(tmp_path, overpass):
    srv = overpass()
    path = tmp_path / "years.toml"
    path.write_text(f"""
schema = 1
extent = [-46.30, -23.60, -46.27, -23.57]
[[source]]
id = "road_{{year}}"
type = "osm"
query = 'way["highway"]'
date = "{{year}}-07-01"
endpoints = ["{srv.url}"]
years = [2010, 2020]
""", encoding="utf-8")
    p = plan(path)
    assert [(s.id, s.config.time, s.config.date) for s in p.sources] == [
        ("road_2010", 2010, "2010-07-01"), ("road_2020", 2020, "2020-07-01")]
    run(path, workspace=tmp_path / "ws")
    assert ['[date:"2010-07-01T00:00:00Z"]' in srv.requests[0]["query"],
            '[date:"2020-07-01T00:00:00Z"]' in srv.requests[1]["query"]] == [True, True]


def test_a_failed_download_is_a_pipeline_error_naming_the_source(tmp_path, overpass):
    srv = overpass(script=(406,))
    with pytest.raises(PipelineError, match=r"source 'road'.*OSM_CONTACT"):
        run(_pipeline(tmp_path, srv), workspace=tmp_path / "ws")


def test_validation_without_network(tmp_path, overpass):
    srv = overpass()
    load(_pipeline(tmp_path, srv, source_extra="margin = 0.5\ndate = \"2015-01-01\""))
    assert not srv.requests
    bad = tmp_path / "bad.toml"
    bad.write_text('schema = 1\n[[source]]\nid = "r"\ntype = "osm"\nquery = "way[x]"\n', encoding="utf-8")
    with pytest.raises(PipelineError, match="extent"):  # a sources-only osm file needs an area
        load(bad)
    neg = _pipeline(tmp_path, srv, source_extra="margin = -1")
    with pytest.raises(PipelineError, match="margin"):
        load(neg)


def test_distance_to_the_fetched_road_is_right(tmp_path, overpass):
    from disscube import CubeClient

    srv = overpass()
    run(_pipeline(tmp_path, srv, source_extra="margin = 0.2"), workspace=tmp_path / "ws")
    cube = CubeClient(str(tmp_path / "ws" / "catalog.db"), str(tmp_path / "ws" / "store"))
    d = np.asarray(cube.load("dist_road").values)
    assert np.isfinite(d).all() and d.min() >= 0 and d.max() > d.min()  # a gradient away from the road
