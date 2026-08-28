"""
The catalog must survive the store moving.

``DerivedVariable.asset_url`` used to be whatever ``AssetStore.get_full_path``
produced, which meant it inherited the shape of the ``base_url`` the client
happened to be built with:

  - ``CubeClient(..., store="/srv/cube")``  → absolute path in the catalog,
    broken as soon as the project is copied to another machine or directory;
  - ``CubeClient(..., store="./data/")``    → working-directory-relative path,
    broken as soon as a script runs from anywhere but the project root.

Both forms resolve only in the process that wrote them, and ``load()`` /
``purge_stale()`` treat an unresolvable path as a *stale entry* — so the
failure is silent data loss from the catalog, not an error.

The catalog now stores the path relative to the store root, and the store
resolves it on read.
"""

import shutil

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from disscube.client import CubeClient
from disscube.storage import AssetStore
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable

CRS = "EPSG:31982"


def _write_tif(path, array, bbox=(0, 0, 100, 100)):
    rows, cols = array.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=CRS, transform=from_bounds(*bbox, cols, rows),
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    return str(path)


def _make_cube(catalog, store):
    cube = CubeClient(str(catalog), str(store))
    cube.register_grid(GridSpec(id="G", type="local", crs=CRS,
                                resolution=100, bbox=[0, 0, 100, 100]))
    return cube


def _derive_one(cube, tif):
    cube.register_spatial_source(
        SpatialSource(id="S", name="S", format="raster", asset_url=tif, crs=CRS)
    )
    return cube.derive(SpatialDerivation(
        source_id="S", grid_id="G", role="driver",
        variables=[Variable(name="v", operator="mean")],
    ))


# --------------------------------------------------------------------------- #
# AssetStore — the relative/full boundary
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("base", ["./data/", "data", "/srv/cube", "/srv/cube/"])
def test_to_relative_inverts_get_full_path(base):
    store = AssetStore(base)
    rel = "derived/G/global/abc123/v.zarr"
    assert store.to_relative(store.get_full_path(rel)) == rel


def test_to_relative_refuses_a_path_outside_the_store(tmp_path):
    store = AssetStore(str(tmp_path / "store"))
    with pytest.raises(ValueError, match="outside the store root"):
        store.to_relative(str(tmp_path / "elsewhere" / "v.zarr"))


def test_to_relative_accepts_an_absolute_path_under_a_relative_base(tmp_path, monkeypatch):
    """base_url="./data/" and an already-absolute path still relativise."""
    monkeypatch.chdir(tmp_path)
    store = AssetStore("./data/")
    absolute = str(tmp_path / "data" / "derived" / "G" / "v.zarr")
    assert store.to_relative(absolute) == "derived/G/v.zarr"


def test_resolve_joins_a_relative_asset_url(tmp_path):
    store = AssetStore(str(tmp_path / "store"))
    resolved = store.resolve("derived/G/global/h/v.zarr")
    assert resolved == str(tmp_path / "store") + "/derived/G/global/h/v.zarr"


@pytest.mark.parametrize("legacy", ["/old/store/derived/v.zarr", "s3://b/p/v.zarr"])
def test_resolve_passes_through_a_legacy_absolute_asset_url(tmp_path, legacy):
    """An existing catalog keeps working: no migration required."""
    store = AssetStore(str(tmp_path / "store"))
    assert store.resolve(legacy) == legacy


def test_resolve_passes_through_a_legacy_base_prefixed_asset_url():
    """The other legacy shape: written when base_url was relative."""
    store = AssetStore("./data/")
    assert store.resolve("./data/derived/G/v.zarr") == "./data/derived/G/v.zarr"


# --------------------------------------------------------------------------- #
# What the writer records
# --------------------------------------------------------------------------- #

def test_catalog_records_a_store_relative_asset_url(tmp_path):
    cube = _make_cube(tmp_path / "catalog.db", tmp_path / "store")
    tif = _write_tif(tmp_path / "src.tif", np.ones((2, 2), dtype=np.float32))

    derived = _derive_one(cube, tif)

    url = derived[0].asset_url
    assert url == "derived/G/global/{}/v.zarr".format(derived[0].spec_hash)
    assert not url.startswith("/"), f"asset_url must not be absolute: {url}"
    assert str(tmp_path) not in url, (
        f"asset_url leaks the store location into the catalog: {url}"
    )


# --------------------------------------------------------------------------- #
# The regression this all exists for
# --------------------------------------------------------------------------- #

def test_catalog_survives_the_store_being_moved(tmp_path):
    """Derive under one root, move everything, reopen — load() still works."""
    origin = tmp_path / "origin"
    origin.mkdir()
    cube = _make_cube(origin / "catalog.db", origin / "store")
    tif = _write_tif(tmp_path / "src.tif", np.array([[2, 4], [3, 5]], np.float32))
    _derive_one(cube, tif)

    expected = cube.load("v", grid_id="G").values[0, 0]
    del cube

    moved = tmp_path / "moved"
    shutil.move(str(origin), str(moved))

    reopened = CubeClient(str(moved / "catalog.db"), str(moved / "store"))
    da = reopened.load("v", grid_id="G")

    np.testing.assert_allclose(da.values[0, 0], expected, atol=1e-6)


def test_moved_store_is_not_purged_as_stale(tmp_path):
    """purge_stale() must not delete entries just because the store moved."""
    origin = tmp_path / "origin"
    origin.mkdir()
    cube = _make_cube(origin / "catalog.db", origin / "store")
    tif = _write_tif(tmp_path / "src.tif", np.ones((2, 2), dtype=np.float32))
    _derive_one(cube, tif)
    del cube

    moved = tmp_path / "moved"
    shutil.move(str(origin), str(moved))

    reopened = CubeClient(str(moved / "catalog.db"), str(moved / "store"))
    assert reopened.purge_stale() == 0
    assert len(reopened.search()) == 1


def test_catalog_written_from_a_relative_store_reads_from_another_cwd(tmp_path, monkeypatch):
    """The ``store="./data/"`` shape used by every example script."""
    project = tmp_path / "project"
    project.mkdir()
    tif = _write_tif(tmp_path / "src.tif", np.full((2, 2), 7.0, np.float32))

    monkeypatch.chdir(project)
    cube = _make_cube("catalog.db", "./data/")
    _derive_one(cube, tif)
    del cube

    # Same catalog and same store, reached from a different working directory.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    reopened = CubeClient(str(project / "catalog.db"), str(project / "data"))
    da = reopened.load("v", grid_id="G")

    np.testing.assert_allclose(da.values[0, 0], 7.0, atol=1e-6)


def test_tile_layout_url_is_openable_after_a_move(tmp_path):
    """tile_layout()'s ``url`` is consumed directly by haloexec — it must open."""
    import xarray as xr

    origin = tmp_path / "origin"
    origin.mkdir()
    cube = _make_cube(origin / "catalog.db", origin / "store")
    tif = _write_tif(tmp_path / "src.tif", np.full((2, 2), 3.0, np.float32))
    _derive_one(cube, tif)
    del cube

    moved = tmp_path / "moved"
    shutil.move(str(origin), str(moved))

    reopened = CubeClient(str(moved / "catalog.db"), str(moved / "store"))
    item = reopened.tile_layout("v", "G")[0]

    da = xr.open_zarr(item["url"], consolidated=False)[item["variable"]]
    np.testing.assert_allclose(da.values[0, 0], 3.0, atol=1e-6)
