"""
Tests for the Aggregator pipeline stage.

After the operator refactor the Aggregator's contract is:
  - Dispatch to the correct Operator class via OPERATOR_REGISTRY.
  - Accept xr.Dataset (raster path, from GridAligner) or GeoDataFrame (vector).
  - Raise ValueError for unknown operators.

Band selection has moved to GridAligner; see test_aligner.py for those tests.
"""

import geopandas as gpd
import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import box

from disscube.models import GridSpec, SpatialDerivation, SpatialSource, Variable
from disscube.pipeline import PipelineContext
from disscube.pipeline.aggregator import Aggregator


def _grid():
    return GridSpec(
        id="G1", type="local", crs="EPSG:31982", resolution=10, bbox=[0, 0, 100, 100]
    )


def _source(**kwargs):
    return SpatialSource(
        id="S1", name="S1", format="raster", asset_url="test.tif", crs="EPSG:31982",
        **kwargs
    )


def _ctx(grid, variables, data, source=None):
    derivation = SpatialDerivation(
        source_id="S1", grid_id=grid.id, role="test", variables=variables
    )
    return PipelineContext(
        source=source or _source(), grid=grid, derivation=derivation, data=data
    )


# ── Raster path (Dataset from GridAligner) ───────────────────────────────────

def test_aggregator_raster_dataset_passthrough():
    """Aggregator forwards a pre-aligned DataArray from a Dataset."""
    grid = _grid()
    da = xr.DataArray(
        np.ones((10, 10), dtype=np.float32),
        dims=("y", "x"),
        coords={"y": grid.ys, "x": grid.xs},
    )
    ds_in = xr.Dataset({"B1": da})

    ctx = _ctx(grid, [Variable(name="B1", operator="mean")], ds_in)
    result_ctx = Aggregator().execute(ctx)

    assert "B1" in result_ctx.data.data_vars
    assert result_ctx.data["B1"].shape == (10, 10)


def test_aggregator_two_variables_different_operators():
    """Two variables from the same Dataset, different operators."""
    grid = _grid()
    da1 = xr.DataArray(
        np.ones((10, 10), dtype=np.float32),
        dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs},
    )
    da2 = xr.DataArray(
        np.full((10, 10), 2.0, dtype=np.float32),
        dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs},
    )
    ds_in = xr.Dataset({"uso": da1, "alt": da2})

    ctx = _ctx(
        grid,
        [Variable(name="uso", operator="majority"), Variable(name="alt", operator="mean")],
        ds_in,
    )
    result_ctx = Aggregator().execute(ctx)

    assert "uso" in result_ctx.data.data_vars
    assert "alt" in result_ctx.data.data_vars


# ── Vector path (GeoDataFrame) ────────────────────────────────────────────────

def test_aggregator_vector_presence():
    """Presence operator rasterizes a GeoDataFrame to binary mask."""
    grid = _grid()
    gdf = gpd.GeoDataFrame(
        {"geometry": [box(10, 10, 40, 40)]},
        crs="EPSG:31982",
    )
    source = SpatialSource(
        id="S1", name="S1", format="vector",
        asset_url="test.gpkg", crs="EPSG:31982",
    )
    ctx = _ctx(
        grid,
        [Variable(name="pres", operator="presence")],
        gdf,
        source=source,
    )
    result_ctx = Aggregator().execute(ctx)

    assert "pres" in result_ctx.data.data_vars
    arr = result_ctx.data["pres"].values
    assert arr.max() > 0   # at least one pixel is non-zero


# ── Error handling ────────────────────────────────────────────────────────────

def test_aggregator_unknown_operator_raises():
    """Unknown operator raises ValueError with the list of valid operators."""
    grid = _grid()
    da = xr.DataArray(
        np.zeros((10, 10)), dims=("y", "x"),
        coords={"y": grid.ys, "x": grid.xs},
    )
    ds_in = xr.Dataset({"B1": da})

    ctx = _ctx(grid, [Variable(name="B1", operator="nonexistent")], ds_in)

    with pytest.raises(ValueError, match="nonexistent"):
        Aggregator().execute(ctx)
