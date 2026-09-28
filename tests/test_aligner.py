"""
Tests for GridAligner — band selection, per-variable resampling, and the
alignment invariant.

GridAligner takes a raster URL and returns an xr.Dataset keyed by variable
name, each band resampled with the correct method for its operator.
"""

import numpy as np
import pytest
import rioxarray  # noqa: F401

from disscube.models import GridSpec, SpatialDerivation, SpatialSource, Variable
from disscube.pipeline import PipelineContext
from disscube.pipeline.aligner import GridAligner


def _grid(**kw):
    return GridSpec(id="G1", type="local", crs="EPSG:31982", resolution=10, bbox=[0, 0, 100, 100], **kw)


def _raster_source(asset_url="test.tif", **kw):
    return SpatialSource(id="S1", name="S1", format="raster", asset_url=asset_url, crs="EPSG:31982", **kw)


def _ctx(grid, variables, source=None):
    derivation = SpatialDerivation(source_id="S1", grid_id=grid.id, role="test", variables=variables)
    return PipelineContext(source=source or _raster_source(), grid=grid, derivation=derivation)


# ── band_map out-of-range ─────────────────────────────────────────────────────

def test_band_map_out_of_range(tmp_path):
    """band_map pointing beyond available bands raises ValueError."""
    import rasterio
    from rasterio.transform import from_bounds

    tif = tmp_path / "two_band.tif"
    transform = from_bounds(0, 0, 100, 100, 10, 10)
    with rasterio.open(
        tif, "w", driver="GTiff", height=10, width=10, count=2,
        dtype="float32", crs="EPSG:31982", transform=transform,
    ) as dst:
        dst.write(np.ones((2, 10, 10), dtype=np.float32))

    grid = _grid()
    source = _raster_source(asset_url=str(tif), band_map={"B1": 3})
    ctx = _ctx(grid, [Variable(name="B1", operator="mean")], source)

    with pytest.raises(ValueError, match="Band index 3.*out of range"):
        GridAligner().execute(ctx)


# ── per-variable resampling ───────────────────────────────────────────────────

def test_per_variable_resampling_produces_correct_names(tmp_path):
    """Each variable in a multi-variable derivation gets its own DataArray."""
    import rasterio
    from rasterio.transform import from_bounds

    tif = tmp_path / "two_band.tif"
    transform = from_bounds(0, 0, 100, 100, 10, 10)
    with rasterio.open(
        tif, "w", driver="GTiff", height=10, width=10, count=2,
        dtype="float32", crs="EPSG:31982", transform=transform,
    ) as dst:
        dst.write(np.ones((2, 10, 10), dtype=np.float32))

    grid = _grid()
    source = _raster_source(asset_url=str(tif))
    ctx = _ctx(
        grid,
        [Variable(name="uso", operator="majority"), Variable(name="alt", operator="mean")],
        source,
    )

    GridAligner().execute(ctx)
    ds = ctx.data

    # GridAligner now returns a dict keyed by variable name. Continuous
    # operators (mean) are aligned to the target grid; categorical ones
    # (majority) are returned at a fine, origin-snapped resolution and
    # reduced later by the operator, so only the continuous one is asserted
    # to match the target shape here.
    assert isinstance(ds, dict)
    assert "uso" in ds
    assert "alt" in ds
    assert ds["alt"].shape == (10, 10)


# ── alignment invariant ───────────────────────────────────────────────────────

def test_alignment_invariant_passes_on_correct_shape(tmp_path):
    """A well-formed raster passes the shape invariant without error."""
    import rasterio
    from rasterio.transform import from_bounds

    tif = tmp_path / "ok.tif"
    transform = from_bounds(0, 0, 100, 100, 10, 10)
    with rasterio.open(
        tif, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs="EPSG:31982", transform=transform,
    ) as dst:
        dst.write(np.zeros((1, 10, 10), dtype=np.float32))

    grid = _grid()
    source = _raster_source(asset_url=str(tif))
    ctx = _ctx(grid, [Variable(name="v", operator="mean")], source)

    GridAligner().execute(ctx)  # must not raise
    assert ctx.data["v"].shape == (10, 10)
