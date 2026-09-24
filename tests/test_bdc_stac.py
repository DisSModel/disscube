"""
Tests for disscube.utils.bdc_stac — reading Brazil Data Cube cubes via STAC.

Everything runs offline: rasters are written to tmp_path in BDC Albers, STAC
items are small stand-ins, and the catalog search is exercised against a local
HTTP server that speaks enough of the STAC API. A real query against the BDC
runs only with DISSCUBE_ONLINE_TESTS=1.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin

from disscube.utils import bdc_stac
from disscube.utils.bdc_stac import (
    Window2D,
    composite,
    is_bdc_albers,
    mosaic,
    normalized_difference,
    portable_crs,
    read_composite,
    read_window,
    tile_of,
    write_geotiff,
)
from disscube.utils.files import sha256_file
from disscube.utils.grids import BDC_CRS

BBOX = (-44.35, -2.62, -44.20, -2.47)
NODATA = -32768
RES = 30.0


def _albers_origin():
    """Upper-left corner (x, y) in BDC Albers of a raster that covers BBOX with margin."""
    to_albers = Transformer.from_crs("EPSG:4326", BDC_CRS, always_xy=True)
    x, y = to_albers.transform(BBOX[0] - 0.05, BBOX[3] + 0.05)
    return round(x / RES) * RES, round(y / RES) * RES


def _write(path, data, *, x0=None, y0=None, scale=None, crs=BDC_CRS, nodata=NODATA):
    ox, oy = _albers_origin()
    x0 = ox if x0 is None else x0
    y0 = oy if y0 is None else y0
    rows, cols = data.shape
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype=data.dtype, crs=crs, transform=from_origin(x0, y0, RES, RES),
                       nodata=nodata) as dst:
        dst.write(data, 1)
        if scale is not None:
            dst.scales = (scale,)
    return str(path)


def _item(item_id, href, *, scale=None, day="2020-07-01", tile=None, asset="NDVI"):
    extra = {"raster:bands": [{"scale": scale, "offset": 0.0}]} if scale is not None else {}
    props = {"bdc:tiles": [tile]} if tile else {}
    return SimpleNamespace(
        id=item_id, datetime=np.datetime64(day), properties=props,
        assets={asset: SimpleNamespace(href=href, extra_fields=extra)},
    )


# ---------------------------------------------------------------------------
# read_window
# ---------------------------------------------------------------------------

def test_read_window_reads_only_the_bbox_and_masks_nodata(tmp_path):
    data = np.full((1000, 1000), 5000, dtype="int16")
    data[:10, :] = NODATA
    href = _write(tmp_path / "ndvi.tif", data)

    w = read_window(href, BBOX)

    assert w.data.dtype == np.float32
    assert 400 < w.data.shape[0] < 1000 and 400 < w.data.shape[1] < 1000  # a window, not the file
    assert np.nanmax(w.data) == 5000  # no scale declared: raw values kept
    with rasterio.open(href) as ds:
        assert ds.crs == CRS.from_string(BDC_CRS)


def test_read_window_applies_file_scale(tmp_path):
    href = _write(tmp_path / "ndvi.tif", np.full((1000, 1000), 5000, dtype="int16"), scale=0.0001)
    w = read_window(href, BBOX)
    assert np.allclose(w.data, 0.5)


def test_read_window_explicit_scale_overrides_file(tmp_path):
    href = _write(tmp_path / "ndvi.tif", np.full((1000, 1000), 5000, dtype="int16"), scale=0.0001)
    w = read_window(href, BBOX, scale=0.001, offset=-1.0)
    assert np.allclose(w.data, 4.0)


def test_nodata_stays_nan_after_scaling(tmp_path):
    data = np.full((1000, 1000), 5000, dtype="int16")
    data[:] = NODATA
    href = _write(tmp_path / "ndvi.tif", data, scale=0.0001)
    assert np.isnan(read_window(href, BBOX).data).all()


# ---------------------------------------------------------------------------
# composite / normalized_difference / mosaic
# ---------------------------------------------------------------------------

def _win(values, x0=0.0):
    return Window2D(data=np.asarray(values, dtype="float32"),
                    transform=from_origin(x0, 0, RES, RES), crs=CRS.from_string(BDC_CRS))


def test_composite_median_ignores_nan():
    out = composite([_win([[1, np.nan]]), _win([[3, 2]]), _win([[np.nan, 4]])], "median")
    assert out.data.tolist() == [[2.0, 3.0]]


def test_composite_all_nan_pixel_stays_nan_without_warning(recwarn):
    out = composite([_win([[np.nan]]), _win([[np.nan]])])
    assert np.isnan(out.data).all()
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_composite_rejects_different_grids_and_reducers():
    with pytest.raises(ValueError, match="different grids"):
        composite([_win([[1]]), _win([[1]], x0=30.0)])
    with pytest.raises(ValueError, match="unknown reducer"):
        composite([_win([[1]])], "mode")
    with pytest.raises(ValueError):
        composite([])


def test_normalized_difference():
    out = normalized_difference(_win([[0.3, 0.1, 0.0]]), _win([[0.1, 0.3, 0.0]]))
    assert np.allclose(out.data[0, :2], [0.5, -0.5])
    assert np.isnan(out.data[0, 2])  # 0/0 → NaN, not inf


def test_mosaic_pastes_adjacent_tiles():
    left = _win([[1, 1], [1, 1]], x0=0.0)
    right = _win([[2, 2], [2, 2]], x0=60.0)
    out = mosaic([left, right])
    assert out.data.shape == (2, 4)
    assert out.data.tolist() == [[1, 1, 2, 2], [1, 1, 2, 2]]
    assert mosaic([left]) is left


# ---------------------------------------------------------------------------
# CRS handling
# ---------------------------------------------------------------------------

def test_bdc_albers_detection_and_portable_crs():
    albers = CRS.from_string(BDC_CRS)
    utm = CRS.from_epsg(31983)
    assert is_bdc_albers(albers)
    assert not is_bdc_albers(utm)
    assert not is_bdc_albers(None)
    assert portable_crs(utm) == utm
    assert portable_crs(albers).to_proj4() == albers.to_proj4()


def test_crs_declared_as_epsg_10857_is_recognised():
    wkt = CRS.from_string(BDC_CRS).to_wkt()[:-1] + ',AUTHORITY["EPSG","10857"]]'
    declared = CRS.from_wkt(wkt)
    assert is_bdc_albers(declared)
    assert "10857" not in portable_crs(declared).to_wkt()


def test_write_geotiff_roundtrip(tmp_path):
    w = _win([[0.1, np.nan], [0.3, 0.4]])
    path = write_geotiff(w, tmp_path / "out" / "x.tif")
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        assert ds.crs == CRS.from_string(BDC_CRS)
        assert np.isnan(ds.nodata)
    assert np.allclose(arr, w.data, equal_nan=True)


# ---------------------------------------------------------------------------
# read_composite with STAC items
# ---------------------------------------------------------------------------

def test_tile_of_uses_property_then_id():
    assert tile_of(SimpleNamespace(id="x", properties={"bdc:tiles": ["016004"]})) == "016004"
    assert tile_of(SimpleNamespace(id="LANDSAT-16D_V1_016004_20200701", properties={})) == "016004"


def test_read_composite_one_tile_uses_item_scale(tmp_path):
    items = [
        _item(f"it{i}", _write(tmp_path / f"t{i}.tif", np.full((1000, 1000), v, dtype="int16")),
              scale=0.0001, tile="016004")
        for i, v in enumerate([2000, 4000, 9000])
    ]
    out = read_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020", items=items)
    assert np.allclose(out.data, 0.4)  # median of 0.2, 0.4, 0.9


def test_read_composite_mosaics_several_tiles(tmp_path):
    ox, _ = _albers_origin()
    half = 500
    west = _write(tmp_path / "w.tif", np.full((1000, half), 1000, dtype="int16"))
    east = _write(tmp_path / "e.tif", np.full((1000, half), 3000, dtype="int16"), x0=ox + half * RES)
    items = [_item("w", west, scale=0.0001, tile="015004"),
             _item("e", east, scale=0.0001, tile="016004")]

    out = read_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020", items=items)

    single = read_window(_write(tmp_path / "full.tif", np.zeros((1000, 1000), "int16")), BBOX)
    assert out.data.shape == single.data.shape  # same footprint as a one-tile read
    assert np.allclose(np.unique(np.round(out.data, 4)), [0.1, 0.3])


def test_read_composite_explicit_scale_overrides_stac(tmp_path):
    items = [_item("a", _write(tmp_path / "a.tif", np.full((1000, 1000), 5000, dtype="int16")),
                   scale=0.001, tile="016004")]
    out = read_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020", items=items, scale=0.0001)
    assert np.allclose(out.data, 0.5)
    no_scale = [_item("b", _write(tmp_path / "b.tif", np.full((1000, 1000), 5000, dtype="int16")),
                      tile="016004")]
    assert np.allclose(read_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020", items=no_scale).data, 5000)


def test_read_composite_without_items_raises(monkeypatch):
    monkeypatch.setattr(bdc_stac, "search_items", lambda *a, **k: [])
    with pytest.raises(ValueError, match="no LANDSAT-16D-1 items"):
        read_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020")


# ---------------------------------------------------------------------------
# search_items against a local STAC API
# ---------------------------------------------------------------------------

@pytest.fixture
def stac_server(tmp_path):
    pytest.importorskip("pystac_client")
    href = _write(tmp_path / "ndvi.tif", np.full((1000, 1000), 5000, dtype="int16"))
    state = {}

    def feature(i):
        return {
            "type": "Feature", "stac_version": "1.0.0", "id": f"LANDSAT-16D_V1_016004_2020070{i}",
            "collection": "LANDSAT-16D-1", "bbox": list(BBOX),
            "geometry": {"type": "Polygon", "coordinates": [[
                [BBOX[0], BBOX[1]], [BBOX[2], BBOX[1]], [BBOX[2], BBOX[3]],
                [BBOX[0], BBOX[3]], [BBOX[0], BBOX[1]]]]},
            "properties": {"datetime": f"2020-07-0{i}T00:00:00Z", "bdc:tiles": ["016004"]},
            "assets": {"NDVI": {"href": href, "type": "image/tiff",
                                "raster:bands": [{"scale": 0.0001, "offset": 0}]}},
            "links": [],
        }

    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            base = state["base"]
            self._json({
                "type": "Catalog", "stac_version": "1.0.0", "id": "bdc", "description": "mock",
                "conformsTo": ["https://api.stacspec.org/v1.0.0/core",
                               "https://api.stacspec.org/v1.0.0/item-search"],
                "links": [{"rel": "self", "href": base}, {"rel": "root", "href": base},
                          {"rel": "search", "href": base + "search", "method": "POST"}],
            })

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["request"] = body
            self._json({"type": "FeatureCollection", "features": [feature(2), feature(1)],
                        "links": []})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["base"] = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()


def test_search_items_and_fetch_composite(stac_server, tmp_path):
    url = stac_server["base"]
    items = bdc_stac.search_items("LANDSAT-16D-1", BBOX, "2020-07-01/2020-09-30", url=url)

    assert [i.id[-2:] for i in items] == ["01", "02"]  # oldest first
    assert stac_server["request"]["collections"] == ["LANDSAT-16D-1"]
    assert stac_server["request"]["bbox"] == list(BBOX)
    assert bdc_stac.asset_scale_offset(items[0], "NDVI") == (0.0001, 0)

    out = bdc_stac.fetch_composite("LANDSAT-16D-1", "NDVI", BBOX, "2020-07-01/2020-09-30",
                                   tmp_path / "ndvi.tif", url=url)
    with rasterio.open(out) as ds:
        assert np.allclose(ds.read(1), 0.5)


# ---------------------------------------------------------------------------
# Real BDC (opt-in)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("DISSCUBE_ONLINE_TESTS") != "1",
                    reason="set DISSCUBE_ONLINE_TESTS=1 to query the real Brazil Data Cube")
def test_real_bdc_landsat_ndvi(tmp_path):
    pytest.importorskip("pystac_client")
    small = (-44.30, -2.56, -44.27, -2.53)
    out = bdc_stac.fetch_composite("LANDSAT-16D-1", "NDVI", small, "2020-07-01/2020-07-31",
                                   tmp_path / "ndvi.tif", scale=bdc_stac.BDC_INDEX_SCALE)
    with rasterio.open(out) as ds:
        data = ds.read(1)
    valid = data[np.isfinite(data)]
    assert valid.size > 0
    assert -1.0 <= valid.min() and valid.max() <= 1.0  # physical NDVI, scale applied


# ---------------------------------------------------------------------------
# Registration with provenance
# ---------------------------------------------------------------------------

def _cube(tmp_path):
    from disscube import CubeClient
    from disscube.utils.grids import register_local_grid

    cube = CubeClient(catalog=str(tmp_path / "catalog.db"), store=str(tmp_path / "store"))
    grid = register_local_grid(cube, name="ilha", bbox_geo=BBOX, resolution=300.0)
    return cube, grid


def test_period_year():
    assert bdc_stac.period_year("2020-07-01/2020-09-30") == 2020
    assert bdc_stac.period_year("2019") == 2019
    assert bdc_stac.period_year("") is None


def test_register_bdc_source_writes_file_checksum_and_provenance(tmp_path):
    cube, _ = _cube(tmp_path)
    items = [_item(f"LANDSAT-16D_V1_016004_2020070{i}", _write(tmp_path / f"t{i}.tif",
                   np.full((1000, 1000), v, dtype="int16")), tile="016004")
             for i, v in enumerate([2000, 4000, 9000])]

    src = bdc_stac.register_bdc_source(cube, "ndvi", "LANDSAT-16D-1", "NDVI", BBOX,
                                       "2020-07-01/2020-09-30", tmp_path / "raw",
                                       items=items, scale=bdc_stac.BDC_INDEX_SCALE)

    tif = tmp_path / "raw" / "ndvi.tif"
    prov = json.loads((tmp_path / "raw" / "ndvi.provenance.json").read_text())
    assert src.asset_url == str(tif) and src.checksum == sha256_file(tif)
    assert src.time == 2020 and "collection:LANDSAT-16D-1" in src.tags
    assert cube.catalog.get_spatial_source("ndvi").checksum == src.checksum
    assert prov["checksum"] == src.checksum
    assert prov["collection"] == "LANDSAT-16D-1" and prov["asset"] == "NDVI"
    assert prov["period"] == "2020-07-01/2020-09-30" and prov["reducer"] == "median"
    assert prov["scale"] == 1e-4
    assert [i["id"][-2:] for i in prov["items"]] == ["00", "01", "02"]
    assert all(i["href"].endswith(".tif") for i in prov["items"])
    with rasterio.open(tif) as ds:
        assert np.allclose(ds.read(1), 0.4)


def test_new_composite_is_recomputed_downstream(tmp_path):
    from disscube import Derivation

    cube, grid = _cube(tmp_path)
    d = Derivation(target="ndvi_mean", source_id="ndvi", operator="mean")

    def run(value, period):
        items = [_item("LANDSAT-16D_V1_016004_20200701",
                       _write(tmp_path / f"{value}.tif", np.full((1000, 1000), value, dtype="int16")),
                       tile="016004")]
        bdc_stac.register_bdc_source(cube, "ndvi", "LANDSAT-16D-1", "NDVI", BBOX, period,
                                     tmp_path / "raw", items=items, scale=1e-4)
        return cube.derive_declarative(d, grid_id=grid.id)[0]

    dry = run(3000, "2020-07-01/2020-09-30")
    wet = run(7000, "2021-01-01/2021-03-31")
    assert wet.spec_hash != dry.spec_hash
    import xarray as xr
    value = float(xr.open_zarr(wet.asset_url, consolidated=False)["ndvi_mean"].mean())
    assert value == pytest.approx(0.7, abs=1e-4)
