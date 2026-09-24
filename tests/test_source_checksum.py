"""
A source's checksum keys the derivation cache.

Replacing a source file and registering it with a new checksum must produce a
new product (a new spec_hash), not a stale cache hit; sources without a
checksum keep the spec_hash they always had.
"""

import hashlib
import json

import numpy as np
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation, GridSpec, SpatialSource
from disscube.models import SpatialDerivation, Variable
from disscube.utils.files import sha256_file

CRS = "EPSG:31983"


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "catalog.db"), store=str(tmp_path / "store"))
    c.register_grid(GridSpec(id="g", type="local", crs=CRS, resolution=300.0, bbox=[0, 0, 3000, 3000]))
    return c


def _write(path, value):
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
                       crs=CRS, transform=from_origin(0, 3000, 30, 30)) as dst:
        dst.write(np.full((100, 100), value, dtype="float32"), 1)
    return path


def _register(cube, path, checksum):
    cube.register_spatial_source(SpatialSource(id="ndvi", name="ndvi", format="raster",
                                               asset_url=str(path), crs=CRS, checksum=checksum))


MEAN = Derivation(target="ndvi_mean", source_id="ndvi", operator="mean")


def test_new_checksum_recomputes_instead_of_stale_cache(cube, tmp_path):
    path = tmp_path / "ndvi.tif"
    _register(cube, _write(path, 0.2), sha256_file(path))
    first = cube.derive_declarative(MEAN, grid_id="g")[0]
    assert float(cube.load("ndvi_mean", grid_id="g").mean()) == pytest.approx(0.2)

    _register(cube, _write(path, 0.8), sha256_file(path))
    second = cube.derive_declarative(MEAN, grid_id="g")[0]

    assert second.spec_hash != first.spec_hash
    assert float(xr.open_zarr(second.asset_url, consolidated=False)["ndvi_mean"].mean()) == pytest.approx(0.8)
    # the new product supersedes the old one: load() returns only the new version
    loaded = cube.load("ndvi_mean", grid_id="g")
    assert "time" not in loaded.dims
    assert float(loaded.mean()) == pytest.approx(0.8)


def test_superseding_keeps_other_times(cube, tmp_path):
    for year, value in ((2019, 0.3), (2020, 0.6)):
        path = _write(tmp_path / f"ndvi_{year}.tif", value)
        cube.register_spatial_source(SpatialSource(
            id=f"ndvi_{year}", name="ndvi", format="raster", asset_url=str(path), crs=CRS,
            time=year, checksum=sha256_file(path)))
        cube.derive_declarative(Derivation(target="ndvi_mean", source_id=f"ndvi_{year}",
                                           operator="mean"), grid_id="g")
    series = cube.load("ndvi_mean", grid_id="g")
    assert list(series["time"].values) == [2019, 2020]
    assert np.allclose(series.mean(dim=("y", "x")).values, [0.3, 0.6])


def test_same_checksum_is_a_cache_hit(cube, tmp_path):
    path = tmp_path / "ndvi.tif"
    _register(cube, _write(path, 0.2), sha256_file(path))
    first = cube.derive_declarative(MEAN, grid_id="g")[0]
    _register(cube, path, sha256_file(path))
    again = cube.derive_declarative(MEAN, grid_id="g")[0]
    assert again.spec_hash == first.spec_hash


def test_spec_hash_without_checksum_is_unchanged():
    base = SpatialDerivation(source_id="s", grid_id="g", role="driver",
                             variables=[Variable(name="v", operator="mean")])
    assert base.spec_hash() == base.model_copy(update={"source_checksum": None}).spec_hash()
    # value of the hash before source_checksum existed (regression guard)
    legacy = json.dumps({
        "source_id": "s", "grid_id": "g", "role": "driver",
        "variables": [v.model_dump() for v in base.variables],
        "valid_from": None, "valid_until": None,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")
    assert base.spec_hash() == hashlib.sha256(legacy).hexdigest()
    assert base.model_copy(update={"source_checksum": "sha256:abc"}).spec_hash() != base.spec_hash()


def test_sha256_file_prefix_and_change(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"a")
    h1 = sha256_file(p)
    p.write_bytes(b"b")
    assert h1.startswith("sha256:") and len(h1) == 7 + 64
    assert sha256_file(p) != h1
