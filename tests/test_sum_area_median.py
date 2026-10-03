"""
Tests for ``sum`` over vectors (plain and areal-weighted), ``median`` and the
``ddof`` parameter of ``std``.

Areal weighting must conserve the polygon's value: the cells of a polygon that
lies wholly inside the grid add up to its attribute, and a polygon crossing the
border distributes only the share that falls inside.
"""

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import rioxarray  # noqa: F401
from rasterio.transform import from_bounds
from shapely.geometry import LineString, Point, box

from disscube import CubeClient, Derivation, GridSpec, SpatialSource
from disscube.models import SpatialDerivation, Variable
from disscube.models import SpatialSource as RasterSource
from disscube.pipeline import PipelineContext
from disscube.pipeline.aggregator import Aggregator
from disscube.pipeline.aligner import GridAligner

CRS = "EPSG:31982"
GRID = GridSpec(id="g", type="local", crs=CRS, resolution=100, bbox=[0, 0, 400, 300])  # 3 rows x 4 cols


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    c.register_grid(GRID)
    return c


def _vector(cube, tmp_path, sid, geoms, **columns):
    path = tmp_path / f"{sid}.geojson"
    gpd.GeoDataFrame(columns, geometry=geoms, crs=CRS).to_file(path, driver="GeoJSON")
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="vector", crs=CRS, asset_url=str(path)))


def _sum(cube, target="pop", source="s", **params):
    cube.derive_declarative(Derivation(target=target, source_id=source, operator="sum", params=params), grid_id="g")
    return cube.load(target, grid_id="g").values


# --------------------------------------------------------------------------- #
# sum, area = true
# --------------------------------------------------------------------------- #

def test_area_sum_splits_a_polygon_in_proportion_to_the_area(cube, tmp_path):
    # 200 x 100 polygon: covers cell (row 1, col 0) fully and (row 1, col 1) half
    _vector(cube, tmp_path, "s", [box(0, 100, 150, 200)], pop=[300.0])
    got = _sum(cube, area=True)
    assert got[1, 0] == pytest.approx(200.0)       # 100 x 100 of 150 x 100
    assert got[1, 1] == pytest.approx(100.0)       # 50 x 100 of 150 x 100
    assert got.sum() == pytest.approx(300.0)


def test_area_sum_conserves_the_total_over_many_polygons(cube, tmp_path):
    polys = [box(10, 10, 390, 150), box(40, 160, 260, 290), box(100, 100, 300, 250)]  # the last overlaps both
    _vector(cube, tmp_path, "s", polys, pop=[1000.0, 250.0, 40.0])
    assert _sum(cube, area=True).sum() == pytest.approx(1290.0)


def test_area_sum_polygon_crossing_the_border_keeps_its_full_denominator(cube, tmp_path):
    # half of the polygon (x in 300..500) lies outside the grid (x <= 400)
    _vector(cube, tmp_path, "s", [box(300, 0, 500, 100)], pop=[80.0])
    got = _sum(cube, area=True)
    assert got.sum() == pytest.approx(40.0)         # only the inside half is distributed
    assert got[2, 3] == pytest.approx(40.0)


def test_area_sum_with_a_custom_column(cube, tmp_path):
    _vector(cube, tmp_path, "s", [box(0, 0, 100, 100)], pop=[5.0], herd=[7.0])
    got = _sum(cube, target="cattle", area=True, column="herd")
    assert got[2, 0] == pytest.approx(7.0)


def test_area_sum_rejects_non_polygons(cube, tmp_path):
    _vector(cube, tmp_path, "s", [LineString([(0, 0), (300, 0)])], pop=[1.0])
    with pytest.raises(ValueError, match="polygon"):
        _sum(cube, area=True)


def test_vector_sum_without_the_column_is_an_error(cube, tmp_path):
    _vector(cube, tmp_path, "s", [box(0, 0, 100, 100)], other=[1.0])
    with pytest.raises(ValueError, match="pop"):
        _sum(cube)


# --------------------------------------------------------------------------- #
# sum, area = false
# --------------------------------------------------------------------------- #

def test_plain_sum_adds_points_in_their_cell(cube, tmp_path):
    _vector(cube, tmp_path, "s", [Point(50, 250), Point(60, 260), Point(350, 50)], pop=[1.0, 2.0, 10.0])
    got = _sum(cube)
    assert got[0, 0] == 3.0 and got[2, 3] == 10.0
    assert got.sum() == 13.0


def test_plain_sum_gives_a_polygon_to_every_cell_it_touches(cube, tmp_path):
    _vector(cube, tmp_path, "s", [box(50, 50, 150, 150)], pop=[4.0])   # straddles 4 cells
    got = _sum(cube)
    assert got.sum() == 16.0 and (got == 4.0).sum() == 4


# --------------------------------------------------------------------------- #
# median and std ddof (raster, via aligner + aggregator)
# --------------------------------------------------------------------------- #

def _raster_run(tmp_path, array, var, nodata=None):
    rows, cols = array.shape
    path = tmp_path / "r.tif"
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
                       crs=CRS, transform=from_bounds(0, 0, 100, 100, cols, rows), nodata=nodata) as dst:
        dst.write(array.astype(np.float32), 1)
    grid = GridSpec(id="G1", type="local", crs=CRS, resolution=100, bbox=[0, 0, 100, 100])
    src = RasterSource(id="S1", name="S1", format="raster", asset_url=str(path), crs=CRS)
    ctx = PipelineContext(source=src, grid=grid, derivation=SpatialDerivation(
        source_id="S1", grid_id="G1", role="t", variables=[var]))
    GridAligner().execute(ctx)
    Aggregator().execute(ctx)
    return ctx.data[var.name]


def test_median_odd_and_even(tmp_path):
    odd = _raster_run(tmp_path, np.array([[1, 2, 100]], dtype=np.float32), Variable(name="m", operator="median"))
    assert odd.values[0, 0] == pytest.approx(2.0)
    even = _raster_run(tmp_path, np.array([[1, 2], [4, 100]], dtype=np.float32), Variable(name="m", operator="median"))
    assert even.values[0, 0] == pytest.approx(3.0)


def test_median_is_robust_to_an_outlier_where_the_mean_is_not(tmp_path):
    a = np.array([[10, 11], [12, 5000]], dtype=np.float32)
    med = _raster_run(tmp_path, a, Variable(name="m", operator="median")).values[0, 0]
    mean = _raster_run(tmp_path, a, Variable(name="m", operator="mean")).values[0, 0]
    assert med == pytest.approx(11.5) and mean > 1000


def test_median_skips_nodata_and_reports_coverage(tmp_path):
    out = _raster_run(tmp_path, np.array([[1, -1], [3, 9]], dtype=np.float32), Variable(name="m", operator="median"), nodata=-1)
    assert out.values[0, 0] == pytest.approx(3.0)
    assert out["coverage_purity"].values[0, 0] == pytest.approx(0.75)


def test_std_ddof_default_is_population_and_1_is_sample(tmp_path):
    a = np.array([[0, 2], [4, 6]], dtype=np.float32)
    pop = _raster_run(tmp_path, a, Variable(name="s", operator="std")).values[0, 0]
    smp = _raster_run(tmp_path, a, Variable(name="s", operator="std", params={"ddof": 1})).values[0, 0]
    assert pop == pytest.approx(np.std([0, 2, 4, 6]))
    assert smp == pytest.approx(np.std([0, 2, 4, 6], ddof=1))


def test_std_sample_of_a_single_pixel_is_nan(tmp_path):
    out = _raster_run(tmp_path, np.array([[5, -1], [-1, -1]], dtype=np.float32),
                      Variable(name="s", operator="std", params={"ddof": 1}), nodata=-1)
    assert np.isnan(out.values[0, 0])


def test_std_rejects_a_bad_ddof(tmp_path):
    with pytest.raises(ValueError, match="ddof"):
        _raster_run(tmp_path, np.zeros((2, 2), dtype=np.float32), Variable(name="s", operator="std", params={"ddof": 2}))
