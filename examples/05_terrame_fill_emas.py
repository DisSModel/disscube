"""
05 — TerraME's *Fill* example for Emas National Park.

TerraME's ``gis`` package fills a 500 m cellular space over Emas National Park
(Goiás) from firebreak and river lines and a 30 m fire-accumulation raster;
the script and its output are copied in ``examples/data/terrame/emas/``.
This example derives the same attributes with DisSCube and compares them
cell by cell with TerraME's output:

    TerraME fill                     DisSCube operator
    ─────────────────────────────    ─────────────────
    firebreak  presence  (lines)     presence
    river      presence  (lines)     presence
    maxcover   maximum   (raster)    max
    mincover   minimum   (raster)    min

TerraME kept only the cells that touch the park limit (``input = "limit"``),
so DisSCube derives the full rectangle and the comparison uses TerraME's cells.

    python examples/05_terrame_fill_emas.py            # temporary workspace
    python examples/05_terrame_fill_emas.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

DATA = Path(__file__).resolve().parent / "data" / "terrame" / "emas"
CRS = "EPSG:29192"  # SAD69 / UTM zone 22S
RESOLUTION = 500.0


def zipped(name: str) -> str:
    """Shapefiles are stored zipped; GDAL reads them in place via zip://."""
    return f"zip://{DATA / name}.zip"


def main(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)

    # TerraME's output (5 514 cells). Its .prj is a generic UTM 22S, so the
    # CRS of the input layers is assigned explicitly.
    terrame = gpd.read_file(zipped("emas")).set_crs(CRS, allow_override=True)

    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    grid_id = "emas/500m"
    cube.register_grid(GridSpec(
        id=grid_id, type="local", crs=CRS, resolution=RESOLUTION,
        bbox=terrame.total_bounds.tolist(), description="TerraME Emas example grid",
    ))

    for sid, fmt, url in [
        ("firebreak", "vector", zipped("emas-firebreak")),
        ("river", "vector", zipped("emas-river")),
        ("cover", "raster", str(DATA / "emas-accumulation.tif")),
    ]:
        cube.register_spatial_source(SpatialSource(id=sid, name=f"Emas {sid}", format=fmt, asset_url=url, crs=CRS))

    def fill(source_id: str, *variables: Variable) -> None:
        cube.derive(SpatialDerivation(source_id=source_id, grid_id=grid_id, role="driver", variables=list(variables)))

    fill("firebreak", Variable(name="firebreak", operator="presence"))
    fill("river", Variable(name="river", operator="presence"))
    # One raster, two variables: a derivation may compute several at once.
    fill("cover", Variable(name="maxcover", operator="max"), Variable(name="mincover", operator="min"))

    # Match cells by centroid (DisSCube grids are north-up).
    xmin, _, _, ymax = terrame.total_bounds
    centroids = terrame.geometry.centroid
    cols = np.floor((centroids.x.to_numpy() - xmin) / RESOLUTION).astype(int)
    rows = np.floor((ymax - centroids.y.to_numpy()) / RESOLUTION).astype(int)

    grid = cube.catalog.get_grid(grid_id)
    print(f"{len(terrame)} TerraME cells (of a {grid.rows} × {grid.cols} rectangle), "
          f"{RESOLUTION:.0f} m — DisSCube vs TerraME, cell by cell\n")
    print(f"{'attribute':<11} {'DisSCube':<10} {'identical cells':>16} {'differing':>10}")
    print("─" * 52)
    for attr, op in [("firebreak", "presence"), ("river", "presence"), ("maxcover", "max"), ("mincover", "min")]:
        ours = cube.load(attr, grid_id=grid_id).to_numpy()[rows, cols]
        same = ours == terrame[attr].to_numpy()
        print(f"{attr:<11} {op:<10} {same.mean():>15.1%} {int((~same).sum()):>10}")

    print("\nReading: the differing cells sit on cell borders. TerraME marks every cell")
    print("a line touches and assigns each pixel to the cell containing its centre;")
    print("DisSCube rasterizes lines through cell centres and, for min/max, also counts")
    print("pixels that straddle a border (500 m / 30 m is not an integer). See")
    print("docs/terrame_fill_correspondence.md.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
