from disscube.models import SpatialDerivation, Variable


def test_spec_hash_stability():
    derivation1 = SpatialDerivation(
        source_id="src1",
        grid_id="grid1",
        role="driver",
        variables=[
            Variable(name="v1", operator="mean"),
            Variable(name="v2", operator="min_distance")
        ]
    )
    
    derivation2 = SpatialDerivation(
        grid_id="grid1",
        source_id="src1",
        role="driver",
        variables=[
            Variable(name="v1", operator="mean"),
            Variable(name="v2", operator="min_distance")
        ]
    )
    
    # Hash should be the same even if keys are in different order in constructor (Pydantic handles this)
    assert derivation1.spec_hash() == derivation2.spec_hash()
    
    derivation3 = SpatialDerivation(
        source_id="src1",
        grid_id="grid1",
        role="driver",
        variables=[
            Variable(name="v2", operator="min_distance"),
            Variable(name="v1", operator="mean")
        ]
    )
    
    # Different order of variables should now result in the same hash (robust hashing)
    assert derivation1.spec_hash() == derivation3.spec_hash()

def test_spec_hash_determinism():
    derivation = SpatialDerivation(
        source_id="src1",
        grid_id="grid1",
        role="driver",
        variables=[Variable(name="v1", operator="mean")]
    )
    
    h1 = derivation.spec_hash()
    h2 = derivation.spec_hash()
    assert h1 == h2
    assert len(h1) == 64 # SHA-256
