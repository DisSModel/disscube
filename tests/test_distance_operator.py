"""
Tests for the exact ``distance`` operator and ``min_distance`` without features.

``distance`` measures from each cell centre to the nearest feature, in CRS
units, without clipping the source to the grid — so a town outside the grid
still counts. On the LuccME Lab15 grid this reproduces the original distance
fields, which were computed the same way.
"""

import warnings

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point

from disscube import CubeClient, Derivation, GridSpec, SpatialSource

CRS = "EPSG:4618"
RES = 0.0045
BBOX = [-54.8415, -3.5865, -54.459, -3.168]
GRID = GridSpec(id="g", type="local", crs=CRS, resolution=RES, bbox=BBOX)
MOJUI = (-54.606, -2.691)                         # outside the grid, ~0.5° north


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    c.register_grid(GRID)
    return c


def _vector(cube, tmp_path, sid, geoms):
    path = tmp_path / f"{sid}.geojson"
    gpd.GeoDataFrame(geometry=geoms, crs=CRS).to_file(path, driver="GeoJSON")
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="vector", crs=CRS,
                                               asset_url=str(path)))


def test_distance_to_a_point_outside_the_grid_is_exact(cube, tmp_path):
    _vector(cube, tmp_path, "town", [Point(*MOJUI)])
    cube.derive_declarative(Derivation(target="d", source_id="town", operator="distance"), grid_id="g")
    got = cube.load("d", grid_id="g").values
    xx, yy = np.meshgrid(GRID.xs, GRID.ys)
    assert np.allclose(got, np.hypot(xx - MOJUI[0], yy - MOJUI[1]))


def test_distance_to_a_line(cube, tmp_path):
    x = BBOX[0] + 10.5 * RES                      # a north-south line through column 10's centre
    _vector(cube, tmp_path, "road", [LineString([(x, -4.0), (x, -2.5)])])
    cube.derive_declarative(Derivation(target="d", source_id="road", operator="distance"), grid_id="g")
    got = cube.load("d", grid_id="g").values
    assert np.allclose(got[:, 10], 0)
    assert np.allclose(got[:, 13], 3 * RES)


def test_min_distance_without_features_inside_returns_nan(cube, tmp_path):
    _vector(cube, tmp_path, "town", [Point(*MOJUI)])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cube.derive_declarative(Derivation(target="d", source_id="town", operator="min_distance"),
                                grid_id="g")
    assert np.isnan(cube.load("d", grid_id="g").values).all()
    assert any("use operator 'distance'" in str(w.message) for w in caught)


def test_distance_and_min_distance_agree_inside_the_grid(cube, tmp_path):
    _vector(cube, tmp_path, "pt", [Point(BBOX[0] + 40.5 * RES, BBOX[3] - 40.5 * RES)])
    for op in ("distance", "min_distance"):
        cube.derive_declarative(Derivation(target=op, source_id="pt", operator=op), grid_id="g")
    exact = cube.load("distance", grid_id="g").values
    approx = cube.load("min_distance", grid_id="g").values
    assert np.max(np.abs(exact - approx)) <= RES * 1.01
