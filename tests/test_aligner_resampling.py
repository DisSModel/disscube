"""
Complementary GridAligner tests.

These extend ``tests/test_aligner.py`` (which covers band selection, per-variable
naming and the happy-path shape invariant) with the cases that matter most for
scientific correctness:

* downsampling to a resolution that IS an integer multiple of the source;
* downsampling to a resolution that is NOT an integer multiple (e.g. 10 m -> 30 m
  over a 100 m extent: the regression guard for the alignment concern raised in the
  architecture audit, analogous to the real-world 30 m -> 1000 m case);
* numeric correctness of ``mean`` (Resampling.average) and ``majority``
  (Resampling.mode) on hand-crafted rasters;
* vector reprojection + clip to the grid bbox.

All inputs are generated in-memory / in ``tmp_path``; no external files are used.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
import xarray as xr
import rioxarray  # noqa: F401 — registers the .rio accessor

from disscube.pipeline.aligner import GridAligner
from disscube.pipeline import PipelineContext
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable

CRS = "EPSG:31982"  # UTM 22S, metres — same as existing aligner tests


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _grid(resolution, bbox=(0, 0, 100, 100), gid="G1"):
    return GridSpec(id=gid, type="local", crs=CRS, resolution=resolution, bbox=list(bbox))


def _raster_source(asset_url, **kw):
    return SpatialSource(id="S1", name="S1", format="raster", asset_url=asset_url, crs=CRS, **kw)


def _vector_source(**kw):
    return SpatialSource(id="V1", name="V1", format="vector", asset_url="mem://vec", crs=CRS, **kw)


def _ctx(grid, variables, source, data=None):
    deriv = SpatialDerivation(source_id=source.id, grid_id=grid.id, role="test", variables=variables)
    return PipelineContext(source=source, grid=grid, derivation=deriv, data=data)


def _write_raster(path, array, bbox=(0, 0, 100, 100), crs=CRS):
    """Write a single-band float32 GeoTIFF from a (rows, cols) array."""
    rows, cols = array.shape
    transform = from_bounds(*bbox, cols, rows)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=crs, transform=transform,
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    return str(path)


# --------------------------------------------------------------------------- #
# Resolution: integer-multiple downsampling
# --------------------------------------------------------------------------- #

def test_downsample_multiple_shape(tmp_path):
    """10 m source over a 100 m extent -> 20 m grid: clean 5x5 result."""
    src = np.arange(100, dtype=np.float32).reshape(10, 10)  # 10 m, 10x10
    url = _write_raster(tmp_path / "fine.tif", src)

    grid = _grid(resolution=20)  # 100/20 = 5
    ctx = _ctx(grid, [Variable(name="v", operator="mean")], _raster_source(url))

    GridAligner().execute(ctx)
    assert ctx.data["v"].shape == (grid.rows, grid.cols) == (5, 5)


def test_mean_value_is_block_average(tmp_path):
    """mean -> Resampling.average must average the source block, not sample it.

    Source is a 4x4 grid of distinct values; aligning to a 2x2 grid (50 m over a
    100 m extent) must yield the mean of each 2x2 block.
    """
    src = np.array(
        [
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [3, 3, 4, 4],
            [3, 3, 4, 4],
        ],
        dtype=np.float32,
    )  # 25 m pixels over 0..100
    url = _write_raster(tmp_path / "blocks.tif", src)

    grid = _grid(resolution=50)  # 2x2 result, each cell == one 2x2 block
    ctx = _ctx(grid, [Variable(name="v", operator="mean")], _raster_source(url))

    GridAligner().execute(ctx)
    out = ctx.data["v"].values
    assert out.shape == (2, 2)
    # Each block is constant, so the average equals that constant.
    np.testing.assert_allclose(np.sort(out.ravel()), [1, 2, 3, 4], rtol=0, atol=1e-5)


def test_majority_value_is_mode(tmp_path):
    """majority must return the dominant class per target cell.

    After the operator refactor, categorical aggregation is performed by the
    operator's compute() (fine align + window reduction), NOT by the aligner.
    So this exercises the full aligner -> aggregator path.
    """
    from disscube.pipeline.aggregator import Aggregator

    src = np.array(
        [
            [7, 7, 5, 5],
            [7, 9, 5, 5],
            [2, 2, 8, 8],
            [2, 2, 8, 6],
        ],
        dtype=np.float32,
    )
    url = _write_raster(tmp_path / "cls.tif", src)

    grid = _grid(resolution=50)  # 2x2 blocks
    ctx = _ctx(grid, [Variable(name="uso", operator="majority")], _raster_source(url))

    GridAligner().execute(ctx)
    Aggregator().execute(ctx)
    out = ctx.data["uso"].values

    assert out.shape == (2, 2)
    assert out[0, 0] == 7
    assert out[0, 1] == 5
    assert out[1, 0] == 2
    assert out[1, 1] == 8


# --------------------------------------------------------------------------- #
# Resolution: NON-multiple downsampling (the key regression guard)
# --------------------------------------------------------------------------- #

def test_downsample_non_multiple_does_not_crash(tmp_path):
    """10 m source, 30 m grid over a 100 m extent (100/30 is not integer).

    rows/cols round to 3 (round(100/30)=3). The aligner must produce exactly the
    grid shape without raising, proving alignment is driven by the target
    transform/shape rather than a naive reshape that would require divisibility.
    """
    src = (np.arange(100, dtype=np.float32)).reshape(10, 10)
    url = _write_raster(tmp_path / "fine.tif", src)

    grid = _grid(resolution=30)
    assert (grid.rows, grid.cols) == (3, 3)  # round(100/30) == 3

    ctx = _ctx(grid, [Variable(name="v", operator="mean")], _raster_source(url))
    GridAligner().execute(ctx)

    out = ctx.data["v"]
    assert out.shape == (grid.rows, grid.cols) == (3, 3)
    # Values must be finite and within the source value range.
    vals = out.values
    assert np.all(np.isfinite(vals))
    assert vals.min() >= src.min() - 1e-6
    assert vals.max() <= src.max() + 1e-6


def test_non_multiple_coords_match_gridspec(tmp_path):
    """Output coordinates must equal GridSpec.xs / GridSpec.ys exactly."""
    src = np.ones((10, 10), dtype=np.float32)
    url = _write_raster(tmp_path / "ones.tif", src)

    grid = _grid(resolution=30)
    ctx = _ctx(grid, [Variable(name="v", operator="mean")], _raster_source(url))
    GridAligner().execute(ctx)

    out = ctx.data["v"]
    np.testing.assert_allclose(out["x"].values, grid.xs)
    np.testing.assert_allclose(out["y"].values, grid.ys)


# --------------------------------------------------------------------------- #
# Alignment invariant fires on a genuine mismatch
# --------------------------------------------------------------------------- #

def test_alignment_invariant_raises_on_shape_mismatch(tmp_path, monkeypatch):
    """If reprojection yields the wrong shape, the invariant must raise ValueError.

    We force the failure by monkeypatching the .rio.reproject result to a wrong
    shape, isolating the invariant check itself.

    The source is deliberately at a different resolution (5m) than the grid
    (10m) so GridAligner's identity fast path (same CRS/resolution/origin ->
    plain windowed read, no reproject) does not intercept — this test only
    means to exercise the general reproject path's shape invariant.
    """
    src = np.zeros((20, 20), dtype=np.float32)  # 100/20 = 5m pixels, vs grid's 10m
    url = _write_raster(tmp_path / "z.tif", src)

    grid = _grid(resolution=10)  # expects 10x10
    ctx = _ctx(grid, [Variable(name="v", operator="mean")], _raster_source(url))

    real_open = rioxarray.open_rasterio

    def _fake_open(u, *a, **k):
        da = real_open(u, *a, **k)

        class _Reproj:
            def __init__(self, inner):
                self._inner = inner

            def reproject(self, *a, **k):
                # Return a deliberately wrong shape (5x5) to trip the invariant.
                bad = self._inner.isel(band=0)[:5, :5]
                return bad

        # attach a fake .rio with a reproject that returns wrong shape
        band0 = da.isel(band=0) if "band" in da.dims else da

        class _Band:
            dims = band0.dims
            sizes = band0.sizes

            @property
            def rio(self):
                return _Reproj(da)

            def isel(self, *a, **k):
                return self

        return _Band()

    monkeypatch.setattr(rioxarray, "open_rasterio", _fake_open)

    with pytest.raises(ValueError, match="alignment produced shape"):
        GridAligner().execute(ctx)


# --------------------------------------------------------------------------- #
# Vector path: reproject + clip to grid bbox
# --------------------------------------------------------------------------- #

def test_vector_reproject_and_clip(tmp_path):
    """A GeoDataFrame in a different CRS is reprojected and clipped to the bbox."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box as shp_box

    grid = _grid(resolution=10, bbox=(0, 0, 100, 100))

    # One polygon fully inside the grid, one fully outside (after reprojection
    # the outside one must be removed by the clip).
    inside = shp_box(10, 10, 40, 40)
    outside = shp_box(1000, 1000, 1100, 1100)
    gdf = gpd.GeoDataFrame({"v": [1, 2]}, geometry=[inside, outside], crs=CRS)

    # Put it in WGS84 to force a reprojection branch.
    gdf_wgs = gdf.to_crs("EPSG:4326")

    src = _vector_source()
    ctx = _ctx(grid, [Variable(name="v", operator="attribute")], src, data=gdf_wgs)

    GridAligner().execute(ctx)
    out = ctx.data

    assert isinstance(out, gpd.GeoDataFrame)
    # Back in the grid CRS.
    from pyproj import CRS as ProjCRS
    assert ProjCRS.from_user_input(out.crs).equals(ProjCRS.from_user_input(CRS))
    # The outside polygon was clipped away; only the inside one survives.
    assert len(out) >= 1
    assert out.total_bounds[0] >= -1 and out.total_bounds[2] <= 101
