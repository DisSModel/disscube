"""`percentage` accepts a list of classes: the share of the cell in any of them (a MapBiomas level-1 class
is a group of leaf codes)."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import mapbiomas
from disscube.utils.grids import register_local_grid

BBOX = (-44.35, -2.62, -44.20, -2.47)
RES = 0.00026949458523585647


def test_percentage_of_a_list_is_the_sum_of_the_shares(tmp_path):
    rng = np.random.default_rng(3)
    data = np.array([0, 3, 4, 15, 24, 33], dtype="uint8")[rng.integers(0, 6, size=(100, 100))]
    data = np.kron(data, np.ones((10, 10), dtype="uint8"))
    url = tmp_path / "br.tif"
    with rasterio.open(url, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES)) as dst:
        dst.write(data, 1)
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    grid = register_local_grid(cube, name="g", bbox_geo=BBOX, resolution=500.0)
    mapbiomas.register_mapbiomas_source(cube, "lulc", 2020, BBOX, tmp_path / "raw", url=str(url))
    for name, codes in (("a", 3), ("b", 4), ("both", [3, 4]), ("none", [])):
        cube.derive_declarative(Derivation(target=name, source_id="lulc", operator="percentage", class_code=codes),
                                grid_id=grid.id)
    a, b, both = (cube.load(n, grid_id=grid.id).values for n in ("a", "b", "both"))
    assert np.allclose(both, a + b, equal_nan=True)
    assert np.nanmax(both - a) > 0
    assert np.nanmax(cube.load("none", grid_id=grid.id).values) == 0.0


def test_majority_with_groups_is_the_dominant_group(tmp_path):
    import pytest

    rng = np.random.default_rng(5)
    data = np.array([0, 3, 4, 15, 24, 33], dtype="uint8")[rng.integers(0, 6, size=(100, 100))]
    data = np.kron(data, np.ones((10, 10), dtype="uint8"))
    url = tmp_path / "br.tif"
    with rasterio.open(url, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES)) as dst:
        dst.write(data, 1)
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    grid = register_local_grid(cube, name="g", bbox_geo=BBOX, resolution=500.0)
    mapbiomas.register_mapbiomas_source(cube, "lulc", 2020, BBOX, tmp_path / "raw", url=str(url))
    groups = {"1": [3, 4], "2": [15], "3": [24, 33]}              # TOML keys are strings
    cube.derive_declarative(Derivation(target="top", source_id="lulc", operator="majority", params={"groups": groups}),
                            grid_id=grid.id)
    shares = []
    for i, codes in enumerate(groups.values()):
        cube.derive_declarative(Derivation(target=f"s{i}", source_id="lulc", operator="percentage", class_code=codes),
                                grid_id=grid.id)
        shares.append(cube.load(f"s{i}", grid_id=grid.id).values)
    top = cube.load("top", grid_id=grid.id).values
    expected = np.argmax(np.nan_to_num(np.stack(shares), nan=-1), axis=0) + 1.0
    valid = ~np.isnan(shares[0])
    assert np.array_equal(top[valid], expected[valid]) and set(np.unique(top[valid])) <= {1.0, 2.0, 3.0}
    with pytest.raises(ValueError, match="more than one group"):
        cube.derive_declarative(Derivation(target="bad", source_id="lulc", operator="majority",
                                           params={"groups": {"1": [3], "2": [3, 4]}}), grid_id=grid.id)
