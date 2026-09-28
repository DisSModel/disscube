"""
Tests for the declarative Derivation model (disscube/derivation.py).
"""

import pytest

from disscube.derivation import Derivation
from disscube.models.variable import SpatialDerivation, Variable

# ── Construction-time validation ──────────────────────────────────────────────

def test_unknown_operator_raises():
    with pytest.raises(ValueError, match="Unknown operator"):
        Derivation(target="x", source_id="s1", operator="nonexistent")


def test_unknown_operator_error_lists_available():
    with pytest.raises(ValueError, match="Available operators"):
        Derivation(target="x", source_id="s1", operator="bogus")


def test_percentage_without_class_code_raises():
    with pytest.raises(ValueError, match="requires class_code"):
        Derivation(target="forest_pct", source_id="mapbiomas_2020", operator="percentage")


def test_percentage_with_class_code_ok():
    d = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
    )
    assert d.class_code == 3


def test_operator_without_class_code_requirement_accepts_none():
    d = Derivation(target="dist", source_id="roads", operator="min_distance")
    assert d.class_code is None


# ── to_variable() round-trip ──────────────────────────────────────────────────

def test_to_variable_fields():
    d = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
        role="driver",
        valid_from="2020",
        valid_until="2020",
    )
    v = d.to_variable()
    assert isinstance(v, Variable)
    assert v.name == "forest_pct"
    assert v.operator == "percentage"
    assert v.class_code == 3


def test_to_variable_no_class_code():
    d = Derivation(target="dist_road", source_id="roads", operator="min_distance")
    v = d.to_variable()
    assert v.name == "dist_road"
    assert v.class_code is None


# ── spec_hash consistency with SpatialDerivation ─────────────────────────────

def test_spec_hash_consistent_with_spatial_derivation():
    """
    to_spatial_derivation(grid_id).spec_hash() must equal the hash produced
    by building an equivalent SpatialDerivation directly.
    """
    d = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
        role="driver",
        valid_from="2020",
        valid_until="2020",
    )
    grid_id = "acre_5km"

    derived_hash = d.to_spatial_derivation(grid_id).spec_hash()

    direct = SpatialDerivation(
        source_id="mapbiomas_2020",
        grid_id=grid_id,
        role="driver",
        variables=[Variable(name="forest_pct", operator="percentage", class_code=3)],
        valid_from="2020",
        valid_until="2020",
    )
    assert derived_hash == direct.spec_hash()


def test_spec_hash_temporal_window_differs():
    """Different valid_from/valid_until must produce different hashes."""
    d2019 = Derivation(
        target="forest_pct", source_id="mb", operator="percentage",
        class_code=3, valid_from="2019", valid_until="2019",
    )
    d2020 = Derivation(
        target="forest_pct", source_id="mb", operator="percentage",
        class_code=3, valid_from="2020", valid_until="2020",
    )
    assert d2019.spec_hash() != d2020.spec_hash()


# ── purity_threshold changes spec_hash ───────────────────────────────────────

def test_purity_threshold_changes_spec_hash():
    base = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
    )
    with_threshold = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
        purity_threshold=0.9,
    )
    assert base.spec_hash() != with_threshold.spec_hash()


def test_different_purity_thresholds_differ():
    d1 = Derivation(
        target="forest_pct", source_id="mb", operator="percentage",
        class_code=3, purity_threshold=0.7,
    )
    d2 = Derivation(
        target="forest_pct", source_id="mb", operator="percentage",
        class_code=3, purity_threshold=0.9,
    )
    assert d1.spec_hash() != d2.spec_hash()


# ── bbox excluded from spec_hash ─────────────────────────────────────────────

def test_bbox_excluded_from_spec_hash():
    """
    Two Derivations differing only in bbox must produce the same spec_hash.
    bbox is descriptive metadata, not a derivation parameter.
    """
    d_no_bbox = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
    )
    d_with_bbox = Derivation(
        target="forest_pct",
        source_id="mapbiomas_2020",
        operator="percentage",
        class_code=3,
        bbox=[-74.0, -18.0, -44.0, 5.0],
    )
    assert d_no_bbox.spec_hash() == d_with_bbox.spec_hash()


# ── spec_hash determinism ─────────────────────────────────────────────────────

def test_spec_hash_is_deterministic():
    d = Derivation(
        target="forest_pct", source_id="mb", operator="percentage", class_code=3,
    )
    assert d.spec_hash() == d.spec_hash()
    assert len(d.spec_hash()) == 64
