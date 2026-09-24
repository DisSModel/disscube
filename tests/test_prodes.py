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
