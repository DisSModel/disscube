from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialRelation


def test_grid_anchoring_and_lineage(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    store_path = tmp_path / "store"
    cube = CubeClient(catalog=str(catalog_path), store=str(store_path))

    # 1. Register BDC Reference Grid (Parent 0)
    bdc_tile = GridSpec(
        id="BDC_SM_089094",
        type="reference",
        crs="EPSG:200000",
        resolution=10.0,
        bbox=[5000000, 10000000, 5105600, 10105600],
        description="BDC SM Tile 089094",
    )
    cube.register_grid(bdc_tile)

    # 2. Register 5km Grid
    grid_5km = GridSpec(
        id="REGIONAL_5KM",
        type="local",
        crs="EPSG:200000",
        resolution=5000.0,
        bbox=[5010000, 10010000, 5060000, 10060000],
        description="Regional 5km model",
    )
    cube.register_grid(grid_5km)
    cube.register_relation(
        SpatialRelation(
            source_grid_id="REGIONAL_5KM",
            target_grid_id="BDC_SM_089094",
            strategy="simple",
        )
    )

    # 3. Register 1km Grid
    grid_1km = GridSpec(
        id="LOCAL_1KM",
        type="local",
        crs="EPSG:200000",
        resolution=1000.0,
        bbox=[5020000, 10020000, 5030000, 10030000],
        description="Local 1km model",
    )
    cube.register_grid(grid_1km)
    cube.register_relation(
        SpatialRelation(
            source_grid_id="LOCAL_1KM",
            target_grid_id="REGIONAL_5KM",
            strategy="simple",
        )
    )

    # 4. Verify Catalog Persistence
    all_grids = cube.catalog.list_grids()
    assert len(all_grids) == 3
    grid_ids = {g.id for g in all_grids}
    assert grid_ids == {"BDC_SM_089094", "REGIONAL_5KM", "LOCAL_1KM"}

    # 5. Lineage check
    def get_lineage(grid_id, c):
        lineage = [grid_id]
        relations = c.get_relations(grid_id)
        source_rel = next((r for r in relations if r.source_grid_id == grid_id), None)
        while source_rel:
            lineage.append(source_rel.target_grid_id)
            grid_id = source_rel.target_grid_id
            relations = c.get_relations(grid_id)
            source_rel = next((r for r in relations if r.source_grid_id == grid_id), None)
        return lineage

    lineage = get_lineage("LOCAL_1KM", cube)
    assert lineage == ["LOCAL_1KM", "REGIONAL_5KM", "BDC_SM_089094"]
