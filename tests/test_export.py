"""The cube as an xarray Dataset, GeoTIFF/netCDF exports, and DisSModel being optional."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr

from disscube import CubeClient
from disscube.models import DerivedVariable, GridSpec
from disscube.pipeline import export_cube, run

QUICKSTART = Path(__file__).resolve().parents[1] / "examples" / "pipelines" / "quickstart.toml"

N = 6
CRS = "EPSG:31982"


def _da(values, name, **attrs):
    ys = 100.0 - 10.0 * np.arange(N) - 5.0
    xs = 10.0 * np.arange(N) + 5.0
    return xr.DataArray(values.astype("float32"), dims=("y", "x"), coords={"y": ys, "x": xs},
                        attrs={"crs": CRS, "grid_id": "G1", **attrs}, name=name)


@pytest.fixture
def cube(tmp_path):
    """Static ``elev`` and ``mask``; temporal ``forest`` for 2020 and 2010 (registered out of order)."""
    cube = CubeClient(str(tmp_path / "cat.db"), str(tmp_path / "store"))
    cube.register_grid(GridSpec(id="G1", type="local", crs=CRS, resolution=10, bbox=[0, 40, 60, 100]))
    mask = np.ones((N, N))
    mask[:, 0] = 0
    layers = [
        ("elev", [], np.arange(N * N).reshape(N, N)),
        ("mask", [], mask),
        ("forest", [2020], np.full((N, N), 0.2)),
        ("forest", [2010], np.full((N, N), 0.8)),
    ]
    for name, times, values in layers:
        uid = f"{name}_{'_'.join(map(str, times))}"
        path = tmp_path / "store" / f"{uid}.zarr"
        _da(values, name, spec_hash=f"hash-{uid}", source_id=f"src-{uid}", source_checksum=f"sha256:src-{uid}").to_dataset(name=name).to_zarr(path, mode="w", consolidated=False)
        cube.catalog.save_derived(DerivedVariable(
            id=uid, name=name, grid_id="G1", role="driver", times=times, dtype="float32",
            derivation_id=uid, spec_hash=f"hash-{uid}", tile_id=None, content_hash=f"sha256:zarr-{uid}", asset_url=str(path)))
    return cube


def test_to_dataset_mixes_static_and_temporal(cube):
    ds = cube.to_dataset(["elev", "forest"], grid_id="G1")
    assert isinstance(ds, xr.Dataset)
    assert ds["elev"].dims == ("y", "x")
    assert ds["forest"].dims == ("time", "y", "x")
    assert ds["time"].values.tolist() == [2010, 2020]
    assert float(ds["forest"].sel(time=2010).mean()) == pytest.approx(0.8)
    assert ds.rio.crs.to_epsg() == 31982
    t = ds.rio.transform()
    assert (t.a, t.e, t.c, t.f) == (10.0, -10.0, 0.0, 100.0)


def test_to_dataset_period_and_empty_period(cube):
    ds = cube.to_dataset(["forest", "elev"], grid_id="G1", period=("2015", "2020"))
    assert ds["time"].values.tolist() == [2020]
    with pytest.raises(ValueError, match="No variables"):
        cube.to_dataset(["forest"], grid_id="G1", period=("1990", "2000"))


def test_geotiff_one_band_per_variable_and_year(cube, tmp_path):
    out = tmp_path / "out" / "cube.tif"
    cube.export_geotiff(["elev", "forest"], out, grid_id="G1")
    with rasterio.open(out) as src:
        assert src.count == 3
        assert list(src.descriptions) == ["elev", "forest_2010", "forest_2020"]
        assert src.crs.to_epsg() == 31982
        assert src.transform.f == 100.0
        assert src.tags(2)["YEAR"] == "2010"
        assert src.tags(2)["SPEC_HASH"] == "hash-forest_2010"
        assert src.tags(3)["SPEC_HASH"] == "hash-forest_2020"          # each year its own slice
        assert src.tags(3)["SOURCE_CHECKSUM"] == "sha256:src-forest_2020"
        assert src.tags(3)["CONTENT_HASH"] == "sha256:zarr-forest_2020"
        assert src.tags(1)["SOURCE_ID"] == "src-elev_"
        assert src.tags()["BANDS"] == "elev,forest_2010,forest_2020"
        assert src.read(2)[0, 1] == pytest.approx(0.8)


def test_geotiff_period_selects_years(cube, tmp_path):
    out = tmp_path / "recent.tif"
    cube.export_geotiff(["forest"], out, grid_id="G1", period=("2015", "2020"))
    with rasterio.open(out) as src:
        assert list(src.descriptions) == ["forest_2020"]


def test_geotiff_applies_mask_when_requested(cube, tmp_path):
    out = tmp_path / "masked.tif"
    cube.export_geotiff(["mask", "elev"], out, grid_id="G1")
    with rasterio.open(out) as src:
        mask, elev = src.read(1), src.read(2)
    assert np.isnan(mask[:, 0]).all() and (mask[:, 1:] == 1).all()
    assert np.isnan(elev[:, 0]).all() and not np.isnan(elev[:, 1:]).any()


def test_netcdf_roundtrip_keeps_time_crs_and_mask(cube, tmp_path):
    pytest.importorskip("h5py")
    out = tmp_path / "cube.nc"
    cube.export_netcdf(["elev", "forest", "mask"], out, grid_id="G1")
    with xr.open_dataset(out) as ds:
        assert ds.attrs["Conventions"] == "CF-1.8"
        assert [str(t)[:10] for t in ds["time"].values] == ["2010-01-01", "2020-01-01"]
        assert ds["forest"].dims == ("time", "y", "x")
        assert float(ds["forest"].isel(time=0).mean()) == pytest.approx(0.8)
        assert "spec_hash" not in ds["forest"].attrs                      # one per year: see provenance
        assert ds["elev"].attrs["spec_hash"] == "hash-elev_"
        assert ds["elev"].attrs["source_checksum"] == "sha256:src-elev_"
        prov = json.loads(ds.attrs["disscube_provenance"])["variables"]
        assert [(r["time"], r["source_checksum"]) for r in prov["forest"]] == [
            (2010, "sha256:src-forest_2010"), (2020, "sha256:src-forest_2020")]
        assert prov["forest"][1]["content_hash"] == "sha256:zarr-forest_2020"
        assert "exported" in ds.attrs["history"] and ds.attrs["source"].startswith("DisSCube")
        assert (ds["mask"].values[:, 0] == 0).all()  # kept as a variable, not applied
        assert ds.rio.crs.to_epsg() == 31982


def test_netcdf_without_a_backend_says_how_to_install(cube, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "h5netcdf", None)
    monkeypatch.setitem(sys.modules, "netCDF4", None)
    monkeypatch.setitem(sys.modules, "h5py", None)
    with pytest.raises(ImportError, match=r"disscube\[netcdf\]"):
        cube.export_netcdf(["elev"], tmp_path / "x.nc", grid_id="G1")


# --- DisSModel is optional ---------------------------------------------------

def _block_dissmodel(monkeypatch):
    """Make every import of dissmodel fail, even if an earlier test already loaded it."""
    for name in ("dissmodel", "dissmodel.geo", "dissmodel.geo.raster", "dissmodel.geo.raster.backend"):
        monkeypatch.setitem(sys.modules, name, None)


def test_exports_and_dataset_work_without_dissmodel(cube, tmp_path, monkeypatch):
    _block_dissmodel(monkeypatch)
    cube.to_dataset(["elev", "forest"], grid_id="G1")
    cube.export_geotiff(["elev"], tmp_path / "a.tif", grid_id="G1")
    ws = tmp_path / "ws"
    run(QUICKSTART, workspace=ws, export_geotiff=tmp_path / "q.tif")
    export_cube(QUICKSTART, output=tmp_path / "q2.tif", workspace=ws)
    assert (tmp_path / "q.tif").exists() and (tmp_path / "q2.tif").exists()


def test_to_raster_backend_without_dissmodel_says_how_to_install(cube, monkeypatch):
    _block_dissmodel(monkeypatch)
    with pytest.raises(ImportError, match=r"disscube\[dissmodel\]"):
        cube.to_raster_backend(["elev"], grid_id="G1")


def test_to_raster_backend_keeps_each_time_axis(cube):
    pytest.importorskip("dissmodel")
    backend = cube.to_raster_backend(["elev", "forest"], grid_id="G1")
    assert backend.time_axis("forest").tolist() == [2010, 2020]
    assert backend.transform.f == 100.0


# --- pipeline export formats -------------------------------------------------

def test_run_exports_netcdf_by_suffix(tmp_path):
    pytest.importorskip("h5py")
    out = tmp_path / "q.nc"
    run(QUICKSTART, workspace=tmp_path / "ws", export_geotiff=out)
    with xr.open_dataset(out) as ds:
        assert len(ds.data_vars) >= 1


def test_export_table_format_overrides_suffix(tmp_path):
    from disscube.pipeline import runner

    calls = []

    class Fake:
        def export_netcdf(self, *a, **k): calls.append("nc")
        def export_geotiff(self, *a, **k): calls.append("tif")

    runner._export(Fake(), ["v"], tmp_path / "x.dat", "g", fmt="netcdf")
    runner._export(Fake(), ["v"], tmp_path / "x.nc", "g")
    runner._export(Fake(), ["v"], tmp_path / "x.tif", "g")
    assert calls == ["nc", "nc", "tif"]


def test_provenance_lists_slices_in_time_order(cube):
    records = cube.provenance("forest", grid_id="G1")
    assert [r["time"] for r in records] == [2010, 2020]
    assert records[0]["spec_hash"] == "hash-forest_2010"
    (static,) = cube.provenance("elev", grid_id="G1")
    assert static["time"] is None and static["source_id"] == "src-elev_"


def test_provenance_tolerates_products_without_source_checksum(cube, tmp_path):
    # a zarr written before source_checksum was recorded
    path = tmp_path / "store" / "old.zarr"
    da = _da(np.ones((N, N)), "old", spec_hash="h-old")
    da.to_dataset(name="old").to_zarr(path, mode="w", consolidated=False)
    cube.catalog.save_derived(DerivedVariable(
        id="old", name="old", grid_id="G1", role="driver", times=[], dtype="float32",
        derivation_id="h-old", spec_hash="h-old", tile_id=None, content_hash=None, asset_url=str(path)))
    (rec,) = cube.provenance("old", grid_id="G1")
    assert rec == {"time": None, "spec_hash": "h-old"}


def test_pipeline_export_records_the_pipeline_and_source_checksums(tmp_path):
    out = tmp_path / "q.tif"
    run(QUICKSTART, workspace=tmp_path / "ws", export_geotiff=out)
    with rasterio.open(out) as src:
        tags = src.tags()
        assert tags["PIPELINE_FILE"] == "quickstart.toml"
        assert tags["PIPELINE_CHECKSUM"].startswith("sha256:")
        assert src.tags(1)["SOURCE_CHECKSUM"].startswith("sha256:")
        assert len(src.tags(1)["CONTENT_HASH"]) >= 32


def test_cli_reports_a_missing_netcdf_backend_without_a_traceback(tmp_path, monkeypatch, capsys):
    from disscube.cli import main as cli

    for name in ("h5netcdf", "h5py", "netCDF4"):
        monkeypatch.setitem(sys.modules, name, None)
    code = cli(["run", str(QUICKSTART), "--workspace", str(tmp_path / "ws"), "-o", str(tmp_path / "q.nc")])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error:") and "disscube[netcdf]" in err and "Traceback" not in err
