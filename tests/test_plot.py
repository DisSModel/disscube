"""disscube plot: a PNG of the bands of an exported GeoTIFF."""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

pytest.importorskip("matplotlib")

from disscube.cli import main
from disscube.plot import _kind, plot_geotiff


def _tif(path):
    rng = np.random.default_rng(1)
    share = rng.random((30, 40))
    codes = rng.choice([3.0, 15.0, 24.0], size=(30, 40))
    elev = rng.normal(500, 80, (30, 40))
    share[:3] = np.nan
    names = ["forest_2015", "forest_2020", "lulc_2020", "elev"]
    years = ["2015", "2020", "2020", None]
    with rasterio.open(path, "w", driver="GTiff", height=30, width=40, count=4, dtype="float64", nodata=np.nan,
                       crs="EPSG:5880", transform=from_origin(0, 30_000, 1000, 1000)) as dst:
        dst.update_tags(GRID_ID="g1")
        for i, (name, year, arr) in enumerate(zip(names, years, (share, share, codes, elev), strict=True), 1):
            dst.write(arr, i)
            dst.set_band_description(i, name)
            dst.update_tags(i, VARIABLE=name.split("_")[0], **({"YEAR": year} if year else {}))
    return path


def test_kind_follows_the_values():
    assert _kind(np.array([0.0, 0.2, 0.9, np.nan])) == "share"
    assert _kind(np.array([3.0, 15.0, 24.0, 3.0])) == "classes"
    assert _kind(np.array([0.0, 1.0, 1.0])) == "share"          # a binary mask is not a set of classes
    assert _kind(np.array([210.0, 480.5, 760.2])) == "continuous"
    assert _kind(np.array([np.nan, np.nan])) == "empty"


def test_plot_writes_one_panel_per_band(tmp_path):
    out = plot_geotiff(_tif(tmp_path / "a.tif"), tmp_path / "look.png")
    assert out.exists() and out.stat().st_size > 5_000


def test_plot_selects_bands_by_year_name_or_variable(tmp_path):
    tif = _tif(tmp_path / "a.tif")
    assert plot_geotiff(tif, tmp_path / "y.png", year=2020).exists()
    assert plot_geotiff(tif, tmp_path / "n.png", bands=["elev"]).exists()
    assert plot_geotiff(tif, tmp_path / "v.png", bands=["forest"]).exists()      # a variable name: both years
    with pytest.raises(ValueError, match="no band"):
        plot_geotiff(tif, tmp_path / "x.png", bands=["nothing"])


def test_cli_plot_and_its_errors(tmp_path, capsys):
    tif = _tif(tmp_path / "a.tif")
    assert main(["plot", str(tif), "-o", str(tmp_path / "p.png")]) == 0
    assert (tmp_path / "p.png").exists()
    assert main(["plot", str(tif), "--bands", "zzz"]) == 2
    assert "no band" in capsys.readouterr().err


def test_shared_colorbar_draws_one_bar_for_shares_only(tmp_path):
    tif = _tif(tmp_path / "x.tif")
    plain = plot_geotiff(tif, tmp_path / "a.png", bands=["forest_2015", "forest_2020"], cols=2)
    one = plot_geotiff(tif, tmp_path / "b.png", bands=["forest_2015", "forest_2020"], cols=2, shared_colorbar=True)
    assert one.stat().st_size > 0 and plain.stat().st_size > 0
    # with a class band in the mix the option is ignored (each panel keeps its own bar)
    mixed = plot_geotiff(tif, tmp_path / "c.png", bands=["forest_2015", "lulc_2020"], shared_colorbar=True)
    assert mixed.exists()
    assert main(["plot", str(tif), "-o", str(tmp_path / "d.png"), "--shared-colorbar", "--bands", "forest_2015"]) == 0
