"""
Parity with TerraME's *Fill*: DisSCube vs TerraME's own output, cell by cell.

The three Fill examples of TerraME's ``gis`` package (Itaituba, Emas,
Amazônia) ship with the cellular spaces TerraME produced; they are bundled in
``examples/data/terrame/``. Each test derives one attribute with DisSCube on
the same grid and compares it with TerraME's value in every cell.

Passing tests pin the parity reached today; strict ``xfail`` tests record the
known gaps (see docs/terrame_fill_correspondence.md) and will start failing —
as a reminder to promote them — once a gap is closed.
"""

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

DATA = Path(__file__).resolve().parents[1] / "examples" / "data" / "terrame"


@dataclass
class Case:
    cube: CubeClient
    ref: gpd.GeoDataFrame
    rows: np.ndarray
    cols: np.ndarray
    crs: str
    folder: Path

    def source(self, sid: str, fmt: str, name: str) -> None:
        url = f"zip://{self.folder / name}.zip" if fmt == "vector" else str(self.folder / name)
        self.cube.register_spatial_source(SpatialSource(id=sid, name=sid, format=fmt, asset_url=url, crs=self.crs))

    def derive(self, sid: str, name: str, operator: str, class_code: int | None = None, purity: bool = False):
        self.cube.derive(SpatialDerivation(
            source_id=sid, grid_id="g", role="driver",
            variables=[Variable(name=name, operator=operator, class_code=class_code)],
        ))
        da = self.cube.load(name, grid_id="g")
        if purity:
            da = da * da.coords["coverage_purity"]
        return da.to_numpy()[self.rows, self.cols]


def _case(tmp_path_factory, folder: str, epsg: int, resolution: float) -> Case:
    path = DATA / folder
    # The reference cells carry a wrong .prj; their real CRS is the inputs' one.
    ref = gpd.read_file(f"zip://{path / folder}.zip").set_crs(epsg, allow_override=True)
    xmin, _, _, ymax = ref.total_bounds
    tmp = tmp_path_factory.mktemp(folder)
    cube = CubeClient(str(tmp / "catalog.db"), str(tmp / "store"))
    cube.register_grid(GridSpec(id="g", type="local", crs=f"EPSG:{epsg}", resolution=resolution,
                                bbox=ref.total_bounds.tolist()))
    c = ref.geometry.centroid  # TerraME rows grow northwards; DisSCube is north-up
    cols = np.floor((c.x.to_numpy() - xmin) / resolution).astype(int)
    rows = np.floor((ymax - c.y.to_numpy()) / resolution).astype(int)
    return Case(cube, ref, rows, cols, f"EPSG:{epsg}", path)


def _share(ours, theirs, tol):
    return float(np.mean(np.abs(ours - theirs) <= tol))


# ═══ Itaituba — 620 cells, 5 km ═══════════════════════════════════════════════

@pytest.fixture(scope="module")
def itaituba(tmp_path_factory):
    case = _case(tmp_path_factory, "itaituba", 29191, 5_000.0)
    case.source("elev", "raster", "itaituba-elevation.tif")
    case.source("defor", "raster", "itaituba-deforestation.tif")
    case.source("roads", "vector", "itaituba-roads")
    case.source("localities", "vector", "itaituba-localities")
    case.source("census", "vector", "itaituba-census")
    return case


def test_itaituba_average_elevation(itaituba):
    ours = itaituba.derive("elev", "elevation", "mean")
    err = np.abs(ours - itaituba.ref["elevation"].to_numpy())
    assert err.mean() < 1.0      # m (0.89 today)
    assert err.max() < 11.0      # m (10.13 today)


@pytest.mark.parametrize("k", [7, 87, 167, 255])
def test_itaituba_coverage(itaituba, k):
    ours = itaituba.derive("defor", f"defor_{k}", "percentage", class_code=k, purity=True) * 100
    err = np.abs(ours - itaituba.ref[f"defor_{k}"].to_numpy())
    assert err.max() < 0.7       # pp, in every cell (0.64 today)


@pytest.mark.xfail(strict=True, reason="min_distance is a raster approximation; TerraME uses exact polygon distance")
@pytest.mark.parametrize(("sid", "attr"), [("roads", "distroad"), ("localities", "distlocal")])
def test_itaituba_distance(itaituba, sid, attr):
    ours = itaituba.derive(sid, attr, "min_distance")
    assert _share(ours, itaituba.ref[attr].to_numpy(), 100) >= 0.75


@pytest.mark.xfail(strict=True, reason="area-weighted sum over polygons (TerraME sum, area=true) not implemented")
def test_itaituba_area_weighted_sum(itaituba):
    ours = itaituba.derive("census", "population", "sum")
    assert _share(ours, itaituba.ref["population"].to_numpy(), 0.5) >= 0.95


# ═══ Emas — 5 514 cells, 500 m ════════════════════════════════════════════════

@pytest.fixture(scope="module")
def emas(tmp_path_factory):
    case = _case(tmp_path_factory, "emas", 29192, 500.0)
    case.source("firebreak", "vector", "emas-firebreak")
    case.source("river", "vector", "emas-river")
    case.source("cover", "raster", "emas-accumulation.tif")
    return case


@pytest.mark.parametrize("attr", ["firebreak", "river"])
def test_emas_presence(emas, attr):
    ours = emas.derive(attr, attr, "presence")
    assert _share(ours, emas.ref[attr].to_numpy(), 0) >= 0.98   # 98.4 % / 99.7 % today


@pytest.mark.xfail(strict=True, reason="presence rasterizes line centres; TerraME marks every cell a line touches")
@pytest.mark.parametrize("attr", ["firebreak", "river"])
def test_emas_presence_exact(emas, attr):
    ours = emas.derive(attr, f"{attr}_exact", "presence")
    assert _share(ours, emas.ref[attr].to_numpy(), 0) == 1.0


@pytest.mark.parametrize(("attr", "op"), [("maxcover", "max"), ("mincover", "min")])
def test_emas_min_max(emas, attr, op):
    ours = emas.derive("cover", attr, op)
    assert _share(ours, emas.ref[attr].to_numpy(), 0) >= 0.985  # 98.7 % / 99.0 % today


@pytest.mark.xfail(strict=True, reason="min/max count pixels partly inside a cell; TerraME uses pixel centres")
@pytest.mark.parametrize(("attr", "op"), [("maxcover", "max"), ("mincover", "min")])
def test_emas_min_max_exact(emas, attr, op):
    ours = emas.derive("cover", f"{attr}_exact", op)
    assert _share(ours, emas.ref[attr].to_numpy(), 0) == 1.0


# ═══ Amazônia — 2 229 cells, 50 km ════════════════════════════════════════════

@pytest.fixture(scope="module")
def amazonia(tmp_path_factory):
    case = _case(tmp_path_factory, "amazonia", 29191, 50_000.0)
    case.source("prodes", "raster", "amazonia-prodes.tif")
    case.source("roads", "vector", "amazonia-roads")
    case.source("ports", "vector", "amazonia-ports")
    case.source("indigenous", "vector", "amazonia-indigenous")
    return case


@pytest.mark.parametrize("k", [10, 208])
def test_amazonia_coverage(amazonia, k):
    ours = amazonia.derive("prodes", f"prodes_{k}", "percentage", class_code=k, purity=True) * 100
    theirs = amazonia.ref[f"prodes_{k}"].to_numpy()
    no_data = np.isnan(ours)
    # Cells without any PRODES pixel: DisSCube reports NaN, TerraME 0.
    assert (theirs[no_data] == 0).all()
    np.testing.assert_allclose(ours[~no_data], theirs[~no_data], atol=1e-6)


@pytest.mark.xfail(strict=True, reason="min_distance is a raster approximation; TerraME uses exact polygon distance")
@pytest.mark.parametrize(("sid", "attr"), [("roads", "distroads"), ("ports", "distports")])
def test_amazonia_distance(amazonia, sid, attr):
    ours = amazonia.derive(sid, attr, "min_distance")
    assert _share(ours, amazonia.ref[attr].to_numpy(), 100) >= 0.75


def test_amazonia_area_fraction(amazonia):
    """TerraME's ``area`` (intersection area / cell area) is DisSCube's ``area``."""
    ours = amazonia.derive("indigenous", "protected", "area")
    assert _share(ours, amazonia.ref["protected"].to_numpy(), 0.01) >= 0.99
