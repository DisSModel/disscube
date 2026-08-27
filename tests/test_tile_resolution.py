"""
Tests for tile-source resolution in ``CubeClient._resolve_tile_source``.

Two registration conventions must both work:
  * ``{grid_id}_{tile_id}``   — a tile mesh defined for one specific grid;
  * ``BDC_{LEVEL}_{tile_id}`` — the national BDC grids, registered once and
    shared by every grid in the BDC CRS.

BDC tile ids are NOT unique across levels (in the real V2 grids 189 ids exist
in both SM and MD, covering different areas), so a bare ambiguous id must be
an error rather than resolved by precedence.
"""

import tempfile
from pathlib import Path

import pytest

from disscube.client import CubeClient
from disscube.models import SpatialSource

GRID_ID = "BR/5km"
CRS = "EPSG:4326"


@pytest.fixture
def cube():
    with tempfile.TemporaryDirectory() as d:
        yield CubeClient(catalog=str(Path(d) / "c.db"), store=str(Path(d) / "store"))


def _tile(cube, source_id, bbox=(0.0, 0.0, 1.0, 1.0)):
    cube.register_spatial_source(SpatialSource(
        id=source_id, name=source_id, format="raster",
        asset_url="planned", crs=CRS, bbox=list(bbox),
    ))


# ── Grid-scoped convention ───────────────────────────────────────────────────

def test_grid_scoped_tile_is_found(cube):
    _tile(cube, f"{GRID_ID}_T01", bbox=(1, 2, 3, 4))
    assert cube._resolve_tile_source(GRID_ID, "T01").bbox == [1, 2, 3, 4]


def test_grid_scoped_wins_over_bdc(cube):
    """A mesh registered for this grid takes precedence over a BDC tile."""
    _tile(cube, f"{GRID_ID}_027005", bbox=(9, 9, 10, 10))
    _tile(cube, "BDC_SM_027005", bbox=(1, 1, 2, 2))
    assert cube._resolve_tile_source(GRID_ID, "027005").bbox == [9, 9, 10, 10]


def test_tile_source_without_bbox_raises(cube):
    cube.register_spatial_source(SpatialSource(
        id=f"{GRID_ID}_T01", name="T01", format="raster",
        asset_url="planned", crs=CRS,   # sem bbox
    ))
    with pytest.raises(ValueError, match="no bbox"):
        cube._resolve_tile_source(GRID_ID, "T01")


# ── BDC convention ───────────────────────────────────────────────────────────

def test_bare_bdc_tile_id_resolves(cube):
    """The workflow documented in docs/guides/bdc.md: grid BR/5km + bare id."""
    _tile(cube, "BDC_SM_027005", bbox=(5, 6, 7, 8))
    assert cube._resolve_tile_source(GRID_ID, "027005").bbox == [5, 6, 7, 8]


@pytest.mark.parametrize("level", ["SM", "MD", "LG"])
def test_each_bdc_level_resolves(cube, level):
    _tile(cube, f"BDC_{level}_000123", bbox=(1, 1, 2, 2))
    assert cube._resolve_tile_source(GRID_ID, "000123").id == f"BDC_{level}_000123"


def test_fully_qualified_bdc_id_resolves(cube):
    _tile(cube, "BDC_MD_000123", bbox=(3, 3, 4, 4))
    assert cube._resolve_tile_source(GRID_ID, "BDC_MD_000123").bbox == [3, 3, 4, 4]


# ── Ambiguity must fail loudly ───────────────────────────────────────────────

def test_ambiguous_bare_id_raises(cube):
    """Same id at two levels = different areas; guessing would be silently wrong."""
    _tile(cube, "BDC_SM_005004", bbox=(1, 1, 2, 2))
    _tile(cube, "BDC_MD_005004", bbox=(50, 50, 60, 60))
    with pytest.raises(ValueError, match="ambiguous"):
        cube._resolve_tile_source(GRID_ID, "005004")


def test_ambiguity_error_lists_candidates_and_suggests_qualified_id(cube):
    _tile(cube, "BDC_SM_005004")
    _tile(cube, "BDC_LG_005004")
    with pytest.raises(ValueError) as exc:
        cube._resolve_tile_source(GRID_ID, "005004")
    msg = str(exc.value)
    assert "BDC_SM_005004" in msg and "BDC_LG_005004" in msg
    assert "tile_id='BDC_SM_005004'" in msg


def test_qualified_id_escapes_ambiguity(cube):
    _tile(cube, "BDC_SM_005004", bbox=(1, 1, 2, 2))
    _tile(cube, "BDC_MD_005004", bbox=(50, 50, 60, 60))
    assert cube._resolve_tile_source(GRID_ID, "BDC_SM_005004").bbox == [1, 1, 2, 2]
    assert cube._resolve_tile_source(GRID_ID, "BDC_MD_005004").bbox == [50, 50, 60, 60]


# ── Not found ────────────────────────────────────────────────────────────────

def test_missing_tile_raises_with_searched_ids(cube):
    with pytest.raises(ValueError) as exc:
        cube._resolve_tile_source(GRID_ID, "999999")
    msg = str(exc.value)
    assert f"{GRID_ID}_999999" in msg
    assert "BDC_" in msg
