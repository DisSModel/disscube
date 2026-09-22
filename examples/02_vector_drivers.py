"""
02 — Vector drivers: distances, counts, presence and attributes.

LUCC models often use drivers derived from vector layers. This example builds
three synthetic GeoPackages — towns (points), roads (lines) and protected
areas (polygons) — and derives, on a 1 km grid:

  - ``min_distance``  distance from each cell to the nearest road / town
  - ``count``         number of towns whose centroid falls in each cell
  - ``presence``      1 where a protected area touches the cell
  - ``attribute``     a numeric column rasterized as is (``protection``;
                      the column must have the same name as the variable)

    python examples/02_vector_drivers.py            # temporary workspace
    python examples/02_vector_drivers.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box

from disscube import CubeClient, GridSpec, SpatialDerivation, SpatialSource, Variable

CRS = "EPSG:31983"
XMIN, YMIN, SIZE = 560_000.0, 9_690_000.0, 20_000.0  # 20 km × 20 km study area


def make_inputs(raw: Path) -> None:
    rng = np.random.default_rng(7)

    towns = gpd.GeoDataFrame(
        {"name": [f"town_{i}" for i in range(12)]},
        geometry=[Point(XMIN + rng.uniform(0, SIZE), YMIN + rng.uniform(0, SIZE)) for _ in range(12)],
        crs=CRS,
    )
    towns.to_file(raw / "towns.gpkg")

    roads = gpd.GeoDataFrame(
        {"kind": ["highway", "local"]},
        geometry=[
            LineString([(XMIN, YMIN + 5_000), (XMIN + SIZE, YMIN + 15_000)]),
            LineString([(XMIN + 12_000, YMIN), (XMIN + 12_000, YMIN + SIZE)]),
        ],
        crs=CRS,
    )
    roads.to_file(raw / "roads.gpkg")

    protected = gpd.GeoDataFrame(
        {"protection": [1, 2]},  # e.g. 1 = sustainable use, 2 = strict protection
        geometry=[
            box(XMIN + 1_000, YMIN + 12_000, XMIN + 7_000, YMIN + 19_000),
            box(XMIN + 14_000, YMIN + 1_000, XMIN + 19_000, YMIN + 6_000),
        ],
        crs=CRS,
    )
    protected.to_file(raw / "protected.gpkg")


def main(workspace: Path) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    make_inputs(raw)

    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    cube.register_grid(GridSpec(
        id="demo/1km", type="local", crs=CRS, resolution=1_000.0,
        bbox=[XMIN, YMIN, XMIN + SIZE, YMIN + SIZE],
    ))
    for name in ("towns", "roads", "protected"):
        cube.register_spatial_source(SpatialSource(
            id=name, name=f"synthetic {name}", format="vector",
            asset_url=str(raw / f"{name}.gpkg"), crs=CRS,
        ))

    # One derivation per source; a derivation may compute several variables.
    cube.derive(SpatialDerivation(
        source_id="roads", grid_id="demo/1km", role="driver",
        variables=[Variable(name="dist_road", operator="min_distance")],
    ))
    cube.derive(SpatialDerivation(
        source_id="towns", grid_id="demo/1km", role="driver",
        variables=[
            Variable(name="dist_town", operator="min_distance"),
            Variable(name="n_towns", operator="count"),
        ],
    ))
    cube.derive(SpatialDerivation(
        source_id="protected", grid_id="demo/1km", role="driver",
        variables=[
            Variable(name="in_protected", operator="presence"),
            Variable(name="protection", operator="attribute"),
        ],
    ))

    dist_road = cube.load("dist_road", grid_id="demo/1km")
    dist_town = cube.load("dist_town", grid_id="demo/1km")
    n_towns = cube.load("n_towns", grid_id="demo/1km")
    in_protected = cube.load("in_protected", grid_id="demo/1km")
    protection = cube.load("protection", grid_id="demo/1km")

    print(f"grid shape             : {dist_road.shape}")
    print(f"distance to road (m)   : max {float(dist_road.max()):,.0f}")
    print(f"distance to town (m)   : mean {float(dist_town.mean()):,.0f}")
    print(f"towns counted          : {int(n_towns.sum())} of 12")
    print(f"cells in protected area: {int(in_protected.sum())}")
    levels, counts = np.unique(protection.values, return_counts=True)
    print(f"protection levels      : {dict(zip(levels.astype(int).tolist(), counts.tolist()))}")

    print("\nderived variables in the catalog:")
    for d in cube.search(grid="demo/1km"):
        print(f"  {d.name:<13} {d.spec_hash[:12]}…  {Path(d.asset_url).name}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
