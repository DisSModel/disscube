"""
Tests for the NetworkCostOperator and GpmNetworkOperator (TerraME GPM connectivity).
"""

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
    

def test_network_cost_unreachable_cells_are_nan_not_inf(cube, tmp_path):
    """A road component without a target must give NaN (with a warning), never inf."""
    y1 = BBOX[1] + (BBOX[3] - BBOX[1]) * 0.25
    y2 = BBOX[1] + (BBOX[3] - BBOX[1]) * 0.75
    reachable = LineString([(BBOX[0], y1), (BBOX[2], y1)])
    isolated = LineString([(BBOX[0], y2), (BBOX[2], y2)])
    _roads(cube, tmp_path, "roads_split", [reachable, isolated])

    with pytest.warns(RuntimeWarning, match="cannot reach any target"):
        cube.derive_declarative(
            Derivation(
                target="conn_split",
                source_id="roads_split",
                operator="network_cost",
                params={"targets": [[BBOX[0], y1]]},
            ),
            grid_id="g",
        )
    result = cube.load("conn_split", grid_id="g").values
    assert not np.isinf(result).any()
    # grids are north-up: row 0 is nearest the isolated (northern) road, the last row the target road
    assert np.isnan(result[0]).all()
    assert np.isfinite(result[-1]).all()


# ---------------------------------------------------------------------------
# Value tests on a metric grid: 10 x 10 cells of 1 km, so every expected number
# can be worked out by hand. Row 0 is north; the road runs along y = 5000 m, the
# line between rows 4 and 5, so cells of rows 4 and 5 are 500 m from it.
# ---------------------------------------------------------------------------

PCRS = "EPSG:31983"
PGRID = GridSpec(id="p", type="local", crs=PCRS, resolution=1000.0, bbox=[0.0, 0.0, 10000.0, 10000.0])
ROAD = LineString([(0, 5000), (10000, 5000)])
PORT = [0, 5000]


@pytest.fixture
def pcube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "pc.db"), store=str(tmp_path / "ps"))
    c.register_grid(PGRID)
    return c


def _metric_roads(cube, tmp_path, sid, lines, **columns):
    path = tmp_path / f"{sid}.geojson"
    gpd.GeoDataFrame({"geometry": lines, **columns}, crs=PCRS).to_file(path, driver="GeoJSON")
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="vector", crs=PCRS,
                                               asset_url=str(path)))


def _derive(cube, sid, **params):
    cube.derive_declarative(
        Derivation(target="t", source_id=sid, operator="network_cost", params=params),
        grid_id="p",
    )
    return cube.load("t", grid_id="p").values


def test_corridor_exact_values(pcube, tmp_path):
    """Cell at column c: 500 m off the road + (c * 1000 + 500) m along it."""
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    out = _derive(pcube, "r", targets=[PORT])
    expected = 500.0 + (np.arange(10) * 1000.0 + 500.0)
    np.testing.assert_allclose(out[4], expected)
    np.testing.assert_allclose(out[5], expected)  # symmetric about the road


def test_outside_factor_scales_only_the_off_road_leg(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    out = _derive(pcube, "r", targets=[PORT], outside=2.0)
    np.testing.assert_allclose(out[4], 2 * 500.0 + (np.arange(10) * 1000.0 + 500.0))


def test_unit_scale_converts_units(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    metres = _derive(pcube, "r", targets=[PORT])
    km = _derive(pcube, "r", targets=[PORT], unit_scale=1e-3)
    np.testing.assert_allclose(km, metres / 1000.0)


def test_cost_column_overrides_the_status_factor(pcube, tmp_path):
    """A per-feature multiplier doubles the on-road leg and leaves the off-road one alone."""
    _metric_roads(pcube, tmp_path, "r", [ROAD], custo_ajus=[2.0], status=["paved"])
    out = _derive(pcube, "r", targets=[PORT], cost_column="custo_ajus",
                  status_column="status", inside_paved=1.0)
    np.testing.assert_allclose(out[4], 500.0 + 2.0 * (np.arange(10) * 1000.0 + 500.0))


def test_status_column_unpaved_factor(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD], status=["unpaved"])
    out = _derive(pcube, "r", targets=[PORT], status_column="status",
                  inside_paved=1.0, inside_unpaved=3.0)
    np.testing.assert_allclose(out[4], 500.0 + 3.0 * (np.arange(10) * 1000.0 + 500.0))


def test_entrance_segment_vs_vertex(pcube, tmp_path):
    """The road has only two vertices, at x = 0 and x = 10000.

    'segment' joins a cell at the foot of the perpendicular, 'vertex' (the TerraME rule)
    at the nearest vertex, so a cell in the middle goes to the far end and back.
    """
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    seg = _derive(pcube, "r", targets=[PORT], entrance="segment")
    vert = _derive(pcube, "r", targets=[PORT], entrance="vertex")
    assert seg[4, 5] == pytest.approx(500.0 + 5500.0)
    # nearest vertex of the centre (5500, 4500) is (10000, 5000): 4500 across, 500 up
    # (off-road), then 10000 m back along the road to the port at x = 0
    assert vert[4, 5] == pytest.approx(np.hypot(4500.0, 500.0) + 10000.0)
    assert vert[4, 5] > seg[4, 5]  # mid-road cell: the vertex rule detours to the far end


def test_multilinestring_is_one_connected_road(pcube, tmp_path):
    from shapely.geometry import MultiLineString

    halves = MultiLineString([[(0, 5000), (5000, 5000)], [(5000, 5000), (10000, 5000)]])
    _metric_roads(pcube, tmp_path, "r", [halves])
    out = _derive(pcube, "r", targets=[PORT])
    np.testing.assert_allclose(out[4], 500.0 + (np.arange(10) * 1000.0 + 500.0))


def test_nearest_target_wins(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    out = _derive(pcube, "r", targets=[[0, 5000], [10000, 5000]])
    # symmetric: the two ends see the same cost, and the middle is the costliest column
    np.testing.assert_allclose(out[4], out[4][::-1])
    assert out[4].argmax() in (4, 5)


def test_no_targets_gives_nan_and_warns(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    with pytest.warns(RuntimeWarning, match="no valid targets"):
        out = _derive(pcube, "r", targets=[])
    assert np.isnan(out).all()


def test_targets_from_a_vector_file(pcube, tmp_path):
    _metric_roads(pcube, tmp_path, "r", [ROAD])
    port_file = tmp_path / "port.geojson"
    gpd.GeoDataFrame({"geometry": [Point(*PORT)]}, crs=PCRS).to_file(port_file, driver="GeoJSON")
    from_file = _derive(pcube, "r", targets=str(port_file))
    from_list = _derive(pcube, "r", targets=[PORT])
    np.testing.assert_allclose(from_file, from_list)


def test_network_cost_rejects_a_raster_source(pcube, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "r.tif"
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
                       crs=PCRS, transform=from_origin(0, 10000, 1000, 1000)) as dst:
        dst.write(np.ones((1, 10, 10), dtype="float32"))
    pcube.register_spatial_source(SpatialSource(id="ras", name="ras", format="raster", crs=PCRS,
                                                asset_url=str(path)))
    with pytest.raises(TypeError, match="vector"):
        _derive(pcube, "ras", targets=[PORT])
