"""The aligner must close the source raster once the aligned arrays are in memory.

It used to leave the file open; the handle was finalised at interpreter exit and `disscube run` ended with
`Error in sys.excepthook:` on stderr (exit code 0, results fine).
"""
import numpy as np
import rasterio
import rioxarray
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import mapbiomas
from disscube.utils.grids import register_local_grid

BBOX = (-44.35, -2.62, -44.20, -2.47)
RES = 0.00026949458523585647


def test_aligner_closes_the_source_file(tmp_path, monkeypatch):
    url = tmp_path / "br.tif"
    with rasterio.open(url, "w", driver="GTiff", height=1000, width=1000, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(BBOX[0] - 0.02, BBOX[3] + 0.02, RES, RES)) as dst:
        dst.write(np.full((1000, 1000), 3, dtype="uint8"), 1)
    cube = CubeClient(catalog=str(tmp_path / "c.db"), store=str(tmp_path / "s"))
    grid = register_local_grid(cube, name="g", bbox_geo=BBOX, resolution=500.0)
    mapbiomas.register_mapbiomas_source(cube, "lulc", 2020, BBOX, tmp_path / "raw", url=str(url))

    closed, original = [], rioxarray.open_rasterio

    def spy(*args, **kwargs):
        da = original(*args, **kwargs)
        close = da._close
        da.set_close(lambda: (closed.append(1), close())[1])
        return da

    monkeypatch.setattr("disscube.pipeline.aligner.rioxarray.open_rasterio", spy)
    for op, kw in (("percentage", {"class_code": 3}), ("mean", {})):                 # fine and continuous paths
        closed.clear()
        cube.derive_declarative(Derivation(target=f"v_{op}", source_id="lulc", operator=op, **kw), grid_id=grid.id)
        assert closed == [1], op
