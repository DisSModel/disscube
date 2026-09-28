"""
Tests for window-based categorical aggregation: percentage, majority, and the
coverage / dominance purity metrics.

These exercise the full GridAligner -> Aggregator path, since categorical
aggregation is performed by the operator's compute() (fine align + window
reduction), not by the aligner.
"""

import numpy as np
import pytest
import rasterio
import rioxarray  # noqa: F401
from rasterio.transform import from_bounds

from disscube.models import GridSpec, SpatialDerivation, SpatialSource, Variable
from disscube.pipeline import PipelineContext
from disscube.pipeline.aggregator import Aggregator
from disscube.pipeline.aligner import GridAligner

CRS = "EPSG:31982"


def _grid(resolution, bbox=(0, 0, 100, 100)):
    return GridSpec(id="G1", type="local", crs=CRS, resolution=resolution, bbox=list(bbox))


def _source(url, **kw):
    return SpatialSource(id="S1", name="S1", format="raster", asset_url=url, crs=CRS, **kw)


def _ctx(grid, variables, url):
    src = _source(url)
    deriv = SpatialDerivation(source_id="S1", grid_id=grid.id, role="test", variables=variables)
    return PipelineContext(source=src, grid=grid, derivation=deriv)


def _write(path, array, bbox=(0, 0, 100, 100), nodata=None):
    rows, cols = array.shape
    transform = from_bounds(*bbox, cols, rows)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=CRS, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    return str(path)


def _run(grid, var, url):
    ctx = _ctx(grid, [var], url)
    GridAligner().execute(ctx)
    Aggregator().execute(ctx)
    return ctx.data


# --------------------------------------------------------------------------- #
# percentage
# --------------------------------------------------------------------------- #

def test_percentage_known_mix(tmp_path):
    """A single 2x2 target cell with 3 px class 1 and 1 px class 2."""
    src = np.array([[1, 1], [1, 2]], dtype=np.float32)  # 50 m px over 0..100
    url = _write(tmp_path / "mix.tif", src)
    grid = _grid(resolution=100)  # one cell covering the whole extent

    out1 = _run(grid, Variable(name="c", operator="percentage", class_code=1), url)
    np.testing.assert_allclose(out1["c"].values[0, 0], 0.75, atol=1e-6)

    out2 = _run(grid, Variable(name="c", operator="percentage", class_code=2), url)
    np.testing.assert_allclose(out2["c"].values[0, 0], 0.25, atol=1e-6)


def test_percentage_sums_to_one(tmp_path):
    """Percentages over all present classes sum to ~1.0 per cell."""
    src = np.array(
        [[1, 1, 2, 3], [1, 1, 2, 3], [2, 2, 3, 3], [1, 2, 3, 3]],
        dtype=np.float32,
    )
    url = _write(tmp_path / "multi.tif", src)
    grid = _grid(resolution=50)  # 2x2 cells

    total = np.zeros((2, 2))
    for c in (1, 2, 3):
        out = _run(grid, Variable(name="p", operator="percentage", class_code=c), url)
        total += out["p"].values
    np.testing.assert_allclose(total, np.ones((2, 2)), atol=1e-6)


def test_percentage_requires_class_code(tmp_path):
    src = np.ones((4, 4), dtype=np.float32)
    url = _write(tmp_path / "x.tif", src)
    grid = _grid(resolution=50)
    with pytest.raises(ValueError, match="class_code"):
        _run(grid, Variable(name="p", operator="percentage"), url)


# --------------------------------------------------------------------------- #
# majority
# --------------------------------------------------------------------------- #

def test_majority_dominant_class(tmp_path):
    src = np.array(
        [[7, 7, 5, 5], [7, 9, 5, 5], [2, 2, 8, 8], [2, 2, 8, 6]],
        dtype=np.float32,
    )
    url = _write(tmp_path / "cls.tif", src)
    grid = _grid(resolution=50)
    out = _run(grid, Variable(name="uso", operator="majority"), url)
    v = out["uso"].values
    assert (v[0, 0], v[0, 1], v[1, 0], v[1, 1]) == (7, 5, 2, 8)


def test_majority_tie_resolves_to_smallest(tmp_path):
    """A 2x2 cell with two of class 3 and two of class 9 -> smallest (3)."""
    src = np.array([[3, 3], [9, 9]], dtype=np.float32)
    url = _write(tmp_path / "tie.tif", src)
    grid = _grid(resolution=100)
    out = _run(grid, Variable(name="uso", operator="majority"), url)
    assert out["uso"].values[0, 0] == 3


# --------------------------------------------------------------------------- #
# non-multiple resolution
# --------------------------------------------------------------------------- #

def test_percentage_non_multiple_resolution(tmp_path):
    """10 m source -> 30 m grid over 100 m extent: no crash, values in [0,1]."""
    rng = np.random.default_rng(0)
    src = rng.integers(1, 4, size=(10, 10)).astype(np.float32)
    url = _write(tmp_path / "rand.tif", src)
    grid = _grid(resolution=30)
    assert (grid.rows, grid.cols) == (3, 3)

    out = _run(grid, Variable(name="p", operator="percentage", class_code=2), url)
    vals = out["p"].values
    assert vals.shape == (3, 3)
    finite = vals[np.isfinite(vals)]
    assert np.all(finite >= 0.0) and np.all(finite <= 1.0)


# --------------------------------------------------------------------------- #
# purity metrics
# --------------------------------------------------------------------------- #

def test_coverage_purity_with_nodata(tmp_path):
    """A cell half-filled with nodata has coverage_purity ~0.5; full cell ~1.0."""
    # Left half class 1, right half nodata(-1). One 2x2 cell -> coverage 0.5.
    src = np.array([[1, -1], [1, -1]], dtype=np.float32)
    url = _write(tmp_path / "nd.tif", src, nodata=-1)
    grid = _grid(resolution=100)
    out = _run(grid, Variable(name="c", operator="percentage", class_code=1), url)
    cov = out["c"]["coverage_purity"].values[0, 0]
    np.testing.assert_allclose(cov, 0.5, atol=1e-6)
    # percentage is computed over VALID pixels only -> class 1 is 100% of valid.
    np.testing.assert_allclose(out["c"].values[0, 0], 1.0, atol=1e-6)


def test_coverage_purity_full_cell(tmp_path):
    src = np.ones((4, 4), dtype=np.float32)
    url = _write(tmp_path / "full.tif", src)
    grid = _grid(resolution=50)
    out = _run(grid, Variable(name="c", operator="percentage", class_code=1), url)
    np.testing.assert_allclose(out["c"]["coverage_purity"].values, np.ones((2, 2)), atol=1e-6)


def test_dominance_purity_value(tmp_path):
    """Cell with 3 px class 1, 1 px class 2 -> dominance_purity = 0.75."""
    src = np.array([[1, 1], [1, 2]], dtype=np.float32)
    url = _write(tmp_path / "dom.tif", src)
    grid = _grid(resolution=100)
    out = _run(grid, Variable(name="uso", operator="majority"), url)
    np.testing.assert_allclose(out["uso"]["dominance_purity"].values[0, 0], 0.75, atol=1e-6)


# --------------------------------------------------------------------------- #
# std (real window-based standard deviation)
# --------------------------------------------------------------------------- #

def test_std_real_value(tmp_path):
    """std computes the true per-cell standard deviation over a window."""
    src = np.array([[0, 2], [4, 6]], dtype=np.float32)
    url = _write(tmp_path / "std.tif", src)
    grid = _grid(resolution=100)  # single cell over 0,2,4,6
    out = _run(grid, Variable(name="v", operator="std"), url)
    np.testing.assert_allclose(out["v"].values[0, 0], np.std([0, 2, 4, 6]), atol=1e-5)


def test_std_coverage_purity(tmp_path):
    """std exposes coverage_purity and respects nodata."""
    src = np.array([[1, -1], [3, 5]], dtype=np.float32)  # one nodata pixel
    url = _write(tmp_path / "stdnd.tif", src, nodata=-1)
    grid = _grid(resolution=100)
    out = _run(grid, Variable(name="v", operator="std"), url)
    # std over valid {1,3,5}
    np.testing.assert_allclose(out["v"].values[0, 0], np.std([1, 3, 5]), atol=1e-5)
    np.testing.assert_allclose(out["v"]["coverage_purity"].values[0, 0], 0.75, atol=1e-6)
