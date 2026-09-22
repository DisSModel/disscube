"""
examples/drivers/01_brazil_national.py

Derives spatial driver variables on the national BR/5km grid:
  - slope          → mean slope per cell
  - presenca_ti    → indigenous land presence
  - dist_cidades   → min distance to urban centers
  - dist_rios      → min distance to rivers

Usage:
    python examples/drivers/01_brazil_national.py
"""

from disscube.client import CubeClient
from disscube.models import SpatialDerivation, Variable

GRID_ID = "BR/5km"
cube = CubeClient(catalog="catalog.db", store="./data/")

grid = cube.catalog.get_grid(GRID_ID)
if grid is None:
    raise RuntimeError(f"Grid {GRID_ID!r} not found. Run examples/setup/01_init_catalog.py first.")

# 1. Slope
print("\n--- 1. Slope ---")
cube.derive(SpatialDerivation(
    source_id="slope_brazil", grid_id=GRID_ID, role="driver",
    variables=[Variable(name="slope", operator="mean")]
))

# 2. Indigenous lands
print("\n--- 2. Indigenous lands ---")
cube.derive(SpatialDerivation(
    source_id="terras_indigenas", grid_id=GRID_ID, role="driver",
    variables=[Variable(name="presenca_ti", operator="presence")]
))

# 3. Distance to cities (temporal)
print("\n--- 3. Distance to cities ---")
for start, end in [("2000", "2014"), ("2015", "2025")]:
    cube.derive(SpatialDerivation(
        source_id="urban_centers", grid_id=GRID_ID, role="driver",
        variables=[Variable(name="dist_cidades", operator="min_distance")],
        valid_from=start, valid_until=end
    ))

# 4. Distance to rivers
print("\n--- 4. Distance to rivers ---")
cube.derive(SpatialDerivation(
    source_id="rios_pnlt", grid_id=GRID_ID, role="driver",
    variables=[Variable(name="dist_rios", operator="min_distance")]
))

print("\n=== National drivers ready ===")
