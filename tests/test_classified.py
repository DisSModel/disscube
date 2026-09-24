"""Tests for disscube.sources.classified and the legend/reclassification helpers."""

from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from disscube import CubeClient
from disscube.sources import Window2D, read_legend, reclassify
from disscube.sources.classified import register_classified_map

BBOX = (-54.84, -3.59, -54.46, -3.17)
RES = 0.0005


def _map(path, *, with_zero=False, nodata=None):
    data = np.ones((1000, 1000), dtype="uint8")
    data[:, 400:] = 2
    data[:, 800:] = 3
    if with_zero:
        data[:100, :] = 0
    with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES),
                       nodata=nodata) as dst:
        dst.write(data, 1)
    return path


def test_read_legend_formats(tmp_path):
    (tmp_path / "l.json").write_text(json.dumps({"1": "Forest", "2": "Pasture"}))
    (tmp_path / "l2.json").write_text(json.dumps([{"code": 1, "label": "Forest"}]))
    (tmp_path / "l.csv").write_text("code,label\n1,Forest\n2,Pasture\n")
    (tmp_path / "l.qml").write_text('<qgis><paletteEntry value="1" label="Forest"/></qgis>')
    assert read_legend({1: "Forest"}) == {1: "Forest"}
    assert read_legend(tmp_path / "l.json") == {1: "Forest", 2: "Pasture"}
    assert read_legend(tmp_path / "l2.json") == {1: "Forest"}
    assert read_legend(tmp_path / "l.csv") == {1: "Forest", 2: "Pasture"}
    assert read_legend(tmp_path / "l.qml") == {1: "Forest"}
    with pytest.raises(ValueError, match="unsupported legend"):
        read_legend(tmp_path / "l.txt")


def test_reclassify_unknown_codes_become_nan():
    w = Window2D(np.array([[1, 2, np.nan, 9]], dtype="float32"), from_origin(0, 0, 1, 1),
                 CRS.from_epsg(4326))
    out = reclassify(w, {1: 10, 2: 20})
    assert out.data[0, :2].tolist() == [10, 20]
    assert np.isnan(out.data[0, 2:]).all()


def test_register_classified_map_with_legend(tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    href = _map(tmp_path / "sits_class_2020.tif")
    src = register_classified_map(cube, "lulc_sits", href, BBOX, tmp_path / "raw", time=2020,
                                  legend={1: "Forest", 2: "Pasture", 3: "Water"},
                                  producer="SITS")
    with rasterio.open(src.asset_url) as ds:
        assert ds.dtypes[0] == "uint8" and ds.nodata == 0
        assert set(np.unique(ds.read(1)).tolist()) <= {0, 1, 2, 3}
    prov = json.loads((tmp_path / "raw" / "lulc_sits.provenance.json").read_text())
    assert prov["producer"] == "SITS" and prov["legend"] == {"1": "Forest", "2": "Pasture", "3": "Water"}
    assert prov["href_checksum"].startswith("sha256:")
    assert src.time == 2020 and "producer:SITS" in src.tags


def test_class_zero_requires_explicit_nodata(tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    href = _map(tmp_path / "m.tif", with_zero=True)
    with pytest.raises(ValueError, match="class 0"):
        register_classified_map(cube, "m", href, BBOX, tmp_path / "raw")
    src = register_classified_map(cube, "m", href, BBOX, tmp_path / "raw", nodata=255)
    with rasterio.open(src.asset_url) as ds:
        assert ds.nodata == 255 and 0 in np.unique(ds.read(1))
