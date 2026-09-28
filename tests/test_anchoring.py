import os

from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialRelation

# Initialize client
if os.path.exists("test_catalog.json"):
    os.remove("test_catalog.json")
cube = CubeClient(catalog="test_catalog.json", store="./data/")

# 1. Register BDC Reference Grid (Parent 0)
bdc_tile = GridSpec(
    id="BDC_SM_089094",
    type="reference",
    crs="EPSG:200000",
    resolution=10.0,
    bbox=[5000000, 10000000, 5105600, 10105600],
    description="BDC SM Tile 089094"
)
cube.register_grid(bdc_tile)
print(f"Registered: {bdc_tile.id}")

# 2. Register 5km Grid
grid_5km = GridSpec(
    id="REGIONAL_5KM",
    type="local",
    crs="EPSG:200000",
    resolution=5000.0,
    bbox=[5010000, 10010000, 5060000, 10060000],
    description="Regional 5km model"
)
cube.register_grid(grid_5km)
cube.register_relation(SpatialRelation(
    source_grid_id="REGIONAL_5KM",
    target_grid_id="BDC_SM_089094",
    strategy="simple"
))
print(f"Registered: {grid_5km.id}")

# 3. Register 1km Grid
grid_1km = GridSpec(
    id="LOCAL_1KM",
    type="local",
    crs="EPSG:200000",
    resolution=1000.0,
    bbox=[5020000, 10020000, 5030000, 10030000],
    description="Local 1km model"
)
cube.register_grid(grid_1km)
cube.register_relation(SpatialRelation(
    source_grid_id="LOCAL_1KM",
    target_grid_id="REGIONAL_5KM",
    strategy="simple"
))
print(f"Registered: {grid_1km.id}")

# 4. Verify Catalog Persistence
print("\nVerifying catalog content...")
all_grids = cube.catalog.list_grids()
for g in all_grids:
    relations = cube.get_relations(g.id)
    rel_info = ""
    for r in relations:
        if r.source_grid_id == g.id:
            rel_info += f" -> Relates to: {r.target_grid_id} ({r.strategy})"
    print(f"- {g.id} [{g.type}]{rel_info}")

# 5. Simple lineage check
def get_lineage(grid_id, cube):
    lineage = [grid_id]
    relations = cube.get_relations(grid_id)
    # Find relation where current grid is source
    source_rel = next((r for r in relations if r.source_grid_id == grid_id), None)
    while source_rel:
        lineage.append(source_rel.target_grid_id)
        grid_id = source_rel.target_grid_id
        relations = cube.get_relations(grid_id)
        source_rel = next((r for r in relations if r.source_grid_id == grid_id), None)
    return lineage

print(f"\nLineage for LOCAL_1KM: {' -> '.join(get_lineage('LOCAL_1KM', cube))}")
