"""
Full-pipeline roundtrip integration tests.

Exercises the complete Normalizer → GridAligner → Aggregator → VariableWriter
chain via CubeClient.derive(), then reloads via CubeClient.load() and verifies
that pixel values, purity coordinates, and catalog metadata survive the Zarr
serialisation roundtrip.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialDerivation, SpatialSource, Variable

CRS = "EPSG:31982"


def _write_tif(path, array, bbox=(0, 0, 100, 100), nodata=None):
    rows, cols = array.shape
    transform = from_bounds(*bbox, cols, rows)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=CRS, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    return str(path)


@pytest.fixture()
def cube(tmp_path):
    """Fresh CubeClient with a single 1×1 cell grid (100 m, 100×100 bbox)."""
    c = CubeClient(str(tmp_path / "catalog.db"), str(tmp_path / "store"))
    c.register_grid(GridSpec(id="G", type="local", crs=CRS,
                             resolution=100, bbox=[0, 0, 100, 100]))
    return c


def _derive(cube, tif_path, operator, class_code=None, var_name="v",
            valid_from=None):
    cube.register_spatial_source(
        SpatialSource(id="S", name="S", format="raster",
                      asset_url=tif_path, crs=CRS)
    )
    derivation = SpatialDerivation(
        source_id="S", grid_id="G", role="driver",
        variables=[Variable(name=var_name, operator=operator,
                            class_code=class_code)],
        valid_from=valid_from,
    )
    return cube.derive(derivation), derivation


# --------------------------------------------------------------------------- #
# Value correctness
# --------------------------------------------------------------------------- #

def test_mean_values_survive_roundtrip(tmp_path, cube):
    """derive(mean) → load() → pixel value equals arithmetic mean of source pixels."""
    src = np.array([[2, 4], [3, 5]], dtype=np.float32)
    tif = _write_tif(tmp_path / "mean.tif", src)

    _derive(cube, tif, operator="mean")

    da = cube.load("v", grid_id="G")
    assert da.shape == (1, 1)
    np.testing.assert_allclose(da.values[0, 0], np.mean([2, 4, 3, 5]), atol=1e-4)


def test_percentage_value_survives_roundtrip(tmp_path, cube):
    """derive(percentage) → load() → value matches expected class fraction."""
    # 2×2 source over 1×1 target cell: 3 px class 1, 1 px class 2 → 0.75
    src = np.array([[1, 1], [1, 2]], dtype=np.float32)
    tif = _write_tif(tmp_path / "pct.tif", src)

    _derive(cube, tif, operator="percentage", class_code=1, var_name="pct")

    da = cube.load("pct", grid_id="G")
    np.testing.assert_allclose(da.values[0, 0], 0.75, atol=1e-5)


# --------------------------------------------------------------------------- #
# Purity coordinates survive Zarr serialisation
# --------------------------------------------------------------------------- #

def test_coverage_purity_survives_roundtrip(tmp_path, cube):
    """coverage_purity coordinate is preserved after Zarr write + reload."""
    src = np.array([[1, 1], [1, 2]], dtype=np.float32)
    tif = _write_tif(tmp_path / "pct.tif", src)

    _derive(cube, tif, operator="percentage", class_code=1, var_name="pct")

    da = cube.load("pct", grid_id="G")
    assert "coverage_purity" in da.coords, (
        "coverage_purity coordinate was dropped during Zarr serialisation. "
        "Check that VariableWriter saves the full DataArray with auxiliary coords."
    )
    # All 4 source pixels are valid → coverage = 1.0
    np.testing.assert_allclose(da.coords["coverage_purity"].values[0, 0], 1.0, atol=1e-5)


def test_dominance_purity_survives_roundtrip(tmp_path, cube):
    """dominance_purity coordinate is preserved after Zarr write + reload."""
    src = np.array([[1, 1], [1, 2]], dtype=np.float32)
    tif = _write_tif(tmp_path / "pct.tif", src)

    _derive(cube, tif, operator="percentage", class_code=1, var_name="pct")

    da = cube.load("pct", grid_id="G")
    assert "dominance_purity" in da.coords, (
        "dominance_purity coordinate was dropped during Zarr serialisation."
    )
    # Class 1 dominates: 3/4 = 0.75
    np.testing.assert_allclose(da.coords["dominance_purity"].values[0, 0], 0.75, atol=1e-5)


def test_nodata_purity_survives_roundtrip(tmp_path, cube):
    """coverage_purity < 1 for cells with nodata pixels survives roundtrip."""
    # Half-nodata: left column class 1, right column nodata(-1)
    src = np.array([[1, -1], [1, -1]], dtype=np.float32)
    tif = _write_tif(tmp_path / "nodata.tif", src, nodata=-1)

    _derive(cube, tif, operator="percentage", class_code=1, var_name="p")

    da = cube.load("p", grid_id="G")
    # 2 valid / 4 total → 0.5
    np.testing.assert_allclose(da.coords["coverage_purity"].values[0, 0], 0.5, atol=1e-5)
    # class 1 is 100 % of valid pixels
    np.testing.assert_allclose(da.values[0, 0], 1.0, atol=1e-5)


# --------------------------------------------------------------------------- #
# Catalog metadata
# --------------------------------------------------------------------------- #

def test_content_hash_is_recorded(tmp_path, cube):
    """VariableWriter records a non-empty SHA-256 content_hash in the catalog."""
    src = np.ones((2, 2), dtype=np.float32)
    tif = _write_tif(tmp_path / "ones.tif", src)

    derived_vars, _ = _derive(cube, tif, operator="mean")

    assert len(derived_vars) == 1
    dv = derived_vars[0]
    assert dv.content_hash is not None
    assert len(dv.content_hash) == 64  # SHA-256 hex digest is 64 chars


def test_derive_records_spec_hash(tmp_path, cube):
    """Catalog entry carries the spec_hash from the derivation."""
    src = np.ones((2, 2), dtype=np.float32)
    tif = _write_tif(tmp_path / "ones.tif", src)

    derived_vars, derivation = _derive(cube, tif, operator="mean")

    assert derived_vars[0].spec_hash == derivation.spec_hash()


# --------------------------------------------------------------------------- #
# Cache hit
# --------------------------------------------------------------------------- #

def test_cache_hit_skips_recomputation(tmp_path, cube):
    """Second derive() with the same spec returns cached result without re-writing Zarr."""
    src = np.ones((2, 2), dtype=np.float32)
    tif = _write_tif(tmp_path / "cache.tif", src)

    derived_first, derivation = _derive(cube, tif, operator="mean")
    first_hash = derived_first[0].content_hash

    derived_second = cube.derive(derivation)

    assert derived_second[0].spec_hash == derived_first[0].spec_hash
    assert derived_second[0].content_hash == first_hash


# --------------------------------------------------------------------------- #
# Temporal variable
# --------------------------------------------------------------------------- #

def test_temporal_variable_roundtrip(tmp_path, cube):
    """Temporal derivation (valid_from='2020') → load() returns (time, y, x)."""
    src = np.full((2, 2), 7.0, dtype=np.float32)
    tif = _write_tif(tmp_path / "t2020.tif", src)

    _derive(cube, tif, operator="mean", var_name="v", valid_from="2020")

    da = cube.load("v", grid_id="G")
    assert da.ndim == 3, (
        f"Expected 3D (time, y, x), got {da.ndim}D. "
        "Temporal variable should always produce a time axis."
    )
    assert "time" in da.dims
    assert list(da.coords["time"].values) == [2020]


def test_cf_conventions_metadata_roundtrip(tmp_path, cube):
    """Zarr storage enriches derived variables with CF-1.8 metadata and coordinates."""
    src = np.ones((2, 2), dtype=np.float32)
    tif = _write_tif(tmp_path / "cf_test.tif", src)

    _derive(cube, tif, operator="mean", var_name="cf_var")

    da = cube.load("cf_var", grid_id="G")
    assert da.attrs.get("conventions") == "CF-1.8"
    assert da.attrs.get("operator") == "mean"
    assert da.attrs.get("source_id") == "S"
    assert da.attrs.get("grid_mapping") == "spatial_ref"
    assert da.coords["x"].attrs.get("standard_name") == "projection_x_coordinate"
    assert da.coords["y"].attrs.get("standard_name") == "projection_y_coordinate"

