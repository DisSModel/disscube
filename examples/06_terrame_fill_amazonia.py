"""
06 — TerraME's *Fill* example for the Brazilian Amazon.

TerraME's ``gis`` package fills a 50 km cellular space over the Brazilian
Amazon from the PRODES deforestation map, roads, ports and indigenous lands;
the script and its output are copied in ``examples/data/terrame/amazonia/``.
This example derives the same attributes with DisSCube and compares them
cell by cell with TerraME's output:

    TerraME fill                          DisSCube operator
    ──────────────────────────────────    ─────────────────────────────
    prodes_<k>  coverage  (raster)        percentage × coverage_purity
    distroads   distance  (lines)         min_distance
    distports   distance  (points)        min_distance
    protected   area      (polygons)      — not supported yet

PRODES is a 5 km raster with a declared nodata (255); cells with no PRODES
data at all are NaN in DisSCube (coverage_purity 0) and 0 in TerraME.

    python examples/06_terrame_fill_amazonia.py            # temporary workspace
    python examples/06_terrame_fill_amazonia.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

DATA = Path(__file__).resolve().parent / "data" / "terrame" / "amazonia"
CRS = "EPSG:29191"  # SAD69 / UTM zone 21S
RESOLUTION = 50_000.0
PRODES_CLASSES = (10, 208)


def zipped(name: str) -> str:
    """Shapefiles are stored zipped; GDAL reads them in place via zip://."""
    return f"zip://{DATA / name}.zip"


def main(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)

    # TerraME's output (2 229 cells). Its .prj is wrong (NAD83_Austin), so the
    # CRS of the input layers is assigned explicitly.
    terrame = gpd.read_file(zipped("amazonia")).set_crs(CRS, allow_override=True)

    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    grid_id = "amazonia/50km"
    cube.register_grid(GridSpec(
        id=grid_id, type="local", crs=CRS, resolution=RESOLUTION,
        bbox=terrame.total_bounds.tolist(), description="TerraME Amazonia example grid",
    ))

    for sid, fmt, url in [
        ("prodes", "raster", str(DATA / "amazonia-prodes.tif")),
        ("roads", "vector", zipped("amazonia-roads")),
        ("ports", "vector", zipped("amazonia-ports")),
    ]:
        cube.register_spatial_source(SpatialSource(id=sid, name=f"Amazonia {sid}", format=fmt, asset_url=url, crs=CRS))

    def fill(source_id: str, *variables: Variable) -> None:
        cube.derive(SpatialDerivation(source_id=source_id, grid_id=grid_id, role="driver", variables=list(variables)))

    fill("prodes", *(Variable(name=f"prodes_{k}", operator="percentage", class_code=k) for k in PRODES_CLASSES))
    fill("roads", Variable(name="distroads", operator="min_distance"))
    fill("ports", Variable(name="distports", operator="min_distance"))

    # Match cells by centroid (DisSCube grids are north-up).
    xmin, _, _, ymax = terrame.total_bounds
    centroids = terrame.geometry.centroid
    cols = np.floor((centroids.x.to_numpy() - xmin) / RESOLUTION).astype(int)
    rows = np.floor((ymax - centroids.y.to_numpy()) / RESOLUTION).astype(int)

    grid = cube.catalog.get_grid(grid_id)
    print(f"{len(terrame)} TerraME cells (of a {grid.rows} × {grid.cols} rectangle), "
          f"{RESOLUTION / 1000:.0f} km — DisSCube vs TerraME, cell by cell\n")

    print("coverage (percent of the cell):")
    for k in PRODES_CLASSES:
        da = cube.load(f"prodes_{k}", grid_id=grid_id)
        ours = (da * da.coords["coverage_purity"]).to_numpy()[rows, cols] * 100
        theirs = terrame[f"prodes_{k}"].to_numpy()
        no_data = np.isnan(ours)
        diff = np.abs(ours[~no_data] - theirs[~no_data])
        print(f"  prodes_{k:<4} percentage × coverage_purity: max |diff| {diff.max():.2f} pp in "
              f"{(~no_data).sum()} cells with data; {no_data.sum()} cells without PRODES data "
              f"(TerraME: 0, DisSCube: NaN)")

    print("\ndistance (m):")
    for attr in ("distroads", "distports"):
        ours = cube.load(attr, grid_id=grid_id).to_numpy()[rows, cols]
        diff = ours - terrame[attr].to_numpy()
        print(f"  {attr:<10} min_distance: mean |diff| {np.abs(diff).mean():>8,.0f}  "
              f"mean bias {diff.mean():>+8,.0f}  max |diff| {np.abs(diff).max():>8,.0f}")

    print("\nprotected   area (fraction of the cell covered by indigenous lands): not supported yet")

    print("\nReading: class coverage reproduces TerraME wherever PRODES has data;")
    print("min_distance is a raster approximation between 50 km cell centres, so it")
    print("differs by tens of kilometres from TerraME's per-polygon distance. See")
    print("docs/terrame_fill_correspondence.md.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
