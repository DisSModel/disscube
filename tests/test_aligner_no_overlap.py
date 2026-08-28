"""
Tests for the no-overlap path in ``GridAligner``.

A tile mesh is normally selected by envelope, so some tiles legitimately fall
outside the data. Reading the whole source only to reproject it into an
all-nodata window costs the entire raster in memory and produces nothing —
measured at 31x the target window for a real BDC tile against a real mosaic.
``_crop_to_grid`` must report the no-overlap case instead of falling back to
an unclipped read, and the alignment must still yield a correctly shaped,
fully invalid result.
"""

import numpy as np
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_bounds

from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable
from disscube.pipeline import PipelineContext
from disscube.pipeline.aligner import GridAligner

CRS = "EPSG:31982"


def _write_raster(path, array, bbox, nodata=-9999.0):
    rows, cols = array.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=CRS, transform=from_bounds(*bbox, cols, rows),
        nodata=nodata,
    ) as dst:
        dst.write(array.astype("float32"), 1)
    return str(path)


def _grid(bbox, resolution=10, gid="G1"):
    return GridSpec(id=gid, type="local", crs=CRS, resolution=resolution, bbox=list(bbox))


def _align(url, grid, operator="mean"):
    aligner = GridAligner()
    source = SpatialSource(id="S1", name="S1", format="raster", asset_url=url, crs=CRS)
    variables = [Variable(name="v", operator=operator)]
    ctx = PipelineContext(
        source=source, grid=grid,
        derivation=SpatialDerivation(
            source_id="S1", grid_id=grid.id, role="test", variables=variables
        ),
    )
    return aligner.execute(ctx).data["v"]


# ── _crop_to_grid contract ───────────────────────────────────────────────────

def test_crop_returns_none_when_grid_is_outside_source(tmp_path):
    import rioxarray
    url = _write_raster(tmp_path / "s.tif", np.ones((10, 10)), bbox=(0, 0, 100, 100))
    band = rioxarray.open_rasterio(url).isel(band=0)
    # Grid far away from the source extent.
    assert GridAligner()._crop_to_grid(band, _grid((500, 500, 600, 600))) is None


def test_crop_returns_data_when_grid_overlaps(tmp_path):
    import rioxarray
    url = _write_raster(tmp_path / "s.tif", np.ones((10, 10)), bbox=(0, 0, 100, 100))
    band = rioxarray.open_rasterio(url).isel(band=0)
    cropped = GridAligner()._crop_to_grid(band, _grid((20, 20, 50, 50)))
    assert cropped is not None
    assert cropped.size < band.size   # genuinely cropped, not the whole raster


# ── Alignment result for a non-overlapping grid ──────────────────────────────

@pytest.mark.parametrize("operator", ["mean", "majority"])
def test_non_overlapping_grid_yields_empty_result_of_grid_shape(tmp_path, operator):
    url = _write_raster(tmp_path / "s.tif", np.ones((10, 10)), bbox=(0, 0, 100, 100))
    grid = _grid((500, 500, 600, 600))
    aligned = _align(url, grid, operator=operator)
    assert aligned.shape == (grid.rows, grid.cols)


def test_non_overlapping_result_is_entirely_nodata(tmp_path):
    url = _write_raster(tmp_path / "s.tif", np.ones((10, 10)), bbox=(0, 0, 100, 100),
                        nodata=-9999.0)
    aligned = _align(url, _grid((500, 500, 600, 600)))
    values = np.asarray(aligned.values)
    assert np.all((values == -9999.0) | np.isnan(values))


def test_non_overlapping_result_carries_source_nodata(tmp_path):
    """Categorical operators rely on _disscube_nodata to mark cells invalid."""
    url = _write_raster(tmp_path / "s.tif", np.ones((10, 10)), bbox=(0, 0, 100, 100),
                        nodata=-9999.0)
    aligned = _align(url, _grid((500, 500, 600, 600)), operator="majority")
    assert aligned.attrs.get("_disscube_nodata") == -9999.0


def test_overlapping_grid_still_reads_data(tmp_path):
    """Guard: the no-overlap shortcut must not swallow the normal path."""
    src = np.arange(100, dtype="float32").reshape(10, 10)
    url = _write_raster(tmp_path / "s.tif", src, bbox=(0, 0, 100, 100))
    aligned = _align(url, _grid((0, 0, 100, 100)))
    values = np.asarray(aligned.values)
    assert np.isfinite(values).any()
    assert not np.all(values == -9999.0)
