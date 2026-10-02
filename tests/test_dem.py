"""DEM source: tiles, voids, mosaics, slope on a known plane, TOPODATA download, and the pipeline type.

Everything uses synthetic tiles on disk and a local HTTP server; nothing touches the network.
"""

from __future__ import annotations

import gzip
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient
from disscube.pipeline import PipelineError, load, run
from disscube.sources import dem

VALID_MAX = dem.VALID_RANGE[1]
LAB15 = [-54.842, -3.587, -54.459, -3.168]
M_PER_DEG_LAT = 110_580.0  # one degree of latitude near 3.4°S
RISE = 0.05                # the test plane climbs 5 cm per metre northward (stays under 9 000 m over a degree)
EXPECTED_DEG = float(np.degrees(np.arctan(RISE)))  # 2.862°


def _write_tif(path, west, north, size_deg, pixel, z):
    """A 1-band float32 GeoTIFF in EPSG:4326 whose upper-left corner is (west, north)."""
    rows, cols = z.shape
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(west, north, pixel, pixel), nodata=np.nan) as d:
        d.write(z.astype("float32"), 1)
    return str(path)


def _plane(west, south, east, north, pixel):
    """Elevation that climbs northward by RISE m per metre, in a tile covering the given box."""
    lat = north - (np.arange(round((north - south) / pixel)) + 0.5) * pixel
    cols = round((east - west) / pixel)
    return np.tile(((lat - south) * M_PER_DEG_LAT * RISE)[:, None], (1, cols))


@pytest.fixture
def plane_tile(tmp_path):
    box = (-54.95, -3.70, -54.35, -3.05)
    return _write_tif(tmp_path / "plane.tif", box[0], box[3], 0.0, 0.0005, _plane(*box, 0.0005))


@pytest.fixture
def cube(tmp_path):
    return CubeClient(str(tmp_path / "cat.db"), str(tmp_path / "store"))


# --- naming and geometry ---------------------------------------------------------------------

def test_tile_names_for_the_lab15_area():
    box = dem.padded(LAB15, 0.02)
    assert dem.tile_urls(box, "srtm") == ["https://s3.amazonaws.com/elevation-tiles-prod/skadi/S04/S04W055.hgt.gz"]
    assert dem.tile_urls(box, "copernicus")[0].endswith("Copernicus_DSM_COG_10_S04_00_W055_00_DEM.tif")
    assert dem.tile_urls(box, "topodata") == ["http://www.dsr.inpe.br/topodata/data/geotiff/03S555ZN.zip"]


def test_tiles_across_boundaries_and_exact_edges():
    names = [u.rsplit("/", 1)[1] for u in dem.tile_urls([-55.2, -4.5, -53.8, -2.5], "srtm")]
    assert sorted(names) == sorted(f"S{la:02d}W{lo:03d}.hgt.gz" for la in (5, 4, 3) for lo in (56, 55, 54))
    assert len(dem.tile_urls([-55.0, -4.0, -54.0, -3.0], "srtm")) == 1  # edges on whole degrees: one tile
    assert [u.rsplit("/", 1)[1] for u in dem.tile_urls([10.2, 50.2, 10.8, 50.8], "srtm")] == ["N50E010.hgt.gz"]
    with pytest.raises(ValueError, match="unknown DEM"):
        dem.tile_urls(LAB15, "aster")


def test_utm_zone_of_the_grid():
    assert dem.utm_epsg(LAB15) == 32721
    assert dem.utm_epsg([10, 50, 11, 51]) == 32632
    assert dem.utm_epsg([-70, 40, -69, 41]) == 32619


# --- reading ---------------------------------------------------------------------------------

def test_slope_of_a_known_plane(plane_tile):
    elevation, used = dem.read_dem(dem.padded(LAB15, 0.02), tiles=[plane_tile])
    assert used == [plane_tile]
    deg = dem.make_product(elevation, "slope_deg", LAB15, 30)
    pct = dem.make_product(elevation, "slope_pct", LAB15, 30)
    assert deg.crs.to_epsg() == 32721 and abs(deg.transform.a) == 30
    inner = (slice(40, -40), slice(40, -40))  # away from the edges of the window
    assert np.isfinite(elevation.data).all() and np.nanmax(elevation.data) < VALID_MAX  # the plane is not read as voids
    assert np.nanmean(deg.data[inner]) == pytest.approx(EXPECTED_DEG, abs=0.05)
    assert np.nanmean(pct.data[inner]) == pytest.approx(100 * RISE, abs=0.15)
    valid = deg.data[inner][np.isfinite(deg.data[inner])]
    assert np.mean(np.abs(valid - EXPECTED_DEG) > 0.1) < 0.01  # a plane has one slope (edge pixels aside)


def test_elevation_stays_in_wgs84_and_voids_become_nan(tmp_path):
    z = np.full((400, 400), 120.0)
    z[100:110, 100:110] = -32768.0
    z[200, 200] = 12000.0
    path = _write_tif(tmp_path / "v.tif", -54.9, -3.1, 0.0, 0.0005, z)
    win, _ = dem.read_dem([-54.88, -3.28, -54.72, -3.12], tiles=[path])
    assert win.crs.to_epsg() == 4326
    assert np.isnan(win.data).sum() > 0 and np.nanmax(win.data) == 120.0


def test_two_tiles_make_one_continuous_window(tmp_path):
    left = _write_tif(tmp_path / "l.tif", -55.0, -3.0, 0.0, 0.001, np.full((1000, 1000), 10.0))
    right = _write_tif(tmp_path / "r.tif", -54.0, -3.0, 0.0, 0.001, np.full((1000, 1000), 20.0))
    win, used = dem.read_dem([-54.1, -3.6, -53.9, -3.4], tiles=[left, right])  # straddles -54°
    assert used == [left, right] and set(np.unique(win.data[~np.isnan(win.data)])) == {10.0, 20.0}
    assert not np.isnan(win.data).any()


def test_a_missing_tile_is_skipped_with_a_warning_but_all_missing_is_an_error(tmp_path, plane_tile, caplog):
    with caplog.at_level("WARNING", logger="disscube.sources.dem"):
        _, used = dem.read_dem(dem.padded(LAB15, 0.02), tiles=[str(tmp_path / "nope.tif"), plane_tile])
    assert used == [plane_tile] and "tile skipped" in caplog.text
    with pytest.raises(dem.DemError, match="no elevation data"):
        dem.read_dem(LAB15, tiles=[str(tmp_path / "nope.tif")])


def test_exactly_one_of_dem_or_tiles():
    with pytest.raises(ValueError, match="exactly one"):
        dem.read_dem(LAB15)
    with pytest.raises(ValueError, match="exactly one"):
        dem.read_dem(LAB15, dem="srtm", tiles=["a.tif"])


# --- SRTM (gzip-ed HGT) and Copernicus names, through the real download paths -----------------

def test_srtm_hgt_gz_is_read_through_vsigzip(tmp_path, monkeypatch):
    n = 1201
    z = np.full((n, n), 300, dtype="int16")
    z[500:520, 500:520] = -32768  # a void
    hgt = tmp_path / "S04W055.hgt"
    with rasterio.open(hgt, "w", driver="SRTMHGT", height=n, width=n, count=1, dtype="int16", crs="EPSG:4326",
                       transform=from_origin(-55, -3, 1 / 1200, 1 / 1200)) as d:
        d.write(z, 1)
    (tmp_path / "S04W055.hgt.gz").write_bytes(gzip.compress(hgt.read_bytes()))
    monkeypatch.setattr(dem, "SRTM_URL", str(tmp_path) + "/{ns}{lat:02d}{ew}{lon:03d}.hgt.gz")
    win, used = dem.read_dem(dem.padded(LAB15, 0.02), dem="srtm")
    assert used[0].endswith("S04W055.hgt.gz")
    assert np.nanmax(win.data) == 300 and np.isnan(win.data).sum() > 0 and win.data.shape[0] > 100


def test_copernicus_warns_about_the_canopy_when_asked_for_slope(tmp_path, plane_tile, cube, monkeypatch, caplog):
    monkeypatch.setattr(dem, "COPERNICUS_URL", plane_tile.replace("plane.tif", "{ns}{lat:02d}{ew}{lon:03d}.tif"))
    (tmp_path / "S04W055.tif").write_bytes((tmp_path / "plane.tif").read_bytes())
    with caplog.at_level("WARNING", logger="disscube.sources.dem"):
        dem.register_dem_source(cube, "slope", LAB15, tmp_path / "raw", dem="copernicus", product="slope_deg")
    assert "canopy" in caplog.text
    prov = json.loads((tmp_path / "raw" / "slope.provenance.json").read_text())
    assert prov["dem"] == "copernicus" and "canopy" in prov["surface"]
    caplog.clear()
    with caplog.at_level("WARNING", logger="disscube.sources.dem"):
        dem.register_dem_source(cube, "elev", LAB15, tmp_path / "raw", dem="copernicus", product="elevation")
    assert "canopy" not in caplog.text  # the warning is about slope only


# --- TOPODATA: a ZIP downloaded once ---------------------------------------------------------

@pytest.fixture
def topodata_server(tmp_path, monkeypatch):
    sheet = tmp_path / "03S555ZN.tif"
    _write_tif(sheet, -55.5, -3.0, 0.0, 0.0005, _plane(-55.5, -4.0, -54.0, -3.0, 0.0005))
    archive = tmp_path / "03S555ZN.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(sheet, "03S555ZN.tif")
    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            served.append(self.path)
            if self.path.endswith("03S555ZN.zip"):
                body = archive.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
    monkeypatch.setattr(dem, "TOPODATA_URL", f"http://127.0.0.1:{server.server_port}/{{sheet}}ZN.zip")
    yield served
    server.shutdown()
    server.server_close()


def test_topodata_zip_is_downloaded_once_and_cached(tmp_path, topodata_server):
    cache = tmp_path / "cache"
    win, _ = dem.read_dem(dem.padded(LAB15, 0.02), dem="topodata", cache=cache)
    assert np.nanmax(win.data) > 0 and (cache / "03S555ZN.zip").exists()
    dem.read_dem(dem.padded(LAB15, 0.02), dem="topodata", cache=cache)
    assert len(topodata_server) == 1


def test_topodata_download_failure_is_a_clear_error(tmp_path, topodata_server, monkeypatch):
    monkeypatch.setattr(dem, "TOPODATA_URL", dem.TOPODATA_URL.replace("{sheet}ZN", "{sheet}XX"))
    with pytest.raises(dem.DemError, match="could not download"):
        dem.read_dem(dem.padded(LAB15, 0.02), dem="topodata", cache=tmp_path / "c2")
    assert not list((tmp_path / "c2").glob("*.part"))


def test_download_only_accepts_http():
    with pytest.raises(ValueError, match="http"):
        dem._download("file:///etc/passwd", dem.default_cache_dir())


# --- in a pipeline ---------------------------------------------------------------------------

def _pipeline(tmp_path, source_extra=""):
    path = tmp_path / "dem.toml"
    path.write_text(f"""
schema = 1
[grid]
name = "g"
crs = "EPSG:4326"
bbox = [-54.8415, -3.5865, -54.459, -3.168]
resolution = 0.0045
[[source]]
id = "slope"
type = "dem"
tiles = ["plane.tif"]
product = "slope_deg"
{source_extra}
[[derive]]
target = "media_decl"
source = "slope"
operator = "mean"
""", encoding="utf-8")
    return path


def test_pipeline_derives_the_mean_slope_per_cell(tmp_path, plane_tile):
    report = run(_pipeline(tmp_path), workspace=tmp_path / "ws")
    assert report.derived
    cube = CubeClient(str(tmp_path / "ws" / "catalog.db"), str(tmp_path / "ws" / "store"))
    values = np.asarray(cube.load("media_decl").values, dtype=float)
    assert np.isfinite(values).mean() > 0.99
    assert np.nanmean(values) == pytest.approx(EXPECTED_DEG, abs=0.1)
    prov = json.loads((tmp_path / "ws" / "raw" / "slope.provenance.json").read_text())
    assert prov["type"] == "dem" and prov["product"] == "slope_deg" and prov["unit"] == "degrees"
    assert prov["crs"] == "EPSG:32721" and prov["checksum"].startswith("sha256:")
    assert "central-difference" in prov["processing"] and prov["pipeline"]["file"].endswith("dem.toml")


def test_same_tiles_same_spec_hash_and_a_new_resolution_changes_it(tmp_path, plane_tile):
    def spec_hash(ws):
        cube = CubeClient(str(ws / "catalog.db"), str(ws / "store"))
        return next(d.spec_hash for d in cube.catalog.search_derived_variables() if d.name == "media_decl")

    path = _pipeline(tmp_path)
    run(path, workspace=tmp_path / "a")
    run(path, workspace=tmp_path / "b")
    run(_pipeline(tmp_path, source_extra="resolution = 60"), workspace=tmp_path / "c")
    assert spec_hash(tmp_path / "a") == spec_hash(tmp_path / "b") != spec_hash(tmp_path / "c")


def test_a_missing_tile_file_is_a_pipeline_error_naming_the_source(tmp_path):
    with pytest.raises(PipelineError, match=r"source 'slope'.*no elevation data"):
        run(_pipeline(tmp_path), workspace=tmp_path / "ws")


def test_validation(tmp_path, plane_tile):
    load(_pipeline(tmp_path))  # no network, no tile read
    bad = [
        ('type = "dem"\ntiles = ["plane.tif"]', 'type = "dem"'),                    # neither dem nor tiles
        ('tiles = ["plane.tif"]', 'tiles = ["plane.tif"]\ndem = "srtm"'),           # both
        ('product = "slope_deg"', 'product = "aspect"'),                            # unknown product
        ('tiles = ["plane.tif"]', 'dem = "aster"'),                                 # unknown dem
        ('tiles = ["plane.tif"]', 'tiles = ["plane.tif"]\nmargin = -1'),           # negative margin
        ('tiles = ["plane.tif"]', 'tiles = ["plane.tif"]\nresolution = 0'),        # non-positive resolution
    ]
    text = _pipeline(tmp_path).read_text()
    for old, new in bad:
        broken = tmp_path / "broken.toml"
        broken.write_text(text.replace(old, new), encoding="utf-8")
        with pytest.raises(PipelineError):
            load(broken)
