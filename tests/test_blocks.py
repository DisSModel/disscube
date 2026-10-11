"""
Sources and grids larger than memory: reading, writing and deriving a block at a time must give exactly
what the whole-window path gives.

Offline: a small "national" GeoTIFF (WGS84, uint8) stands in for MapBiomas; PRODES' lookup path is
checked against ``reclassify``.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import mapbiomas, read_window, reclassify, write_geotiff
from disscube.sources._raster import copy_window
from disscube.utils.grids import register_local_grid

BBOX = (-44.35, -2.62, -44.20, -2.47)
RES = 0.00026949458523585647
CLASSES = np.array([0, 3, 15, 24, 33], dtype="uint8")


def _national(path):
    rng = np.random.default_rng(7)
    smooth = rng.integers(0, len(CLASSES), size=(100, 100))
    data = np.kron(smooth, np.ones((10, 10), dtype=int))                # patches of 10 x 10 pixels
    data = CLASSES[data]
    with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES)) as dst:
        dst.write(data, 1)
    return str(path)


def test_copy_window_equals_read_window_and_write(tmp_path):
    url = _national(tmp_path / "br.tif")
    whole = write_geotiff(read_window(url, BBOX, nodata=0), tmp_path / "whole.tif", dtype="uint8", nodata=0)
    blocks, _ = copy_window(url, BBOX, tmp_path / "blocks.tif", nodata=0, out_nodata=0, block_rows=37)
    with rasterio.open(whole) as a, rasterio.open(blocks) as b:
        assert (a.width, a.height, a.transform, a.nodata) == (b.width, b.height, b.transform, b.nodata)
        assert np.array_equal(a.read(1), b.read(1))


def test_copy_window_lookup_equals_reclassify(tmp_path):
    url = _national(tmp_path / "br.tif")
    lookup = {3: 1, 15: 2, 24: 3}                                       # 33 is not in it: becomes nodata
    whole = write_geotiff(reclassify(read_window(url, BBOX), lookup), tmp_path / "whole.tif", dtype="uint8", nodata=0)
    blocks, _ = copy_window(url, BBOX, tmp_path / "blocks.tif", lookup=lookup, out_nodata=0, block_rows=50)
    with rasterio.open(whole) as a, rasterio.open(blocks) as b:
        assert np.array_equal(a.read(1), b.read(1))


def _cube(tmp_path, name, url, resolution):
    cube = CubeClient(catalog=str(tmp_path / f"{name}.db"), store=str(tmp_path / f"{name}_store"))
    grid = register_local_grid(cube, name="g", bbox_geo=BBOX, resolution=resolution)
    mapbiomas.register_mapbiomas_source(cube, "lulc", 2020, BBOX, tmp_path / f"{name}_raw", url=url)
    return cube, grid


@pytest.fixture
def exact_transform(monkeypatch):
    """Reproject with GDAL's exact coordinate transformer instead of its default approximate one.

    The approximate transformer (tolerance 0.125 source pixels) interpolates between points whose
    positions depend on the extent being warped, so a strip and the whole grid can pick a neighbouring
    pixel at a cell edge. With the exact transformer every pixel is mapped independently of the extent.
    """
    from rioxarray.raster_array import RasterArray

    original = RasterArray.reproject

    def reproject(self, *args, **kwargs):
        kwargs.setdefault("tolerance", 0.0)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RasterArray, "reproject", reproject)


@pytest.mark.parametrize("resolution", [500.0, 300.0])
def test_strips_give_the_same_cells_as_the_whole_grid(tmp_path, caplog, resolution, exact_transform):
    url = _national(tmp_path / "br.tif")
    whole, g1 = _cube(tmp_path, "whole", url, resolution)
    strips, g2 = _cube(tmp_path, "strips", url, resolution)
    derivations = [
        Derivation(target="major", source_id="lulc", operator="majority"),
        Derivation(target="forest", source_id="lulc", operator="percentage", class_code=3),
        Derivation(target="pasture", source_id="lulc", operator="percentage", class_code=15),
        Derivation(target="mean", source_id="lulc", operator="mean"),
        Derivation(target="total", source_id="lulc", operator="sum"),
        Derivation(target="med", source_id="lulc", operator="median"),
    ]
    for d in derivations:
        whole.derive_declarative(d, grid_id=g1.id)
        with caplog.at_level(logging.INFO, logger="disscube.client.cube_client"):
            caplog.clear()
            strips.derive_declarative(d, grid_id=g2.id, block_pixels=40_000)   # a window of 1M pixels: ~25 strips
        assert any("strip " in r.message for r in caplog.records), "the strip path was not taken"
        a, b = whole.load(d.target, grid_id=g1.id), strips.load(d.target, grid_id=g2.id)
        assert a.shape == b.shape
        if d.operator in ("mean", "sum"):                # GDAL adds the pixels in a different order: ~1e-12
            assert np.allclose(a.values, b.values, rtol=1e-9, atol=1e-9, equal_nan=True), d.target
            assert np.array_equal(np.isnan(a.values), np.isnan(b.values))
        else:                                            # class shares, majority, median: exactly the same
            assert np.array_equal(a.values, b.values, equal_nan=True), d.target
        assert np.allclose(a["x"].values, b["x"].values) and np.allclose(a["y"].values, b["y"].values)


def test_small_sources_and_neighbourhood_operators_are_not_split(tmp_path, caplog):
    url = _national(tmp_path / "br.tif")
    cube, grid = _cube(tmp_path, "c", url, 500.0)
    with caplog.at_level(logging.INFO, logger="disscube.client.cube_client"):
        cube.derive_declarative(Derivation(target="m", source_id="lulc", operator="mean"), grid_id=grid.id)
    assert not any("strip " in r.message for r in caplog.records)       # default budget: 50 M pixels


def test_strips_with_the_default_transformer_differ_in_at_most_a_few_edge_cells(tmp_path):
    """With GDAL's default (approximate) transformer a strip may pick the neighbouring source pixel at
    a cell edge: a small fraction of the cells, never a different layout."""
    url = _national(tmp_path / "br.tif")
    whole, g1 = _cube(tmp_path, "whole", url, 300.0)
    strips, g2 = _cube(tmp_path, "strips", url, 300.0)
    d = Derivation(target="forest", source_id="lulc", operator="percentage", class_code=3)
    whole.derive_declarative(d, grid_id=g1.id)
    strips.derive_declarative(d, grid_id=g2.id, block_pixels=40_000)
    a, b = whole.load("forest", grid_id=g1.id).values, strips.load("forest", grid_id=g2.id).values
    assert np.array_equal(np.isnan(a), np.isnan(b)) or np.mean(np.isnan(a) != np.isnan(b)) < 0.005
    both = ~np.isnan(a) & ~np.isnan(b)
    assert np.mean(np.abs(a[both] - b[both]) > 1e-9) < 0.01
    assert np.nanmax(np.abs(a - b)) < 0.2
