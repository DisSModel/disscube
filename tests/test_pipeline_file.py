"""
Tests for DisSCube pipeline files (TOML): schema, planning, running and the CLI.

Everything runs offline; the Itaituba pipeline uses TerraME's bundled data and
is checked against the same derivations made through the Python API.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation, GridSpec, SpatialDerivation, SpatialSource, Variable
from disscube.cli import main as cli
from disscube.pipeline import PipelineError, load, plan, run
from disscube.sources import Window2D

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = sorted((ROOT / "examples" / "pipelines").glob("*.toml"))
QUICKSTART = ROOT / "examples" / "pipelines" / "quickstart.toml"

GRID = """
schema = 1
[grid]
name = "g"
crs = "EPSG:31983"
bbox = [0, 0, 3000, 3000]
resolution = 300
"""


def _toml(tmp_path, body, name="p.toml"):
    path = tmp_path / name
    path.write_text(GRID + body, encoding="utf-8")
    return path


def _raster(path, value=1.0, dtype="float32"):
    with rasterio.open(path, "w", driver="GTiff", height=100, width=100, count=1, dtype=dtype,
                       crs="EPSG:31983", transform=from_origin(0, 3000, 30, 30)) as dst:
        dst.write(np.full((100, 100), value, dtype=dtype), 1)
    return path


# ---------------------------------------------------------------------------
# The example pipeline files
# ---------------------------------------------------------------------------

def test_example_pipelines_are_discovered():
    assert len(PIPELINES) >= 1


@pytest.mark.parametrize("path", PIPELINES, ids=lambda p: p.name)
def test_example_pipelines_validate(path):
    p = plan(path)
    # Every example declares sources; a sources-only file (e.g. tests/fixtures/pipelines/test_aeroporto.toml,
    # which just fetches/validates one asset) is legitimate and has no [[derive]].
    assert p.sources


def test_quickstart_pipeline_matches_the_python_api(tmp_path):
    report = run(QUICKSTART, workspace=tmp_path / "toml")
    assert len(report.derived) == 3

    data = ROOT / "examples" / "data" / "quickstart"
    cube = CubeClient(catalog=str(tmp_path / "api.db"), store=str(tmp_path / "api"))
    cfg = load(QUICKSTART).config.grid
    cube.register_grid(GridSpec(id="api", type="local", crs=cfg.crs, resolution=cfg.resolution, bbox=cfg.bbox))
    cube.register_spatial_source(SpatialSource(id="landuse", name="lu", format="raster", crs=cfg.crs,
                                               asset_url=str(data / "landuse.tif")))
    cube.register_spatial_source(SpatialSource(id="elevation", name="el", format="raster", crs=cfg.crs,
                                               asset_url=str(data / "elevation.tif")))
    cube.derive_declarative(Derivation(target="forest_pct", source_id="landuse", operator="percentage", class_code=3),
                            grid_id="api")
    cube.derive_declarative(Derivation(target="elev_mean", source_id="elevation", operator="mean"),
                            grid_id="api")

    toml_cube = CubeClient(catalog=str(tmp_path / "toml" / "catalog.db"), store=str(tmp_path / "toml" / "store"))
    for name in ("forest_pct", "elev_mean"):
        a = toml_cube.load(name, grid_id=report.grid_id).values
        b = cube.load(name, grid_id="api").values
        assert np.allclose(a, b, equal_nan=True), name


def test_run_writes_record_and_annotates_provenance(tmp_path):
    report = run(QUICKSTART, workspace=tmp_path / "ws")
    record = json.loads(report.record.read_text())
    checksum = load(QUICKSTART).checksum
    assert record["pipeline"]["checksum"] == checksum
    assert {d["target"] for d in record["derived"]} == {"forest_pct", "landuse_major", "elev_mean"}
    prov = json.loads((tmp_path / "ws" / "raw" / "landuse.provenance.json").read_text())
    assert prov["pipeline"]["checksum"] == checksum and prov["checksum"].startswith("sha256:")



# ---------------------------------------------------------------------------
# Years expansion
# ---------------------------------------------------------------------------

def test_years_expand_sources_and_derivations(tmp_path):
    for y in (2000, 2010):
        _raster(tmp_path / f"lulc_{y}.tif", value=1 if y == 2000 else 2, dtype="uint8")
    path = _toml(tmp_path, """
[[source]]
id = "lulc_{year}"
type = "file"
path = "lulc_{year}.tif"
years = [2000, 2010]

[[derive]]
target = "class1"
source = "lulc_{year}"
operator = "percentage"
class_code = 1
""")
    p = plan(path)
    assert [s.id for s in p.sources] == ["lulc_2000", "lulc_2010"]
    assert [s.config.time for s in p.sources] == [2000, 2010]
    assert [d.source for d in p.derives] == ["lulc_2000", "lulc_2010"]

    report = run(p, workspace=tmp_path / "ws")
    cube = CubeClient(catalog=str(tmp_path / "ws" / "catalog.db"), store=str(tmp_path / "ws" / "store"))
    series = cube.load("class1", grid_id=report.grid_id)
    assert list(series["time"].values) == [2000, 2010]
    assert np.allclose(series.mean(dim=("y", "x")).values, [1.0, 0.0])


def test_explicit_years_on_a_derivation(tmp_path):
    for y in (2000, 2010):
        _raster(tmp_path / f"a_{y}.tif")
    path = _toml(tmp_path, """
[[source]]
id = "a_{year}"
type = "file"
path = "a_{year}.tif"
years = [2000, 2010]

[[derive]]
target = "m"
source = "a_{year}"
operator = "mean"
years = [2010]
""")
    assert [d.source for d in plan(path).derives] == ["a_2010"]


# ---------------------------------------------------------------------------
# Errors are caught before anything is fetched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("body", "message"), [
    ('[[derive]]\ntarget="x"\nsource="nope"\noperator="mean"\n', "unknown source 'nope'"),
    ('[[source]]\nid="a"\ntype="file"\npath="a.tif"\n[[derive]]\ntarget="x"\nsource="a"\noperator="avg"\n',
     "Unknown operator"),
    (('[[source]]\nid="a"\ntype="file"\npath="a.tif"\n[[derive]]\ntarget="x"\nsource="a"\n'
      'operator="percentage"\n'), "requires class_code"),
    ('[[source]]\nid="a"\ntype="file"\npath="a.tif"\nyears=[2000]\n', "requires '{year}' in the id"),
    ('[[source]]\nid="m"\ntype="mapbiomas"\nyear=1984\n', "1985–2025"),
    ('[[source]]\nid="m"\ntype="mapbiomas"\n', "needs 'year'"),
    (('[[source]]\nid="a"\ntype="file"\npath="a.tif"\n'
      '[[source]]\nid="a"\ntype="file"\npath="b.tif"\n'), "duplicate source ids: a"),
    ('[[source]]\nid="b"\ntype="bdc"\ncollection="C"\nperiod="2020"\n', "exactly one of 'asset'"),
    ('[[source]]\nid="a"\ntype="file"\npath="a.tif"\ncolour="red"\n', "Extra inputs are not permitted"),
    ('[[source]]\nid="a"\ntype="ftp"\npath="a.tif"\n', "does not match any of the expected tags"),
])
def test_invalid_pipelines(tmp_path, body, message):
    with pytest.raises(PipelineError, match=message):
        plan(_toml(tmp_path, body))


def test_schema_version_and_toml_syntax(tmp_path):
    bad = tmp_path / "v2.toml"
    bad.write_text(GRID.replace("schema = 1", "schema = 2"))
    with pytest.raises(PipelineError, match="unsupported pipeline schema 2"):
        load(bad)
    broken = tmp_path / "broken.toml"
    broken.write_text("schema = \n")
    with pytest.raises(PipelineError, match="invalid TOML"):
        load(broken)
    with pytest.raises(PipelineError, match="file not found"):
        load(tmp_path / "missing.toml")


def test_missing_file_source_fails_at_run(tmp_path):
    path = _toml(tmp_path, '[[source]]\nid="a"\ntype="file"\npath="nope.tif"\n')
    with pytest.raises(PipelineError, match="file not found"):
        run(path, workspace=tmp_path / "ws")


# ---------------------------------------------------------------------------
# Other source types
# ---------------------------------------------------------------------------

def test_classified_source_with_legend_file(tmp_path):
    _raster(tmp_path / "cls.tif", value=2, dtype="uint8")
    (tmp_path / "legend.csv").write_text("code,label\n1,Forest\n2,Pasture\n")
    path = _toml(tmp_path, """
[[source]]
id = "sits_2020"
type = "classified"
path = "cls.tif"
legend = "legend.csv"
time = 2020
producer = "SITS"

[[derive]]
target = "pasture"
source = "sits_2020"
operator = "percentage"
class_code = 2
""")
    run(path, workspace=tmp_path / "ws")
    prov = json.loads((tmp_path / "ws" / "raw" / "sits_2020.provenance.json").read_text())
    assert prov["legend"] == {"1": "Forest", "2": "Pasture"} and prov["producer"] == "SITS"


def test_bdc_normalized_difference_source(tmp_path, monkeypatch):
    from disscube.sources import bdc

    crs = CRS.from_epsg(31983)
    layers = {"green": 0.3, "swir16": 0.1}

    def fake_composite(collection, asset, bbox, period, **kw):
        return Window2D(np.full((100, 100), layers[asset], "float32"), from_origin(0, 3000, 30, 30), crs)

    monkeypatch.setattr(bdc, "search_items", lambda *a, **k: [])
    monkeypatch.setattr(bdc, "read_composite", fake_composite)
    path = _toml(tmp_path, """
[[source]]
id = "mndwi"
type = "bdc"
collection = "LANDSAT-16D-1"
normalized_difference = ["green", "swir16"]
period = "2020-07-01/2020-09-30"

[[derive]]
target = "mndwi_mean"
source = "mndwi"
operator = "mean"
""")
    report = run(path, workspace=tmp_path / "ws")
    cube = CubeClient(catalog=str(tmp_path / "ws" / "catalog.db"), store=str(tmp_path / "ws" / "store"))
    assert float(cube.load("mndwi_mean", grid_id=report.grid_id).mean()) == pytest.approx(0.5)
    prov = json.loads((tmp_path / "ws" / "raw" / "mndwi.provenance.json").read_text())
    assert prov["expression"] == "(green - swir16) / (green + swir16)"
    assert report.sources[0]["time"] == 2020


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def test_cli_validate_and_run(tmp_path, capsys):
    assert cli(["validate", str(QUICKSTART)]) == 0
    assert "OK" in capsys.readouterr().out
    assert cli(["run", str(QUICKSTART), "--workspace", str(tmp_path / "ws")]) == 0
    assert (tmp_path / "ws" / "run.json").exists()


def test_cli_reports_errors(tmp_path, capsys):
    path = _toml(tmp_path, '[[derive]]\ntarget="x"\nsource="nope"\noperator="mean"\n')
    assert cli(["validate", str(path)]) == 2
    assert "unknown source" in capsys.readouterr().err


def test_cli_validate_json(capsys):
    assert cli(["validate", str(QUICKSTART), "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["grid"] == "demo/300m"
    assert "elev_mean" in data["derives"]
    assert "elevation" in data["sources"]


def test_cli_validate_json_error(tmp_path, capsys):
    path = _toml(tmp_path, '[[derive]]\ntarget="x"\nsource="nope"\noperator="mean"\n')
    assert cli(["validate", str(path), "--json"]) == 2
    err = capsys.readouterr().err
    data = json.loads(err)
    assert data["status"] == "error"
    assert "unknown source" in data["error"]


def test_cli_run_dry_run(capsys):
    assert cli(["run", str(QUICKSTART), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "Plan is valid" in out


def test_cli_run_dry_run_json(capsys):
    assert cli(["run", str(QUICKSTART), "--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["dry_run"] is True
    assert data["grid"] == "demo/300m"
    assert any(s["id"] == "elevation" for s in data["sources"])
    assert any(d["target"] == "elev_mean" for d in data["derives"])


def test_cli_run_json(tmp_path, capsys):
    ws = tmp_path / "ws_json"
    assert cli(["run", str(QUICKSTART), "--workspace", str(ws), "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["workspace"] == str(ws)
    assert data["grid_id"] == "demo/300m"
    assert len(data["derived"]) == 3
    assert (ws / "run.json").exists()


def test_cli_export_json(tmp_path, capsys):
    ws = tmp_path / "ws_export"
    assert cli(["run", str(QUICKSTART), "--workspace", str(ws)]) == 0
    capsys.readouterr()
    out_tif = tmp_path / "exported.tif"
    assert cli(["export", str(QUICKSTART), "--workspace", str(ws), "--output", str(out_tif), "--json"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["workspace"] == str(ws)
    assert data["grid_id"] == "demo/300m"
    assert "elev_mean" in data["variables"]
    assert data["output"] == str(out_tif)
    assert out_tif.exists()


def test_sources_only_pipeline_without_grid_or_extent(tmp_path):
    tif = _raster(tmp_path / "raw.tif")
    path = tmp_path / "sources_only.toml"
    path.write_text(f"""schema = 1
name = "Sources Only Pipeline"

[[source]]
id = "my_raster"
type = "file"
path = "{tif.name}"
""", encoding="utf-8")

    # Planning and running should succeed without [grid] or extent
    p = plan(path)
    assert p.grid is None
    report = run(path, workspace=tmp_path / "ws")
    assert report.grid_id is None
    assert len(report.sources) == 1
    assert report.sources[0]["id"] == "my_raster"

    cube = CubeClient(catalog=str(tmp_path / "ws" / "catalog.db"), store=str(tmp_path / "ws" / "store"))
    assert cube.catalog.get_spatial_source("my_raster") is not None


def test_sources_only_windowed_source_without_extent_fails(tmp_path):
    path = tmp_path / "invalid_sources.toml"
    path.write_text("""schema = 1
name = "Windowed Without Extent"

[[source]]
id = "mb"
type = "mapbiomas"
collection = "9"
year = 2020
""", encoding="utf-8")

    with pytest.raises(ValueError, match="needs `extent` \\(or a \\[grid\\]\\)"):
        plan(path)

