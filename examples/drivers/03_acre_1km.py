"""
examples/drivers/03_acre_1km.py

Derives spatial driver variables for the high-resolution Acre grid (AC/1km):
  - dist_cidades   → min distance to urban centers (PNLT)

Usage:
    python examples/drivers/03_acre_1km.py
"""

from disscube.client import CubeClient
from disscube.models import SpatialDerivation, Variable

GRID_ID = "AC/1km"
cube = CubeClient(catalog="catalog.db", store="./data/")

# Sanity check
grid = cube.catalog.get_grid(GRID_ID)
if grid is None:
    raise RuntimeError(f"Grid {GRID_ID!r} not found. Run examples/setup/01_init_catalog.py first.")

# Distance to cities (temporal)
for start, end in [("2000", "2014"), ("2015", "2025")]:
    print(f"\n--- Distance to cities ({start}-{end}) @ 1km ---")
    cube.derive(SpatialDerivation(
        source_id="urban_centers", grid_id=GRID_ID, role="driver",
        variables=[Variable(name="dist_cidades", operator="min_distance")],
        valid_from=start, valid_until=end
    ))

print("\n=== Acre 1km drivers ready ===")
