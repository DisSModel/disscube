"""
Tests for the temporal path: writer time extraction, load() shape consistency,
and to_raster_backend period-filter logging.
"""

import logging

import numpy as np
import pytest
import xarray as xr

from disscube.catalog.sqlite_store import SqliteCatalogStore
from disscube.client.cube_client import CubeClient
from disscube.models import (
    DerivedVariable,
    GridSpec,
    SpatialDerivation,
    SpatialSource,
    Variable,
)
from disscube.pipeline.context import PipelineContext
from disscube.pipeline.writer import VariableWriter
from disscube.storage.local import AssetStore

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _grid():
    return GridSpec(id="G1", type="local", crs="EPSG:31982", resolution=10,
                    bbox=[0, 0, 100, 100])


def _source(**kw):
    return SpatialSource(id="S1", name="S1", format="raster",
                         asset_url="test.tif", crs="EPSG:31982", **kw)


def _ones_da(grid):
    return xr.DataArray(
        np.ones((grid.rows, grid.cols), dtype=np.float32),
        dims=("y", "x"),
        coords={"y": grid.ys, "x": grid.xs},
    )


def _write_zarr(da, path, name="v"):
    da.to_dataset(name=name).to_zarr(path, mode="w", consolidated=False)
    return str(path)


def _register_dv(cube, *, zarr_path, name="v", times, spec_hash="h", uid=None):
    uid = uid or f"{spec_hash}_{name}_{'_'.join(map(str, times))}"
    dv = DerivedVariable(
        id=uid, name=name, grid_id="G1", role="driver",
        times=times, dtype="float32", derivation_id=spec_hash,
        spec_hash=spec_hash, tile_id=None, content_hash=None,
        asset_url=zarr_path,
    )
    cube.catalog.save_derived(dv)
    return dv


# --------------------------------------------------------------------------- #
# VariableWriter: valid_from → DerivedVariable.times
# --------------------------------------------------------------------------- #

def test_writer_year_string_produces_times(tmp_path):
    """valid_from="2005" must produce times=[2005]."""
    catalog = SqliteCatalogStore(tmp_path / "cat.db")
    store = AssetStore(str(tmp_path / "store"))
    grid = _grid()
    derivation = SpatialDerivation(
        source_id="S1", grid_id="G1", role="driver",
        variables=[Variable(name="v", operator="mean")],
        valid_from="2005",
    )
    ctx = PipelineContext(source=_source(), grid=grid, derivation=derivation,
                          data=xr.Dataset({"v": _ones_da(grid)}))
    VariableWriter(store, catalog).execute(ctx)

    derived = catalog.search_derived_variables(grid_id="G1")
    assert len(derived) == 1
    assert derived[0].times == [2005]


def test_writer_iso_date_extracts_year(tmp_path):
    """valid_from="2020-01-01" must produce times=[2020], not times=[]."""
    catalog = SqliteCatalogStore(tmp_path / "cat.db")
    store = AssetStore(str(tmp_path / "store"))
    grid = _grid()
    derivation = SpatialDerivation(
        source_id="S1", grid_id="G1", role="driver",
        variables=[Variable(name="v", operator="mean")],
        valid_from="2020-01-01",
        valid_until="2020-12-31",
    )
    ctx = PipelineContext(source=_source(), grid=grid, derivation=derivation,
                          data=xr.Dataset({"v": _ones_da(grid)}))
    VariableWriter(store, catalog).execute(ctx)

    derived = catalog.search_derived_variables(grid_id="G1")
    assert len(derived) == 1
    assert derived[0].times == [2020], (
        f"Expected [2020] for ISO date valid_from, got {derived[0].times}. "
        "Likely the int() conversion dropped the temporal marker."
    )


def test_writer_static_variable_has_empty_times(tmp_path):
    """A derivation with no valid_from must produce times=[]."""
    catalog = SqliteCatalogStore(tmp_path / "cat.db")
    store = AssetStore(str(tmp_path / "store"))
    grid = _grid()
    derivation = SpatialDerivation(
        source_id="S1", grid_id="G1", role="driver",
        variables=[Variable(name="v", operator="mean")],
    )
    ctx = PipelineContext(source=_source(), grid=grid, derivation=derivation,
                          data=xr.Dataset({"v": _ones_da(grid)}))
    VariableWriter(store, catalog).execute(ctx)

    derived = catalog.search_derived_variables(grid_id="G1")
    assert derived[0].times == []


# --------------------------------------------------------------------------- #
# CubeClient.load(): shape consistency for temporal variables
# --------------------------------------------------------------------------- #

def test_load_single_temporal_slice_returns_3d(tmp_path):
    """load() must return (time, y, x) even when only one temporal slice exists.

    Regression guard: previously returned 2D (y, x) for single-slice variables,
    causing to_raster_backend to treat them as static.
    """
    cube = CubeClient(str(tmp_path / "cat.db"), str(tmp_path / "store"))
    grid = _grid()
    cube.register_grid(grid)

    da = _ones_da(grid)
    zarr_path = _write_zarr(da, tmp_path / "store" / "v_2020.zarr")
    _register_dv(cube, zarr_path=zarr_path, times=[2020])

    result = cube.load("v", grid_id="G1")
    assert result.ndim == 3, (
        f"Expected 3D (time, y, x) for a single temporal slice, got {result.ndim}D. "
        "to_raster_backend would silently treat this as a static variable."
    )
    assert "time" in result.dims
    assert list(result.coords["time"].values) == [2020]


def test_load_multiple_temporal_slices_are_sorted(tmp_path):
    """load() stacks temporal slices in ascending time order."""
    cube = CubeClient(str(tmp_path / "cat.db"), str(tmp_path / "store"))
    grid = _grid()
    cube.register_grid(grid)

    for year, val in [(2010, 3.0), (2000, 1.0), (2005, 2.0)]:
        da = xr.DataArray(
            np.full((grid.rows, grid.cols), val, dtype=np.float32),
            dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs},
        )
        zarr_path = _write_zarr(da, tmp_path / "store" / f"v_{year}.zarr")
        _register_dv(cube, zarr_path=zarr_path, times=[year],
                     spec_hash=f"h{year}", uid=f"h{year}_v")

    result = cube.load("v", grid_id="G1")
    assert result.ndim == 3
    assert list(result.coords["time"].values) == [2000, 2005, 2010]
    # Pixel values should match the time-sorted order
    np.testing.assert_allclose(result.isel(time=0).values, 1.0)
    np.testing.assert_allclose(result.isel(time=1).values, 2.0)
    np.testing.assert_allclose(result.isel(time=2).values, 3.0)


# --------------------------------------------------------------------------- #
# to_raster_backend: period filter emits log.warning (not print)
# --------------------------------------------------------------------------- #

def test_to_raster_backend_period_skip_logs_warning(tmp_path, caplog):
    """A temporal variable outside the requested period logs a warning.

    Verifies the fix from print() to log.warning() so the message is
    observable in structured logging rather than stdout only.
    """
    pytest.importorskip("dissmodel")

    cube = CubeClient(str(tmp_path / "cat.db"), str(tmp_path / "store"))
    grid = _grid()
    cube.register_grid(grid)

    # Temporal variable at 2000 — outside the requested period 2010–2020
    da = _ones_da(grid)
    t_path = _write_zarr(da, tmp_path / "store" / "v_2000.zarr")
    _register_dv(cube, zarr_path=t_path, times=[2000], spec_hash="ht", uid="ht_v")

    # Static variable — always loaded regardless of period
    s_path = _write_zarr(da, tmp_path / "store" / "s.zarr", name="s")
    _register_dv(cube, zarr_path=s_path, name="s", times=[], spec_hash="hs",
                 uid="hs_s")

    with caplog.at_level(logging.WARNING, logger="disscube.client.cube_client"):
        cube.to_raster_backend(["v", "s"], grid_id="G1", period=("2010", "2020"))

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("v" in m for m in warning_messages), (
        "Expected a log.warning mentioning 'v' for the out-of-period variable. "
        f"Got records: {warning_messages}"
    )
