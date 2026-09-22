"""
04 — TerraME's *Fill* tutorial (Itaituba) reproduced with DisSCube.

TerraME's ``gis`` package fills a 5 km cellular space over Itaituba (Pará)
from five layers; the script and its output ship with TerraME and are copied
in ``examples/data/terrame/itaituba/`` (see the README there). This example
derives the same attributes with DisSCube and compares them cell by cell with
TerraME's own output:

    TerraME fill                         DisSCube operator
    ─────────────────────────────────    ─────────────────────────────────
    elevation   average   (raster)       mean
    defor_<k>   coverage  (raster)       percentage (class_code=k)
    distroad    distance  (lines)        min_distance
    distlocal   distance  (points)       min_distance
    population  sum, area=true (polys)   — not supported yet

Coverage is where the two tools differ by design: TerraME divides by the
whole cell, DisSCube by the valid pixels, reporting the covered share as
``coverage_purity``. Their product reproduces TerraME's value.

    python examples/04_terrame_fill_itaituba.py            # temporary workspace
    python examples/04_terrame_fill_itaituba.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

DATA = Path(__file__).resolve().parent / "data" / "terrame" / "itaituba"
CRS = "EPSG:29191"  # SAD69 / UTM zone 21S
RESOLUTION = 5_000.0
# Class names as given in itaituba.lua (87 and 167); the others are unnamed there.
CLASSES = {7: "class 7", 87: "deforestation", 167: "river", 255: "class 255"}


def zipped(name: str) -> str:
    """Shapefiles are stored zipped; GDAL reads them in place via zip://."""
    return f"zip://{DATA / name}.zip"


def main(workspace: Path) -> None:
    # TerraME's output: 620 cells with the filled attributes. Its .prj is
    # wrong (NAD83_Austin), so the real CRS is assigned explicitly.
    terrame = gpd.read_file(zipped("itaituba")).set_crs(CRS, allow_override=True)

    workspace.mkdir(parents=True, exist_ok=True)
    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))

    # Same grid as TerraME: 5 km cells over the extent of its cellular space.
    grid_id = "itaituba/5km"
    cube.register_grid(GridSpec(
        id=grid_id, type="local", crs=CRS, resolution=RESOLUTION,
        bbox=terrame.total_bounds.tolist(), description="TerraME Fill tutorial grid",
    ))

    sources = {
        "elevation": ("raster", str(DATA / "itaituba-elevation.tif")),
        "deforestation": ("raster", str(DATA / "itaituba-deforestation.tif")),
        "roads": ("vector", zipped("itaituba-roads")),
        "localities": ("vector", zipped("itaituba-localities")),
    }
    for sid, (fmt, url) in sources.items():
        cube.register_spatial_source(SpatialSource(id=sid, name=f"Itaituba {sid}", format=fmt, asset_url=url, crs=CRS))

    def fill(source_id: str, *variables: Variable) -> None:
        cube.derive(SpatialDerivation(source_id=source_id, grid_id=grid_id, role="driver", variables=list(variables)))

    fill("elevation", Variable(name="elevation", operator="mean"))
    fill("deforestation", *(Variable(name=f"defor_{k}", operator="percentage", class_code=k) for k in CLASSES))
    fill("roads", Variable(name="distroad", operator="min_distance"))
    fill("localities", Variable(name="distlocal", operator="min_distance"))

    # Match cells: TerraME numbers rows from the south, DisSCube grids are north-up.
    xmin, _, _, ymax = terrame.total_bounds
    centroids = terrame.geometry.centroid
    cols = np.floor((centroids.x.to_numpy() - xmin) / RESOLUTION).astype(int)
    rows = np.floor((ymax - centroids.y.to_numpy()) / RESOLUTION).astype(int)

    def ours(name: str, with_purity: bool = False) -> np.ndarray:
        da = cube.load(name, grid_id=grid_id)
        if with_purity:
            da = da * da.coords["coverage_purity"]
        return da.to_numpy()[rows, cols]

    print(f"{len(terrame)} cells, {RESOLUTION / 1000:.0f} km — DisSCube vs TerraME, cell by cell\n")
    print(f"{'attribute':<12} {'DisSCube':<32} {'mean |diff|':>12} {'max |diff|':>11}   within")
    print("─" * 88)

    def row(attr, label, values, reference, tol, unit):
        diff = np.abs(values - reference)
        print(f"{attr:<12} {label:<32} {diff.mean():>10.2f}{unit:>2} {diff.max():>9.2f}{unit:>2}"
              f"   {np.mean(diff <= tol):>4.0%} of cells within {tol:g}{unit}")

    row("elevation", "mean", ours("elevation"), terrame["elevation"], 1, "m")
    for k, label in CLASSES.items():
        ref = terrame[f"defor_{k}"].to_numpy()
        row(f"defor_{k}", f"percentage ({label})", ours(f"defor_{k}") * 100, ref, 1, "pp")
        row("", "  × coverage_purity", ours(f"defor_{k}", with_purity=True) * 100, ref, 1, "pp")
    row("distroad", "min_distance", ours("distroad"), terrame["distroad"], 100, "m")
    row("distlocal", "min_distance", ours("distlocal"), terrame["distlocal"], 100, "m")
    print(f"{'population':<12} {'— (area-weighted sum: not yet)':<32}")

    print("\nReading: averages and class coverage (with purity) reproduce TerraME;")
    print("min_distance is a raster approximation between cell centres, while TerraME")
    print("measures the exact distance from each cell polygon — see")
    print("docs/terrame_fill_correspondence.md.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
