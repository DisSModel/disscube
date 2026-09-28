from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialDerivation, SpatialRelation, Variable


def test_derive_no_mutation(tmp_path):
    catalog_file = tmp_path / "catalog.json"
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    
    client = CubeClient(str(catalog_file), str(store_dir))
    
    # Setup catalog
    grid = GridSpec(id="G1", type="local", crs="EPSG:31982", resolution=10, bbox=[0,0,100,100])
    client.register_grid(grid)
    
    relation = SpatialRelation(source_grid_id="G1", target_grid_id="G2", strategy="simple")
    client.register_relation(relation)
    
    # Input derivation without relations
    derivation = SpatialDerivation(
        source_id="S1",
        grid_id="G1",
        role="test",
        variables=[Variable(name="V1", operator="mean")],
        relations=[]
    )
    
    # Store a copy of original state
    original_relations = list(derivation.relations)
    
    try:
        # This will fail because S1 is not registered, but we want to check if relations was mutated before failure
        client.derive(derivation)
    except Exception:  # noqa: BLE001, S110 - failure is expected; we only assert on mutation
        pass
    
    assert derivation.relations == original_relations
    assert len(derivation.relations) == 0

def test_spec_hash_robustness():
    # Check that metadata does not affect hash
    d1 = SpatialDerivation(
        source_id="S1", grid_id="G1", role="test",
        variables=[Variable(name="V1", operator="mean")],
        relations=[SpatialRelation(source_grid_id="G1", target_grid_id="G2", strategy="simple", metadata={"note": "A"})]
    )
    d2 = SpatialDerivation(
        source_id="S1", grid_id="G1", role="test",
        variables=[Variable(name="V1", operator="mean")],
        relations=[SpatialRelation(source_grid_id="G1", target_grid_id="G2", strategy="simple", metadata={"note": "B"})]
    )
    
    assert d1.spec_hash() == d2.spec_hash()

    # Check that variable order does not affect hash (wait, I implemented sorting by name)
    d3 = SpatialDerivation(
        source_id="S1", grid_id="G1", role="test",
        variables=[Variable(name="V1", operator="mean"), Variable(name="V2", operator="sum")],
        relations=[]
    )
    d4 = SpatialDerivation(
        source_id="S1", grid_id="G1", role="test",
        variables=[Variable(name="V2", operator="sum"), Variable(name="V1", operator="mean")],
        relations=[]
    )
    assert d3.spec_hash() == d4.spec_hash()
