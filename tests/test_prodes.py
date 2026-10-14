"""
Tests for disscube.sources.prodes — PRODES deforestation maps.

Offline: a small ZIP shaped like the TerraBrasilis edition (one GeoTIFF with
PRODES codes and a QGIS .qml legend) is served by a local HTTP server, so the
download, the cache, the legend parsing and the per-year reclassification are
all exercised without the real 130 MB file.
"""

from __future__ import annotations

import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import prodes
from disscube.sources._categorical import read_qml_legend, strip_code
from disscube.utils.grids import register_local_grid

BBOX = (-54.84, -3.59, -54.46, -3.17)
RES = 0.00026949458523585647
EDITION = "prodes_amazonia_legal_2025_v20260408"

LEGEND = {7: "7 d2007", 12: "12 d2012", 20: "20 d2020", 55: "55 r2015", 91: "91 Hidrografia",
          100: "100 Vegetação nativa florestal", 101: "101 Vegetação nativa não florestal"}
QML = ('<!DOCTYPE qgis><qgis><pipe><rasterrenderer><colorPalette>'
       + "".join(f'<paletteEntry value="{k}" label="{v}" color="#00ff00" alpha="255"/>'
                 for k, v in LEGEND.items())
       + "</colorPalette></rasterrenderer></pipe></qgis>")


def _make_zip(tmp_path):
    """1800 × 1800 px: vertical bands of forest, d2007, d2012, d2020, r2015, non-forest, water."""
    data = np.full((1800, 1800), 100, dtype="uint8")
    for i, code in enumerate([7, 12, 20, 55, 101, 91]):
        data[:, 200 + i * 200: 400 + i * 200] = code
    tif = tmp_path / f"{EDITION}.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=1800, width=1800, count=1, dtype="uint8",
                       crs="EPSG:4674", transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES),
                       nodata=255) as dst:
        dst.write(data, 1)
    zpath = tmp_path / "srv" / f"{EDITION}.zip"
    zpath.parent.mkdir()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tif, f"{EDITION}.tif")
        zf.writestr(f"{EDITION}.qml", QML)
        zf.writestr("readme.txt", "PRODES")
    return zpath


@pytest.fixture
def server(tmp_path):
    zpath = _make_zip(tmp_path)
    state = {"hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("/download/all"):
                body = json.dumps([
                    {"name": "old", "link": "/download/dataset/legal-amz-prodes/raster/prodes_amazonia_legal_2023.zip"},
                    {"name": "new", "link": f"/download/dataset/legal-amz-prodes/raster/{EDITION}.zip"},
                    {"name": "cerrado", "link": "/download/dataset/cerrado-prodes/raster/x_2025.zip"},
                ]).encode()
            else:
                state["hits"] += 1
                body = zpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["base"] = f"http://127.0.0.1:{srv.server_address[1]}"
    state["url"] = f"{state['base']}/download/dataset/legal-amz-prodes/raster/{EDITION}.zip"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield state
    srv.shutdown()


def test_legend_parsing():
    legend = read_qml_legend(QML)
    assert legend == LEGEND
    assert strip_code("7 d2007") == "d2007"
    scheme = prodes.parse_legend(legend)
    assert scheme.deforestation == {2007: 7, 2012: 12, 2020: 20}
    assert scheme.residual == {2015: 55}
    assert scheme.forest == {100} and scheme.non_forest == {101} and scheme.water == {91}
    assert scheme.first_year == 2007


def test_lookup_keeps_the_yearly_class_when_a_residual_class_has_the_same_year():
    """In the real legend every year from 2010 has both dYYYY (code 10 to 25) and rYYYY (50 to 65)."""
    legend = {9: "9 d2009", 10: "10 d2010", 15: "15 d2015", 24: "24 d2024", 25: "25 d2025", 50: "50 r2010",
              55: "55 r2015", 64: "64 r2024", 100: "100 Vegetação nativa florestal"}
    scheme = prodes.parse_legend(legend)
    t = scheme.lookup(2015)
    assert t[10] == t[50] == t[15] == t[55] == prodes.DEFORESTED
    assert t[24] == t[64] == t[25] == prodes.FOREST                  # not yet cleared in 2015
    assert set(legend) - {100} <= set(t)                              # no class is left out (it would be nodata)
    assert scheme.lookup(2024)[24] == scheme.lookup(2024)[64] == prodes.DEFORESTED


def test_lookup_by_year():
    scheme = prodes.parse_legend(LEGEND)
    t2012 = scheme.lookup(2012)
    assert t2012[7] == t2012[12] == prodes.DEFORESTED
    assert t2012[20] == t2012[55] == t2012[100] == prodes.FOREST     # not yet cleared
    assert t2012[101] == t2012[91] == prodes.OTHER
    assert scheme.lookup(2016)[55] == prodes.DEFORESTED               # residual counts from its year
    with pytest.raises(ValueError, match="from 2007 on"):
        scheme.lookup(2004)


def test_download_caches_and_unpacks(server, tmp_path):
    files = prodes.download(server["url"], cache_dir=tmp_path / "cache")
    again = prodes.download(server["url"], cache_dir=tmp_path / "cache")
    assert server["hits"] == 1                                        # second call used the cache
    assert files.tif.name == f"{EDITION}.tif" and files.qml.name == f"{EDITION}.qml"
    assert files.zip_checksum == again.zip_checksum and files.zip_checksum.startswith("sha256:")
    assert not list((tmp_path / "cache").glob("*.part"))


def test_latest_url_picks_newest_legal_amazon_raster(server):
    url = prodes.latest_url(f"{server['base']}/business/api/v1/download/all")
    assert url == server["url"]


def test_register_prodes_source_and_grid(server, tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    grid = register_local_grid(cube, name="lab15", bbox_geo=BBOX, resolution=500.0)
    files = prodes.download(server["url"], cache_dir=tmp_path / "cache")

    shares = {}
    for year in (2008, 2016, 2024):
        src = prodes.register_prodes_source(cube, f"prodes_{year}", year, BBOX, tmp_path / "raw",
                                            files=files)
        with rasterio.open(src.asset_url) as ds:
            assert ds.dtypes[0] == "uint8" and ds.nodata == 0
            values = set(np.unique(ds.read(1)).tolist())
        assert values <= {0, prodes.FOREST, prodes.DEFORESTED, prodes.OTHER}
        cube.derive_declarative(Derivation(target="deforested_pct", source_id=f"prodes_{year}",
                                           operator="percentage", class_code=prodes.DEFORESTED),
                                grid_id=grid.id)
        shares[year] = src

    series = cube.load("deforested_pct", grid_id=grid.id)
    means = [float(np.nanmean(series.sel(time=y))) for y in (2008, 2016, 2024)]
    assert means[0] < means[1] < means[2]                              # d2007 → + d2012, r2015 → + d2020

    prov = json.loads((tmp_path / "raw" / "prodes_2016.provenance.json").read_text())
    assert prov["edition"] == EDITION and prov["url"] == server["url"]
    assert prov["zip_checksum"] == files.zip_checksum and prov["first_separable_year"] == 2007
    assert prov["legend"]["55"] == "55 r2015"


def test_register_before_first_year_fails(server, tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    files = prodes.download(server["url"], cache_dir=tmp_path / "cache")
    with pytest.raises(ValueError, match="use MapBiomas"):
        prodes.register_prodes_source(cube, "p", 2004, BBOX, tmp_path / "raw", files=files)


def test_codes_choose_the_local_code_of_each_category(server, tmp_path):
    scheme = prodes.parse_legend(LEGEND)
    assert scheme.lookup(2012)[91] == scheme.lookup(2012)[101] == 3   # default: water and non-forest together
    t = scheme.lookup(2012, {"water": 4})
    assert t[91] == 4 and t[101] == 3 and t[100] == prodes.FOREST
    merged = scheme.lookup(2012, {"forest": 7, "non_forest": 7, "water": 7})
    assert merged[100] == merged[101] == merged[91] == 7 and merged[7] == prodes.DEFORESTED
    with pytest.raises(ValueError, match="unknown PRODES categories"):
        prodes.resolve_codes({"cloud": 9})
    for bad in (0, 256, 2.5, True):
        with pytest.raises(ValueError, match="1 to 255"):
            prodes.resolve_codes({"water": bad})

    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    files = prodes.download(server["url"], cache_dir=tmp_path / "cache")
    plain = prodes.register_prodes_source(cube, "p_plain", 2016, BBOX, tmp_path / "raw", files=files)
    apart = prodes.register_prodes_source(cube, "p_water", 2016, BBOX, tmp_path / "raw", files=files,
                                          codes={"water": 4})
    with rasterio.open(plain.asset_url) as a, rasterio.open(apart.asset_url) as b:
        va, vb = a.read(1), b.read(1)
    assert 4 not in np.unique(va) and 4 in np.unique(vb)
    assert np.array_equal(np.where(vb == 4, 3, vb), va)               # only the water pixels change
    prov = json.loads((tmp_path / "raw" / "p_water.provenance.json").read_text())
    assert prov["codes"] == {"forest": 1, "deforested": 2, "non_forest": 3, "water": 4, "nodata": 0}


def test_codes_in_a_pipeline_file(tmp_path):
    from pydantic import ValidationError

    from disscube.pipeline.schema import ProdesSource

    assert ProdesSource(id="p", type="prodes", codes={"water": 4}).codes == {"water": 4}
    with pytest.raises(ValidationError, match="unknown PRODES categories"):
        ProdesSource(id="p", type="prodes", codes={"agua": 4})
