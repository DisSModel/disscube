"""
Tests for disscube.sources.mapbiomas — MapBiomas annual land-cover maps.

Offline: a small "national" GeoTIFF shaped like MapBiomas' (WGS84, uint8,
256 × 256 blocks, no declared nodata, code 0 = not observed) stands in for the
published files. A real read runs only with DISSCUBE_ONLINE_TESTS=1.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import mapbiomas
from disscube.utils.files import sha256_file
from disscube.utils.grids import register_local_grid

BBOX = (-44.35, -2.62, -44.20, -2.47)
RES = 0.00026949458523585647


def _national(path, *, sea_cols=150):
    """1000 × 1000 px around BBOX: code 0 (sea) on the west, then water, urban, forest."""
    data = np.full((1000, 1000), 3, dtype="uint8")             # forest
    data[:, 450:650] = 24                                      # urban
    data[:, sea_cols:300] = 33                                 # water
    data[:, :sea_cols] = 0                                     # not observed
    with rasterio.open(path, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES),
                       tiled=True, blockxsize=256, blockysize=256) as dst:
        dst.write(data, 1)
    return str(path)


def test_coverage_url_and_year_range():
    assert mapbiomas.coverage_url(2020).endswith(
        "/collection11/lulc/coverage/brazil_coverage/brazil_coverage-col11_2020.tif")
    assert mapbiomas.coverage_url(2021, 4, 10).endswith(
        "/lulc_10m/collection4/coverage/brazil_coverage/brazil_coverage-col4_10m_2021.tif")
    with pytest.raises(ValueError, match="1985–2025"):
        mapbiomas.coverage_url(1984)
    with pytest.raises(ValueError, match="2017–2025"):
        mapbiomas.coverage_url(2016, 4, 10)
    with pytest.raises(ValueError, match="unknown MapBiomas dataset"):
        mapbiomas.coverage_url(2020, 9, 30)


def test_read_coverage_treats_code_zero_as_nodata(tmp_path):
    w = mapbiomas.read_coverage(2020, BBOX, url=_national(tmp_path / "br.tif", sea_cols=400))
    values = set(np.unique(w.data[~np.isnan(w.data)]).astype(int).tolist())
    assert 0 not in values
    assert values <= {3, 24, 33}
    assert np.isnan(w.data).any()                               # the "sea" columns


def test_register_mapbiomas_source(tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    url = _national(tmp_path / "br.tif")
    src = mapbiomas.register_mapbiomas_source(cube, "lulc_2020", 2020, BBOX, tmp_path / "raw", url=url)

    with rasterio.open(src.asset_url) as ds:
        assert ds.dtypes[0] == "uint8" and ds.nodata == 0
        assert str(ds.crs) == "EPSG:4326"
    assert src.time == 2020 and src.checksum == sha256_file(src.asset_url)
    assert "mapbiomas" in src.tags and "year:2020" in src.tags
    prov = json.loads((tmp_path / "raw" / "lulc_2020.provenance.json").read_text())
    assert prov["collection"] == 11 and prov["resolution_m"] == 30 and prov["year"] == 2020
    assert prov["url"] == url and prov["nodata"] == 0 and prov["license"] == "CC-BY-4.0"


def test_code_zero_is_never_a_class_on_the_grid(tmp_path):
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    grid = register_local_grid(cube, name="ilha", bbox_geo=BBOX, resolution=300.0)
    mapbiomas.register_mapbiomas_source(cube, "lulc_2020", 2020, BBOX, tmp_path / "raw",
                                        url=_national(tmp_path / "br.tif", sea_cols=400))
    cube.derive_declarative(Derivation(target="landuse", source_id="lulc_2020", operator="majority"),
                            grid_id=grid.id)
    major = cube.load("landuse", grid_id=grid.id).values
    classes = set(np.unique(major[~np.isnan(major)]).astype(int).tolist())
    assert 0 not in classes and classes <= {3, 24, 33}


@pytest.mark.skipif(os.environ.get("DISSCUBE_ONLINE_TESTS") != "1",
                    reason="set DISSCUBE_ONLINE_TESTS=1 to read the real MapBiomas files")
def test_real_mapbiomas_2020():
    w = mapbiomas.read_coverage(2020, (-44.30, -2.56, -44.27, -2.53))
    values = np.unique(w.data[~np.isnan(w.data)]).astype(int)
    assert values.size > 0 and values.min() >= 1 and values.max() <= 75
