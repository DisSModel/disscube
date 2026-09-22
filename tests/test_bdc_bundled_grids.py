"""
Tests against the BDC Grid V2 files bundled in disscube/data/bdc_grids/.

Unlike the mocked importer tests in test_bdc_master_grids.py, these read the
real shapefiles. They need the optional ``fiona`` dependency
(``pip install disscube[bdc]``) and are skipped without it.
"""

import hashlib
from importlib.resources import files

import pytest
from pyproj import CRS

from disscube.client import CubeClient
from disscube.models import SpatialDerivation, SpatialSource, Variable
from disscube.utils.bdc_importer import bundled_bdc_grid, import_bdc_grids
from disscube.utils.grids import BDC_CRS

fiona = pytest.importorskip("fiona", reason="fiona not installed (install disscube[bdc])")

GRID_DIR = files("disscube") / "data" / "bdc_grids"

# Checksums recorded in disscube/data/bdc_grids/README.md
SHA256 = {
    "SM": "beac19f96be156707361c8a8898675b8da4ab9dbced2bab6dd3221d6bb39c945",
    "MD": "637597ae6b11af7e6946fb6bf40beda9a7e8ade71227830ad2a2fed4e375c566",
    "LG": "142ee56c668a46f79c82d86e18d692db37651a8736408ac41f864a6ab2d0d179",
}
N_TILES = {"SM": 871, "MD": 242, "LG": 75}
TILE_SIZE_M = {"SM": 105_600, "MD": 211_200, "LG": 422_400}


@pytest.fixture(scope="module")
def cube(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bdc")
    c = CubeClient(str(tmp / "catalog.db"), str(tmp / "store"))
    import_bdc_grids(c)  # no paths: uses the bundled files
    return c


@pytest.mark.parametrize("level", ["SM", "MD", "LG"])
def test_bundled_file_checksum(level):
    data = (GRID_DIR / f"BDC_{level}_V2.zip").read_bytes()
    assert hashlib.sha256(data).hexdigest() == SHA256[level]


@pytest.mark.parametrize("level", ["SM", "MD", "LG"])
def test_bundled_grid_tiles_and_crs(level):
    with fiona.open(bundled_bdc_grid(level)) as src:
        assert len(src) == N_TILES[level]
        assert list(src.schema["properties"]) == ["tile"]
        # The .prj carries a bogus AUTHORITY["EPSG","200000"]; the projection
        # itself must match BDC_CRS.
        wkt = src.crs_wkt.replace(',AUTHORITY["EPSG","200000"]', "")
        assert CRS.from_wkt(wkt).equals(CRS(BDC_CRS))
        rec = next(iter(src))
        xs = [x for x, _ in rec["geometry"]["coordinates"][0]]
        assert max(xs) - min(xs) == pytest.approx(TILE_SIZE_M[level])


def test_bundled_bdc_grid_rejects_unknown_level():
    with pytest.raises(ValueError):
        bundled_bdc_grid("XL")


def test_import_registers_simulation_grids(cube):
    grid_ids = {g.id for g in cube.catalog.list_grids()}
    assert {"BR/5km", "BR/1km"} <= grid_ids


def test_import_registers_every_tile(cube):
    ids = [s.id for s in cube.catalog.list_spatial_sources()]
    for level, n in N_TILES.items():
        assert sum(i.startswith(f"BDC_{level}_") for i in ids) == n


def test_imported_tile_bbox(cube):
    tile = cube.catalog.get_spatial_source("BDC_SM_001014")
    assert tile is not None
    assert tile.crs == BDC_CRS
    assert tile.bbox == pytest.approx([2_729_600, 10_369_600, 2_835_200, 10_475_200])


@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason=(
        "Known gap: CubeClient.derive(tile_id=...) looks the tile up as "
        "'{grid_id}_{tile_id}' (e.g. 'BR/5km_001014'), but the importer "
        "registers tiles as 'BDC_{LEVEL}_{tile}', so the documented BDC "
        "workflow cannot find any tile."
    ),
)
def test_derive_on_bundled_tile(cube):
    cube.register_spatial_source(SpatialSource(
        id="dummy", name="dummy", format="raster", asset_url="missing.tif", crs=BDC_CRS,
    ))
    cube.derive(
        SpatialDerivation(
            source_id="dummy", grid_id="BR/5km", role="driver",
            variables=[Variable(name="v", operator="mean")],
        ),
        tile_id="001014",
    )
