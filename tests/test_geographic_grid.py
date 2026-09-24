"""
Regression: derivations on a geographic grid with a non-binary cell size.

A raster that already sits on a SAD69 grid of 0.0045° cells (the LuccME Lab15
cellular space) must come back identical after ``mean``/``max``/``majority``.
Before the fix, about a quarter of the rows came back empty: the aligned
array's coordinates, computed by rioxarray, differed from GridSpec's by
floating-point noise, and assigning it into the grid-indexed Dataset aligned
by label, turning those rows into NaN.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation, GridSpec, SpatialSource

CRS = "EPSG:4618"
RES = 0.0045
BBOX = [-54.8415, -3.5865, -54.459, -3.168]          # 85 × 93 cells
ROWS, COLS = 93, 85


@pytest.fixture
def setup(tmp_path):
    rng = np.random.default_rng(0)
    cont = rng.random((ROWS, COLS)).astype("float32") * 20
    cls = rng.integers(1, 4, (ROWS, COLS)).astype("uint8")
    for name, arr, dtype in (("cont", cont, "float32"), ("cls", cls, "uint8")):
        with rasterio.open(tmp_path / f"{name}.tif", "w", driver="GTiff", height=ROWS, width=COLS, count=1,
                           dtype=dtype, crs=CRS, transform=from_origin(BBOX[0], BBOX[3], RES, RES)) as dst:
            dst.write(arr, 1)
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    cube.register_grid(GridSpec(id="g", type="local", crs=CRS, resolution=RES, bbox=BBOX))
    for name in ("cont", "cls"):
        cube.register_spatial_source(SpatialSource(id=name, name=name, format="raster", crs=CRS,
                                                   asset_url=str(tmp_path / f"{name}.tif")))
    return cube, cont, cls


@pytest.mark.parametrize("operator", ["mean", "max"])
def test_continuous_operators_keep_every_row(setup, operator):
    cube, cont, _ = setup
    cube.derive_declarative(Derivation(target=f"v_{operator}", source_id="cont", operator=operator), grid_id="g")
    got = cube.load(f"v_{operator}", grid_id="g").values
    assert not np.isnan(got).any()
    assert np.allclose(got, cont)


def test_categorical_operator_keeps_every_row(setup):
    cube, _, cls = setup
    cube.derive_declarative(Derivation(target="major", source_id="cls", operator="majority"), grid_id="g")
    got = cube.load("major", grid_id="g").values
    assert not np.isnan(got).any()
    assert np.array_equal(got.astype(int), cls.astype(int))


def test_output_coordinates_are_the_grid_coordinates(setup):
    cube, _, _ = setup
    cube.derive_declarative(Derivation(target="m", source_id="cont", operator="mean"), grid_id="g")
    da = cube.load("m", grid_id="g")
    grid = GridSpec(id="g", type="local", crs=CRS, resolution=RES, bbox=BBOX)
    assert np.array_equal(da["y"].values, grid.ys) and np.array_equal(da["x"].values, grid.xs)
