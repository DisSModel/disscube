"""
01 — Quickstart: from a raster to model-ready variables.

Builds a synthetic 30 m land-use raster and a 30 m elevation raster, then
aggregates them onto a 300 m modeling grid with three operators:

  - ``percentage``  fraction of forest (class 3) in each cell
  - ``majority``    dominant land-use class in each cell
  - ``mean``        mean elevation in each cell

Everything is written to a temporary workspace (or to the directory given as
the first argument), so the example runs anywhere without downloads:

    python examples/01_quickstart.py            # temporary workspace
    python examples/01_quickstart.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation, GridSpec, SpatialSource

CRS = "EPSG:31983"            # SIRGAS 2000 / UTM zone 23S
ORIGIN = (570_000, 9_720_000)  # upper-left corner (x, y)
FINE, N = 30.0, 400           # 400 × 400 pixels at 30 m → 12 km × 12 km

# MapBiomas-style class codes
FOREST, PASTURE, URBAN, WATER = 3, 15, 24, 33


def write_geotiff(path: Path, array: np.ndarray) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", height=N, width=N, count=1, dtype=array.dtype,
        crs=CRS, transform=from_origin(*ORIGIN, FINE, FINE),
    ) as dst:
        dst.write(array, 1)


def make_inputs(raw: Path) -> None:
    """A coast-like scene: water to the west, a town in the middle, forest to the east."""
    rng = np.random.default_rng(42)
    x = np.linspace(0, 1, N)[None, :].repeat(N, axis=0)
    y = np.linspace(0, 1, N)[:, None].repeat(N, axis=1)

    landuse = np.full((N, N), PASTURE, dtype="uint8")
    landuse[x + 0.1 * rng.standard_normal((N, N)) > 0.6] = FOREST
    landuse[(x - 0.4) ** 2 + (y - 0.5) ** 2 < 0.02] = URBAN
    landuse[x < 0.15] = WATER
    write_geotiff(raw / "landuse.tif", landuse)

    elevation = (80 * x + 5 * rng.standard_normal((N, N))).astype("float32")
    elevation[landuse == WATER] = 0.0
    write_geotiff(raw / "elevation.tif", elevation)


def main(workspace: Path) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    make_inputs(raw)

    # 1. A catalog (SQLite) and a store (Zarr files) for derived variables.
    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))

    # 2. The modeling grid: 300 m cells over the same 12 km × 12 km extent.
    xmin, ymax = ORIGIN
    cube.register_grid(GridSpec(
        id="demo/300m", type="local", crs=CRS, resolution=300.0,
        bbox=[xmin, ymax - N * FINE, xmin + N * FINE, ymax],
    ))

    # 3. Raw sources are registered by reference; nothing is copied.
    for name in ("landuse", "elevation"):
        cube.register_spatial_source(SpatialSource(
            id=name, name=f"synthetic {name}", format="raster",
            asset_url=str(raw / f"{name}.tif"), crs=CRS,
        ))

    # 4. Declarative derivations: validated when built, before any I/O.
    derivations = [
        Derivation(target="forest_pct", source_id="landuse", operator="percentage", class_code=FOREST),
        Derivation(target="landuse_major", source_id="landuse", operator="majority"),
        Derivation(target="elev_mean", source_id="elevation", operator="mean"),
    ]
    for d in derivations:
        cube.derive_declarative(d, grid_id="demo/300m")

    # 5. Load the results as xarray DataArrays aligned to the grid.
    forest = cube.load("forest_pct", grid_id="demo/300m")
    major = cube.load("landuse_major", grid_id="demo/300m")
    elev = cube.load("elev_mean", grid_id="demo/300m")

    print(f"grid shape          : {forest.shape}  (rows, cols)")
    print(f"forest fraction     : {float(forest.mean()):.2f} of the area")
    classes, counts = np.unique(major.values, return_counts=True)
    print(f"dominant classes    : {dict(zip(classes.astype(int).tolist(), counts.tolist()))}")
    print(f"mean elevation      : {float(elev.mean()):.1f} m")
    if "coverage_purity" in forest.coords:
        print(f"min coverage purity : {float(forest.coords['coverage_purity'].min()):.2f}")

    # 6. Re-running the same derivation is a cache hit (same spec_hash).
    again = cube.derive_declarative(derivations[0], grid_id="demo/300m")
    print(f"spec_hash (cached)  : {again[0].spec_hash[:16]}…")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
