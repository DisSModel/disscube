"""
Defensive fallbacks around third-party accessors.

These handlers are deliberately broad (``except Exception``) because the
rioxarray ``.rio`` accessor raises undocumented exception types — for
example ``ValueError`` for a malformed ``_FillValue`` and pyproj's
``CRSError`` for a malformed ``crs_wkt``. The tests below pin down that the
fallback is taken instead of the error propagating.
"""

from unittest.mock import patch

import numpy as np
import pytest
import rioxarray  # noqa: F401 — registers the .rio accessor
import xarray as xr
from rioxarray.raster_array import RasterArray

from disscube.client import CubeClient
from disscube.models import GridSpec
from disscube.operators.zonal import _fine_array
from disscube.pipeline.aligner import GridAligner


def _raster(n: int = 8, res: float = 50.0) -> xr.DataArray:
    coords = {
        "y": np.arange(n)[::-1] * res + res / 2,
        "x": np.arange(n) * res + res / 2,
    }
    da = xr.DataArray(np.arange(n * n, dtype="float64").reshape(n, n), dims=("y", "x"), coords=coords)
    return da.rio.write_crs("EPSG:31983")


def test_fine_array_falls_back_when_nodata_is_unreadable():
    da = _raster()
    da.attrs["_FillValue"] = "not-a-number"
    with pytest.raises(ValueError):
        _ = da.rio.nodata  # the accessor itself fails ...

    arr, nodata = _fine_array(da)  # ... but the helper falls back

    assert nodata is None
    assert arr.shape == (8, 8)


def test_align_fine_falls_back_to_grid_resolution():
    grid = GridSpec(id="g", type="local", crs="EPSG:31983", resolution=100, bbox=[0, 0, 400, 400])
    band = _raster()

    def boom(*args, **kwargs):
        raise ValueError("boom")

    # GridAligner estimates the source resolution with calculate_default_transform.
    with patch("rasterio.warp.calculate_default_transform", boom):
        aligned = GridAligner()._align_fine(band, grid)

    # With src_res == grid.resolution the fine factor is 1: one pixel per cell.
    assert aligned.shape == (grid.rows, grid.cols)


def test_to_lucc_data_ignores_malformed_spatial_ref(tmp_path):
    da = _raster(n=4).drop_vars("spatial_ref").assign_coords(spatial_ref=0)
    da.spatial_ref.attrs["crs_wkt"] = "not a WKT string"
    cube = CubeClient(str(tmp_path / "catalog.db"), str(tmp_path / "store"))

    with patch.object(CubeClient, "load", return_value=da):
        backend = cube.to_lucc_data(["v"], grid_id="g")

    assert backend.crs is None
    np.testing.assert_array_equal(backend.get("v"), da.values)
