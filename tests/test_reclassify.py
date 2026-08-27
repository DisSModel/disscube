"""
Tests for the generic value-remapping operator (``reclassify``).

Covers the operator itself (lookup semantics, nodata/unmapped handling,
sparse tables) and its integration with the declarative ``Derivation``
front-end (fail-fast validation, spec_hash sensitivity to the table).
"""

import numpy as np
import pytest
import xarray as xr

from disscube.derivation import Derivation
from disscube.models import GridSpec, Variable
from disscube.operators.base import OPERATOR_REGISTRY

CRS = "EPSG:31982"


def _grid(rows=2, cols=3, resolution=10):
    return GridSpec(
        id="G1", type="local", crs=CRS, resolution=resolution,
        bbox=[0, 0, cols * resolution, rows * resolution],
    )


def _da(array, grid, nodata=None):
    da = xr.DataArray(
        np.asarray(array, dtype=np.float64),
        dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs},
    )
    if nodata is not None:
        da.attrs["_disscube_nodata"] = nodata
    return da


def _compute(array, mapping, grid=None, nodata=None):
    grid = grid or _grid(*np.shape(array))
    op = OPERATOR_REGISTRY["reclassify"]()
    var = Variable(name="v", operator="reclassify", mapping=mapping)
    return op.compute(_da(array, grid, nodata), var, grid).values


# ── Lookup semantics ─────────────────────────────────────────────────────────

def test_maps_every_value_through_the_table():
    src = [[1, 2, 3], [4, 5, 6]]
    mapping = {1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60}
    out = _compute(src, mapping)
    assert np.array_equal(out, np.array([[10, 20, 30], [40, 50, 60]], dtype=np.float64))


def test_many_to_one_grouping():
    """Several source codes collapsing onto one target code is the common case."""
    src = [[2, 3, 4], [5, 6, 1]]
    mapping = {1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2}
    out = _compute(src, mapping)
    assert np.array_equal(out, np.array([[2, 2, 2], [2, 2, 1]], dtype=np.float64))


def test_unmapped_values_become_nan():
    src = [[1, 99, 2]]
    out = _compute(src, {1: 10, 2: 20}, grid=_grid(rows=1, cols=3))
    assert out[0, 0] == 10
    assert np.isnan(out[0, 1])
    assert out[0, 2] == 20


def test_nodata_becomes_nan_even_when_present_in_the_table():
    """An explicit nodata sentinel wins over a table entry for the same value."""
    src = [[1, -9999, 2]]
    mapping = {1: 10, 2: 20, -9999: 77}
    out = _compute(src, mapping, grid=_grid(rows=1, cols=3), nodata=-9999)
    assert out[0, 0] == 10
    assert np.isnan(out[0, 1])
    assert out[0, 2] == 20


def test_nan_input_stays_nan():
    src = [[1.0, np.nan, 2.0]]
    out = _compute(src, {1: 10, 2: 20}, grid=_grid(rows=1, cols=3))
    assert np.isnan(out[0, 1])


def test_sparse_table_with_large_codes():
    """Codes far apart must not require a dense 0..max lookup array."""
    src = [[3, 500, 33000]]
    mapping = {3: 1, 500: 2, 33000: 3}
    out = _compute(src, mapping, grid=_grid(rows=1, cols=3))
    assert np.array_equal(out, np.array([[1, 2, 3]], dtype=np.float64))


def test_zero_is_a_mappable_code_not_treated_as_missing():
    src = [[0, 1]]
    out = _compute(src, {0: 7, 1: 8}, grid=_grid(rows=1, cols=2))
    assert np.array_equal(out, np.array([[7, 8]], dtype=np.float64))


def test_negative_codes_are_mappable():
    src = [[-3, 2]]
    out = _compute(src, {-3: 1, 2: 5}, grid=_grid(rows=1, cols=2))
    assert np.array_equal(out, np.array([[1, 5]], dtype=np.float64))


def test_output_is_grid_shaped_with_grid_coords():
    grid = _grid(rows=2, cols=3)
    op = OPERATOR_REGISTRY["reclassify"]()
    var = Variable(name="v", operator="reclassify", mapping={1: 1})
    result = op.compute(_da(np.ones((2, 3)), grid), var, grid)
    assert result.dims == ("y", "x")
    assert result.shape == (grid.rows, grid.cols)
    assert np.array_equal(result.coords["x"].values, grid.xs)
    assert np.array_equal(result.coords["y"].values, grid.ys)


def test_band_dimension_is_collapsed():
    grid = _grid(rows=1, cols=2)
    da = xr.DataArray(
        np.array([[[1.0, 2.0]]]),
        dims=("band", "y", "x"),
        coords={"band": [1], "y": grid.ys, "x": grid.xs},
    )
    op = OPERATOR_REGISTRY["reclassify"]()
    var = Variable(name="v", operator="reclassify", mapping={1: 9, 2: 8})
    out = op.compute(da, var, grid).values
    assert np.array_equal(out, np.array([[9, 8]], dtype=np.float64))


# ── Guard rails ──────────────────────────────────────────────────────────────

def test_empty_mapping_raises():
    grid = _grid(rows=1, cols=2)
    op = OPERATOR_REGISTRY["reclassify"]()
    var = Variable(name="v", operator="reclassify", mapping=None)
    with pytest.raises(ValueError, match="requires a non-empty mapping"):
        op.compute(_da([[1, 2]], grid), var, grid)


def test_vector_source_raises():
    grid = _grid(rows=1, cols=2)
    op = OPERATOR_REGISTRY["reclassify"]()
    var = Variable(name="v", operator="reclassify", mapping={1: 1})
    with pytest.raises(TypeError, match="requires a raster source"):
        op.compute("not a raster", var, grid)


# ── Declarative front-end ────────────────────────────────────────────────────

def test_derivation_requires_mapping():
    with pytest.raises(ValueError, match="requires mapping"):
        Derivation(target="papel", source_id="s1", operator="reclassify")


def test_derivation_with_mapping_ok():
    d = Derivation(
        target="papel", source_id="s1", operator="reclassify", mapping={1: 1, 2: 2},
    )
    assert d.to_variable().mapping == {1: 1, 2: 2}


def test_mapping_changes_spec_hash():
    """Two derivations differing only in the table must be distinct products."""
    a = Derivation(target="p", source_id="s", operator="reclassify", mapping={1: 1})
    b = Derivation(target="p", source_id="s", operator="reclassify", mapping={1: 2})
    assert a.spec_hash() != b.spec_hash()


def test_same_mapping_same_spec_hash():
    a = Derivation(target="p", source_id="s", operator="reclassify", mapping={1: 1, 2: 5})
    b = Derivation(target="p", source_id="s", operator="reclassify", mapping={2: 5, 1: 1})
    assert a.spec_hash() == b.spec_hash()


def test_string_keys_are_coerced_to_int():
    """Tables loaded from JSON/TOML arrive with string keys."""
    d = Derivation(
        target="p", source_id="s", operator="reclassify", mapping={"1": 10, "2": 20},
    )
    assert d.to_variable().mapping == {1: 10, 2: 20}
