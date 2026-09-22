"""
Regression tests: nodata handling during raster alignment.

The scene is a 10x10 raster at 100 m; the target grid has 500 m cells and one
extra column to the east that the raster does not cover:

    source (10x10)              target grid (2 rows x 3 cols)
    +----------+----------+     +------+------+------+
    | left half| right    |     | left | right| out  |
    |          | half     |     |      |      | side |
    +----------+----------+     +------+------+------+

Before the fix, a uint8 source without a declared nodata was reprojected with
the dtype's default fill value (255), which then:
  - on the categorical path, was treated as nodata, dropping a real class 255;
  - on the continuous path, collided with real 255 values (GDAL nudged them
    to 254), and `sum` saturated because it was computed in uint8.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

CRS = "EPSG:31983"


def _write(path, array, nodata=None):
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1, dtype=array.dtype,
        crs=CRS, transform=from_origin(0, 1000, 100, 100), nodata=nodata,
    ) as dst:
        dst.write(array, 1)
    return str(path)


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(str(tmp_path / "catalog.db"), str(tmp_path / "store"))
    c.register_grid(GridSpec(id="g", type="local", crs=CRS, resolution=500.0, bbox=[0, 0, 1500, 1000]))
    return c


def _derive(cube, tmp_path, array, operator, class_code=None, nodata=None):
    url = _write(tmp_path / f"src_{operator}_{class_code}.tif", array, nodata)
    sid = f"src_{operator}_{class_code}_{nodata}"
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="raster", asset_url=url, crs=CRS))
    cube.derive(SpatialDerivation(
        source_id=sid, grid_id="g", role="test",
        variables=[Variable(name=sid, operator=operator, class_code=class_code)],
    ))
    return cube.load(sid, grid_id="g")


def _halves(left, right, dtype="uint8"):
    a = np.full((10, 10), right, dtype=dtype)
    a[:, :5] = left
    return a


# ── Categorical path: class 255 without a declared nodata ─────────────────────

def test_class_255_is_kept_without_declared_nodata(cube, tmp_path):
    da = _derive(cube, tmp_path, _halves(255, 7), "percentage", class_code=255)
    np.testing.assert_allclose(da.values[:, :2], [[1.0, 0.0], [1.0, 0.0]])
    assert np.isnan(da.values[:, 2]).all()  # outside the source
    np.testing.assert_allclose(da.coords["coverage_purity"].values, [[1, 1, 0], [1, 1, 0]])


def test_majority_can_be_class_255(cube, tmp_path):
    da = _derive(cube, tmp_path, _halves(255, 7), "majority")
    np.testing.assert_array_equal(da.values[:, :2], [[255, 7], [255, 7]])


# ── Continuous path: no nudging, no saturation ────────────────────────────────

@pytest.mark.parametrize("operator", ["mean", "max", "min"])
def test_value_255_is_not_nudged(cube, tmp_path, operator):
    da = _derive(cube, tmp_path, _halves(255, 100), operator)
    np.testing.assert_allclose(da.values[:, :2], [[255, 100], [255, 100]])
    assert np.isnan(da.values[:, 2]).all()


def test_integer_sum_does_not_saturate(cube, tmp_path):
    da = _derive(cube, tmp_path, _halves(255, 100), "sum")
    # 25 source pixels per target cell
    np.testing.assert_allclose(da.values[:, :2], [[25 * 255, 25 * 100], [25 * 255, 25 * 100]])


# ── A declared nodata is still excluded ───────────────────────────────────────

def test_declared_nodata_is_excluded_categorical(cube, tmp_path):
    da = _derive(cube, tmp_path, _halves(0, 7), "percentage", class_code=7, nodata=0)
    assert np.isnan(da.values[0, 0])
    assert da.values[0, 1] == pytest.approx(1.0)
    np.testing.assert_allclose(da.coords["coverage_purity"].values[0], [0, 1, 0])


def test_declared_nodata_is_excluded_continuous(cube, tmp_path):
    da = _derive(cube, tmp_path, _halves(0, 7), "mean", nodata=0)
    assert np.isnan(da.values[0, 0])
    assert da.values[0, 1] == pytest.approx(7.0)
