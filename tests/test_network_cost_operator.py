"""
Tests for the NetworkCostOperator and GpmNetworkOperator (TerraME GPM connectivity).
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


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    c.register_grid(GRID)
    return c


def _roads(cube, tmp_path, sid, lines, statuses=None):
    path = tmp_path / f"{sid}.geojson"
    data = {"geometry": lines}
    if statuses is not None:
        data["status"] = statuses
    gpd.GeoDataFrame(data, crs=CRS).to_file(path, driver="GeoJSON")
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="vector", crs=CRS,
                                               asset_url=str(path)))


def test_network_cost_linear_corridor(cube, tmp_path):
    y_road = BBOX[1] + (BBOX[3] - BBOX[1]) / 2
    road = LineString([(BBOX[0], y_road), (BBOX[2], y_road)])
    _roads(cube, tmp_path, "roads", [road], ["paved"])

    target = [BBOX[0], y_road]
    cube.derive_declarative(
        Derivation(
            target="conn",
            source_id="roads",
            operator="network_cost",
            params={
                "targets": [target],
                "inside_paved": 1.0,
                "outside": 2.0,
            },
        ),
        grid_id="g",
    )
    result = cube.load("conn", grid_id="g").values
    assert not np.isnan(result).any()
    assert (result >= 0).all()


def test_gpm_network_alias(cube, tmp_path):
    y_road = BBOX[1] + (BBOX[3] - BBOX[1]) / 2
    road = LineString([(BBOX[0], y_road), (BBOX[2], y_road)])
    _roads(cube, tmp_path, "roads", [road])

    target = [BBOX[0], y_road]
    cube.derive_declarative(
        Derivation(
            target="conn_gpm",
            source_id="roads",
            operator="gpm_network",
            params={
                "targets": [target],
                "outside": 1.5,
            },
        ),
        grid_id="g",
    )
    result = cube.load("conn_gpm", grid_id="g").values
    assert not np.isnan(result).any()


def test_network_cost_unpaved_impedance(cube, tmp_path):
    y1 = BBOX[1] + (BBOX[3] - BBOX[1]) * 0.25
    y2 = BBOX[1] + (BBOX[3] - BBOX[1]) * 0.75
    road_paved = LineString([(BBOX[0], y1), (BBOX[2], y1)])
    road_unpaved = LineString([(BBOX[0], y2), (BBOX[2], y2)])

    _roads(cube, tmp_path, "roads_multi", [road_paved, road_unpaved], ["paved", "unpaved"])

    targets = [[BBOX[0], y1], [BBOX[0], y2]]
    cube.derive_declarative(
        Derivation(
            target="conn_imp",
            source_id="roads_multi",
            operator="network_cost",
            params={
                "targets": targets,
                "status_column": "status",
                "inside_paved": 0.5,
                "inside_unpaved": 2.0,
                "outside": 1.0,
            },
        ),
        grid_id="g",
    )
    result = cube.load("conn_imp", grid_id="g").values
    assert not np.isnan(result).any()


def test_network_cost_multiple_destinations(cube, tmp_path):
    y_road = BBOX[1] + (BBOX[3] - BBOX[1]) / 2
    road = LineString([(BBOX[0], y_road), (BBOX[2], y_road)])
    _roads(cube, tmp_path, "roads_corridor", [road], ["paved"])

    targets = [[BBOX[0], y_road], [BBOX[2], y_road]]
    cube.derive_declarative(
        Derivation(
            target="conn_multi_dest",
            source_id="roads_corridor",
            operator="network_cost",
            params={
                "targets": targets,
                "inside_paved": 1.0,
                "outside": 1.0,
            },
        ),
        grid_id="g",
    )
    result = cube.load("conn_multi_dest", grid_id="g").values
    half_len = (BBOX[2] - BBOX[0]) / 2
    row_centre = result.shape[0] // 2
    col_centre = result.shape[1] // 2
    assert result[row_centre, col_centre] <= half_len * 1.5
    