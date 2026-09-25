"""
Tests for the options a pipeline can declare instead of preparing its inputs in
a script: operator ``params`` (``distance`` in another CRS, ``subcells``), the
``area`` operator, ``fill = "nearest"``, file sources with ``read`` options,
``nodata`` and a NetCDF ``variable``, ``union`` sources, and the grid's
transform on ``to_lucc_data``.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import shapely
import xarray as xr
from pydantic import ValidationError
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from disscube import CubeClient, Derivation, GridSpec, SpatialDerivation, SpatialSource, Variable
from disscube.config import plan, run
from disscube.config.runner import PipelineError

GEO = GridSpec(id="geo", type="local", crs="EPSG:4326", resolution=0.1, bbox=[-50.0, -10.0, -49.0, -9.0])
UTM = GridSpec(id="utm", type="local", crs="EPSG:31983", resolution=300, bbox=[0, 0, 3000, 3000])


@pytest.fixture
def cube(tmp_path):
    c = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    c.register_grid(GEO)
    c.register_grid(UTM)
    return c


def _vector(cube, tmp_path, sid, geoms, crs, **columns):
    path = tmp_path / f"{sid}.gpkg"
    gpd.GeoDataFrame(columns, geometry=geoms, crs=crs).to_file(path, driver="GPKG")
    cube.register_spatial_source(SpatialSource(id=sid, name=sid, format="vector", crs=crs, asset_url=str(path)))
    return path


def _derive(cube, grid, sid, operator, **kw):
    cube.derive_declarative(Derivation(target="v", source_id=sid, operator=operator, **kw), grid_id=grid)
    return cube.load("v", grid_id=grid).values


# ---------------------------------------------------------------------------
# params, and the hash
# ---------------------------------------------------------------------------


def test_unknown_param_is_rejected():
    with pytest.raises(ValidationError, match="does not take"):
        Derivation(target="v", source_id="s", operator="mean", params={"crs": "EPSG:5880"})


def test_params_and_fill_enter_the_hash_only_when_set():
    plain = SpatialDerivation(
        source_id="s", grid_id="g", role="driver", variables=[Variable(name="v", operator="distance")]
    )
    legacy = SpatialDerivation(
        source_id="s",
        grid_id="g",
        role="driver",
        variables=[Variable(name="v", operator="distance", params={}, fill=None)],
    )
    in_metres = SpatialDerivation(
        source_id="s",
        grid_id="g",
        role="driver",
        variables=[Variable(name="v", operator="distance", params={"crs": "EPSG:5880"})],
    )
    filled = SpatialDerivation(
        source_id="s", grid_id="g", role="driver", variables=[Variable(name="v", operator="distance", fill="nearest")]
    )
    assert plain.spec_hash() == legacy.spec_hash()
    assert len({plain.spec_hash(), in_metres.spec_hash(), filled.spec_hash()}) == 3


# ---------------------------------------------------------------------------
# distance in another CRS
# ---------------------------------------------------------------------------


def test_distance_in_a_projected_crs_is_in_metres(cube, tmp_path):
    town = (-49.55, -9.45)
    _vector(cube, tmp_path, "town", [Point(*town)], "EPSG:4326")
    got = _derive(cube, "geo", "town", "distance", params={"crs": "EPSG:5880"})

    to_m = Transformer.from_crs("EPSG:4326", "EPSG:5880", always_xy=True)
    xx, yy = np.meshgrid(GEO.xs, GEO.ys)
    cx, cy = to_m.transform(xx, yy)
    tx, ty = to_m.transform(*town)
    np.testing.assert_allclose(got, np.hypot(cx - tx, cy - ty), rtol=1e-9)
    assert got.max() > 50_000  # metres, not degrees


# ---------------------------------------------------------------------------
# area
# ---------------------------------------------------------------------------


def test_area_is_the_covered_share_of_each_cell(cube, tmp_path):
    # one polygon over cells (0,0) whole and half of (0,1); a second one
    # overlapping the first, so the overlap counts once
    polys = [box(0, 2700, 450, 3000), box(0, 2700, 300, 3000)]
    _vector(cube, tmp_path, "pa", polys, "EPSG:31983")
    got = _derive(cube, "utm", "pa", "area")
    assert got[0, 0] == pytest.approx(1.0)
    assert got[0, 1] == pytest.approx(0.5)
    assert got[1:, :].sum() == 0 and got[0, 2:].sum() == 0


def test_area_of_a_polygon_inside_one_cell(cube, tmp_path):
    _vector(cube, tmp_path, "pa", [box(1250, 1250, 1350, 1300)], "EPSG:31983")
    got = _derive(cube, "utm", "pa", "area")
    assert got.sum() == pytest.approx(100 * 50 / 300**2)


def test_area_rejects_rasters(cube, tmp_path):
    from disscube.operators.zonal import AreaOperator

    with pytest.raises(TypeError, match="vector"):
        AreaOperator().compute(
            xr.DataArray(np.zeros((2, 2)), dims=("y", "x")), Variable(name="v", operator="area"), UTM
        )


# ---------------------------------------------------------------------------
# fill
# ---------------------------------------------------------------------------


def _raster(path, arr, transform, crs="EPSG:31983", nodata=None):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)
    return path


def test_fill_nearest_reaches_the_cells_the_source_misses(cube, tmp_path):
    # a raster covering only the left half of the grid, 1 on column 0 and 5 elsewhere
    arr = np.full((10, 5), 5.0, dtype="float32")
    arr[:, 0] = 1.0
    _raster(tmp_path / "half.tif", arr, from_origin(0, 3000, 300, 300))
    cube.register_spatial_source(
        SpatialSource(id="half", name="half", format="raster", crs="EPSG:31983", asset_url=str(tmp_path / "half.tif"))
    )
    plain = _derive(cube, "utm", "half", "mean")
    assert np.isnan(plain[:, 5:]).all()
    cube.derive_declarative(Derivation(target="w", source_id="half", operator="mean", fill="nearest"), grid_id="utm")
    filled = cube.load("w", grid_id="utm").values
    np.testing.assert_array_equal(filled[:, :5], plain[:, :5])
    np.testing.assert_array_equal(filled[:, 5:], 5.0)


# ---------------------------------------------------------------------------
# subcells
# ---------------------------------------------------------------------------


def test_subcells_caps_the_fine_grid(tmp_path):
    import rioxarray

    from disscube.pipeline.aligner import GridAligner

    arr = np.ones((300, 300), dtype="uint8")
    _raster(tmp_path / "fine.tif", arr, from_origin(0, 3000, 10, 10), nodata=0)
    band = rioxarray.open_rasterio(tmp_path / "fine.tif").isel(band=0)
    assert GridAligner()._align_fine(band, UTM).shape == (300, 300)  # 30 × 30 per cell
    assert GridAligner()._align_fine(band, UTM, 4).shape == (40, 40)  # capped at 4 × 4


def test_percentage_with_subcells(cube, tmp_path):
    arr = np.ones((300, 300), dtype="uint8")
    arr[:, 150:] = 2
    _raster(tmp_path / "cls.tif", arr, from_origin(0, 3000, 10, 10), nodata=0)
    cube.register_spatial_source(
        SpatialSource(id="cls", name="cls", format="raster", crs="EPSG:31983", asset_url=str(tmp_path / "cls.tif"))
    )
    got = _derive(cube, "utm", "cls", "percentage", class_code=1, params={"subcells": 4})
    np.testing.assert_array_equal(got[:, :5], 1.0)
    np.testing.assert_array_equal(got[:, 5:], 0.0)


# ---------------------------------------------------------------------------
# pipeline files: read options, nodata, NetCDF variables, unions
# ---------------------------------------------------------------------------

GRID_TOML = """
schema = 1
[grid]
name = "g"
crs = "EPSG:31983"
bbox = [0, 0, 3000, 3000]
resolution = 300
"""


def _toml(tmp_path, body):
    path = tmp_path / "p.toml"
    path.write_text(GRID_TOML + body, encoding="utf-8")
    return path


def _grid_values(report, name):
    cube = CubeClient(catalog=str(report.workspace / "catalog.db"), store=str(report.workspace / "store"))
    return cube.load(name, grid_id="g").values


def test_read_where_selects_features_and_changes_the_product(tmp_path):
    roads = [LineString([(150, 0), (150, 3000)]), LineString([(2850, 0), (2850, 3000)])]
    gpd.GeoDataFrame({"surface": ["paved", "dirt"]}, geometry=roads, crs="EPSG:31983").to_file(
        tmp_path / "roads.gpkg", driver="GPKG"
    )
    path = _toml(
        tmp_path,
        """
[[source]]
id = "paved"
type = "file"
path = "roads.gpkg"
read = { where = "surface = 'paved'" }

[[source]]
id = "all"
type = "file"
path = "roads.gpkg"

[[derive]]
target = "d_paved"
source = "paved"
operator = "distance"

[[derive]]
target = "d_all"
source = "all"
operator = "distance"
""",
    )
    report = run(path, workspace=tmp_path / "ws")
    paved, every = _grid_values(report, "d_paved"), _grid_values(report, "d_all")
    assert paved[0, 9] == pytest.approx(2700)  # only the paved road, on column 0
    assert every[0, 9] == pytest.approx(0)
    hashes = {d["target"]: d["spec_hash"] for d in report.derived}
    assert hashes["d_paved"] != hashes["d_all"]


def test_nodata_on_a_file_source(tmp_path):
    arr = np.full((10, 10), 7.0, dtype="float32")
    arr[:, 5:] = -9.99e8
    _raster(tmp_path / "mcwd.tif", arr, from_origin(0, 3000, 300, 300))
    path = _toml(
        tmp_path,
        """
[[source]]
id = "mcwd"
type = "file"
path = "mcwd.tif"
nodata = -9.99e8

[[derive]]
target = "mcwd"
source = "mcwd"
operator = "mean"
""",
    )
    got = _grid_values(run(path, workspace=tmp_path / "ws"), "mcwd")
    np.testing.assert_array_equal(got[:, :5], 7.0)
    assert np.isnan(got[:, 5:]).all()


def test_netcdf_variable_is_read_as_a_raster(tmp_path):
    # CF coordinates, rows stored south → north as in most NetCDFs; GDAL turns them north-up
    ys = 150 + 300 * np.arange(10)
    xs = 150 + 300 * np.arange(10)
    veg = np.arange(100, dtype="float64").reshape(10, 10)
    ds = xr.Dataset({"veg": (("y", "x"), veg[::-1]), "other": (("y", "x"), -veg)}, coords={"y": ys, "x": xs})
    ds.x.attrs = {"standard_name": "projection_x_coordinate", "units": "m"}
    ds.y.attrs = {"standard_name": "projection_y_coordinate", "units": "m"}
    ds = ds.expand_dims(time=[0]).transpose("time", "y", "x")
    ds.time.attrs = {"units": "years since 2000-1-1 00:00:00", "calendar": "proleptic_gregorian"}
    ds = ds.rio.write_crs("EPSG:31983")
    ds.to_netcdf(tmp_path / "lc.nc")
    path = _toml(
        tmp_path,
        """
[[source]]
id = "veg"
type = "file"
path = "lc.nc"
variable = "veg"

[[derive]]
target = "veg"
source = "veg"
operator = "mean"
role = "land_use"
""",
    )
    got = _grid_values(run(path, workspace=tmp_path / "ws"), "veg")
    np.testing.assert_allclose(got, veg)


def test_union_joins_vector_sources(tmp_path):
    gpd.GeoDataFrame({"k": [1]}, geometry=[LineString([(150, 0), (150, 3000)])], crs="EPSG:31983").to_file(
        tmp_path / "a.gpkg", driver="GPKG"
    )
    # the second part in another CRS: the union reprojects it to the first's
    far = Transformer.from_crs("EPSG:31983", "EPSG:4326", always_xy=True)
    line = LineString([far.transform(2850, 0), far.transform(2850, 3000)])
    gpd.GeoDataFrame({"k": [2]}, geometry=[line], crs="EPSG:4326").to_file(tmp_path / "b.gpkg", driver="GPKG")
    path = _toml(
        tmp_path,
        """
[[source]]
id = "a"
type = "file"
path = "a.gpkg"

[[source]]
id = "b"
type = "file"
path = "b.gpkg"

[[source]]
id = "roads"
type = "union"
of = ["a", "b"]

[[derive]]
target = "d"
source = "roads"
operator = "distance"
""",
    )
    got = _grid_values(run(path, workspace=tmp_path / "ws"), "d")
    assert got[0, 0] == pytest.approx(0, abs=1e-6)
    assert got[0, 9] == pytest.approx(0, abs=1e-3)
    assert got[0, 4] == pytest.approx(1200, abs=1e-3)


def test_union_needs_earlier_vector_sources(tmp_path):
    _raster(tmp_path / "r.tif", np.ones((10, 10), dtype="float32"), from_origin(0, 3000, 300, 300))
    body = """
[[source]]
id = "r"
type = "file"
path = "r.tif"

[[source]]
id = "u"
type = "union"
of = {of}
"""
    with pytest.raises(PipelineError, match="declared before"):
        plan(_toml(tmp_path, body.format(of='["later", "r"]')))
    with pytest.raises(PipelineError, match="not a vector file"):
        plan(_toml(tmp_path, body.format(of='["r", "r"]')))


def test_options_on_the_wrong_kind_of_file(tmp_path):
    _raster(tmp_path / "r.tif", np.ones((10, 10), dtype="float32"), from_origin(0, 3000, 300, 300))
    path = _toml(
        tmp_path,
        """
[[source]]
id = "r"
type = "file"
path = "r.tif"
read = { where = "x = 1" }
""",
    )
    with pytest.raises(PipelineError, match="vector files"):
        run(path, workspace=tmp_path / "ws")


def test_derive_params_and_fill_in_a_pipeline(tmp_path):
    gpd.GeoDataFrame(geometry=[Point(1500, 1500)], crs="EPSG:31983").to_file(tmp_path / "p.gpkg", driver="GPKG")
    path = _toml(
        tmp_path,
        """
[[source]]
id = "p"
type = "file"
path = "p.gpkg"

[[derive]]
target = "d"
source = "p"
operator = "distance"
params = { crs = "EPSG:5880" }
fill = "nearest"
""",
    )
    p = plan(path)
    assert p.derives[0].params == {"crs": "EPSG:5880"} and p.derives[0].fill == "nearest"
    assert "crs" in p.summary()
    got = _grid_values(run(p, workspace=tmp_path / "ws"), "d")
    assert np.nanmin(got) > 0 and not np.isnan(got).any()


def test_unknown_derive_param_fails_the_plan(tmp_path):
    _raster(tmp_path / "r.tif", np.ones((10, 10), dtype="float32"), from_origin(0, 3000, 300, 300))
    path = _toml(
        tmp_path,
        """
[[source]]
id = "r"
type = "file"
path = "r.tif"

[[derive]]
target = "v"
source = "r"
operator = "mean"
params = { subcells = 4 }
""",
    )
    with pytest.raises(PipelineError, match="does not take subcells"):
        plan(path)


# ---------------------------------------------------------------------------
# to_lucc_data
# ---------------------------------------------------------------------------


def test_to_lucc_data_carries_the_grid_transform(cube, tmp_path):
    _vector(cube, tmp_path, "town", [Point(-49.55, -9.45)], "EPSG:4326")
    _derive(cube, "geo", "town", "distance")
    for grid_id in ("geo", None):
        backend = cube.to_lucc_data(["v"], grid_id=grid_id)
        assert backend.transform == GEO.transform


def test_area_of_a_polygon_with_many_vertices(cube, tmp_path):
    # > 4096 vertices: cut into quadrants before the cells are intersected
    circle = Point(1500, 1500).buffer(1400, quad_segs=2000)
    _vector(cube, tmp_path, "c", [circle], "EPSG:31983")
    got = _derive(cube, "utm", "c", "area")
    xmin, ymax = np.meshgrid(UTM.bbox[0] + 300 * np.arange(10), UTM.bbox[3] - 300 * np.arange(10))
    cells = shapely.box(xmin, ymax - 300, xmin + 300, ymax)
    np.testing.assert_allclose(got, shapely.area(shapely.intersection(cells, circle)) / 300**2, atol=1e-9)


# ---------------------------------------------------------------------------
# sources in one file, derivations in another, sharing a workspace
# ---------------------------------------------------------------------------

SOURCES_ONLY = """
schema = 1
extent = [-49.0, -9.0, -48.0, -8.0]

[[source]]
id = "r"
type = "file"
path = "r.tif"
"""

DERIVE_BLOCK = """
[[derive]]
target = "v"
source = "r"
operator = "mean"
"""
# top-level keys come before the [grid] table
DERIVE_ONLY = GRID_TOML.replace("schema = 1\n", "schema = 1\nsources_from_catalog = true\n") + DERIVE_BLOCK


def test_sources_in_one_file_derivations_in_another(tmp_path):
    _raster(tmp_path / "r.tif", np.full((10, 10), 3.0, dtype="float32"), from_origin(0, 3000, 300, 300))
    (tmp_path / "sources.toml").write_text(SOURCES_ONLY)
    (tmp_path / "derive.toml").write_text(DERIVE_ONLY)
    ws = tmp_path / "ws"

    sources = run(tmp_path / "sources.toml", workspace=ws)
    assert sources.grid_id is None and [s["id"] for s in sources.derived] == []
    p = plan(tmp_path / "derive.toml")
    assert p.catalog_sources == ["r"] and "workspace catalog" in p.summary()
    report = run(p, workspace=ws)
    np.testing.assert_array_equal(_grid_values(report, "v"), 3.0)
    assert (ws / "runs" / "sources.json").exists() and (ws / "runs" / "derive.json").exists()


def test_derivation_from_a_source_not_in_the_catalog(tmp_path):
    (tmp_path / "derive.toml").write_text(DERIVE_ONLY)
    with pytest.raises(PipelineError, match="nor in the catalog"):
        run(tmp_path / "derive.toml", workspace=tmp_path / "ws")


def test_grid_or_extent_is_required(tmp_path):
    (tmp_path / "a.toml").write_text(SOURCES_ONLY.replace("extent = [-49.0, -9.0, -48.0, -8.0]\n", ""))
    with pytest.raises(PipelineError, match="extent"):
        plan(tmp_path / "a.toml")
    (tmp_path / "b.toml").write_text(SOURCES_ONLY + DERIVE_BLOCK)
    with pytest.raises(PipelineError, match="needs a \\[grid\\]"):
        plan(tmp_path / "b.toml")


def test_catalog_sources_are_opt_in(tmp_path):
    (tmp_path / "d.toml").write_text(GRID_TOML + DERIVE_BLOCK)
    with pytest.raises(PipelineError, match="unknown source 'r'"):
        plan(tmp_path / "d.toml")
