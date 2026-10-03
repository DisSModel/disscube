"""
04 — Network Connectivity (TerraME GPM Network replication):
Least-cost path travel distance to destinations via a transport network.

Replicates the behavior of TerraME's GPM (Generalized Proximity Matrix) Network
connectivity algorithm, used in LuccME-BR to derive market and port accessibility
(e_connmkt and e_connport).

Demonstrates:
  - Defining linear transport infrastructure (roads/railways) with surface impedance.
  - Computing multi-target shortest paths with Dijkstra graph optimization.
  - Accounting for off-road Euclidean access distance from cell centres to network.

Usage:
    python examples/04_network_connectivity.py
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point

from disscube import CubeClient, Derivation, GridSpec, SpatialSource


def run_example():
    print("=" * 70)
    print(" DisSCube Example 04: Network Connectivity (GPM Network)")
    print("=" * 70)

    # 1. Grid specification (50 km cells in SIRGAS 2000 Polyconic / EPSG:5880)
    grid = GridSpec(
        id="regional_grid",
        type="local",
        crs="EPSG:5880",
        resolution=50000,
        bbox=[4000000, 7000000, 4500000, 7500000],
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir) / "workspace"
        catalog = Path(tmp_dir) / "catalog.db"
        cube = CubeClient(catalog=str(catalog), store=str(workspace))
        cube.register_grid(grid)

        # 2. Synthetic road network with paved trunk line and unpaved feeders
        trunk = LineString([(4050000, 7250000), (4250000, 7250000), (4450000, 7250000)])
        feeder_north = LineString([(4250000, 7250000), (4250000, 7450000)])
        feeder_south = LineString([(4250000, 7250000), (4250000, 7050000)])

        roads_gdf = gpd.GeoDataFrame(
            [
                {"geometry": trunk, "status": "paved"},
                {"geometry": feeder_north, "status": "unpaved"},
                {"geometry": feeder_south, "status": "unpaved"},
            ],
            crs="EPSG:5880",
        )
        roads_file = Path(tmp_dir) / "roads.geojson"
        roads_gdf.to_file(roads_file, driver="GeoJSON")

        cube.register_spatial_source(
            SpatialSource(
                id="roads",
                name="Road Network",
                format="vector",
                crs="EPSG:5880",
                asset_url=str(roads_file),
            )
        )

        # 3. Destination port at the eastern terminus of the highway
        port_coords = [4450000, 7250000]

        print("Deriving network connectivity with operator='network_cost'...")
        cube.derive_declarative(
            Derivation(
                target="port_accessibility",
                source_id="roads",
                operator="network_cost",
                params={
                    "targets": [port_coords],
                    "status_column": "status",
                    "inside_paved": 1.0,
                    "inside_unpaved": 2.5,
                    "outside": 2.0,
                    "crs": "EPSG:5880",
                },
            ),
            grid_id="regional_grid",
        )

        cost_array = cube.load("port_accessibility", grid_id="regional_grid")
        values = cost_array.values

        print(f"Output grid dimensions: {values.shape} (rows, cols)")
        print(f"Minimum cost (at the port): {np.nanmin(values)/1000:.1f} km")
        print(f"Maximum cost (furthest off-road cell): {np.nanmax(values)/1000:.1f} km")
        print(f"Mean network cost: {np.nanmean(values)/1000:.1f} km")
        print("Network connectivity derived successfully!")
        print("=" * 70)


if __name__ == "__main__":
    run_example()
    