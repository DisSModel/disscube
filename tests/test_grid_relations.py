import pytest
from disscube.models import GridSpec, SpatialRelation, SpatialDerivation, Variable
from disscube.client import CubeClient

def test_cell_id_round_trip():
    grid = GridSpec(
        id="bdc_sm",
        type="reference",
        crs="EPSG:31984",
        resolution=100.0,
        bbox=[0, 0, 1000, 1000] # minx, miny, maxx, maxy
    )
    
    cell = grid.cell_id(row=9, col=4)
    assert cell == "bdc_sm:R0009C0004"
    
    grid_id, row, col = GridSpec.parse_cell_id(cell)
    assert (grid_id, row, col) == ("bdc_sm", 9, 4)

def test_cell_id_coords_round_trip():
    # Grid North-Up
    # bbox [minx, miny, maxx, maxy]
    grid = GridSpec(
        id="bdc_sm",
        type="reference",
        crs="EPSG:31984",
        resolution=100.0,
        bbox=[500000, 7000000, 600000, 7100000]
    )
    
    # Coordinate at the center of cell (0,0)
    # Row 0 is at maxy = 7100000
    # Col 0 is at minx = 500000
    x0, y0 = 500050, 7099950 
    
    cell = grid.cell_id_from_coords(x0, y0)
    assert cell == "bdc_sm:R0000C0000"
    
    cx, cy = grid.coords_from_cell_id(cell)
    assert abs(cx - x0) < 1e-6
    assert abs(cy - y0) < 1e-6

    # Test with row 1, col 1
    x1, y1 = 500150, 7099850
    cell1 = grid.cell_id_from_coords(x1, y1)
    assert cell1 == "bdc_sm:R0001C0001"

def test_multiple_relations_and_bidirectional(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    store_path = tmp_path / "store"
    store_path.mkdir()
    
    cube = CubeClient(str(catalog_path), str(store_path))
    
    cube.register_relation(SpatialRelation(
        source_grid_id="br_mangue_100m",
        target_grid_id="bdc_sm",
        strategy="keepinboth",
        params={"min_intersection": 0.01}
    ))
    
    cube.register_relation(SpatialRelation(
        source_grid_id="br_mangue_100m",
        target_grid_id="modelo_nacional_10km",
        strategy="keepinboth"
    ))
    
    # Teste busca por source
    relations = cube.get_relations("br_mangue_100m")
    assert len(relations) == 2
    
    # Teste busca por target (bidirecional)
    relations_bdc = cube.get_relations("bdc_sm")
    assert len(relations_bdc) == 1
    assert relations_bdc[0].source_grid_id == "br_mangue_100m"

def test_spec_hash_stable_regardless_of_relations():
    """Relations are excluded from spec_hash: they are not used in computation.

    Two derivations identical in every computational aspect (source, grid, role,
    variables, temporal window) must produce the same spec_hash even if their
    SpatialRelation lists differ. This is the correct behaviour: including
    relations would make the cache key sensitive to metadata that does not
    affect the output.
    """
    v = [Variable(name="test", operator="mean")]

    deriv_no_rel = SpatialDerivation(
        source_id="src1", grid_id="grid1", role="test",
        variables=v, relations=[],
    )
    deriv_with_rel = SpatialDerivation(
        source_id="src1", grid_id="grid1", role="test",
        variables=v,
        relations=[SpatialRelation(source_grid_id="grid1",
                                   target_grid_id="grid2",
                                   strategy="simple")],
    )
    deriv_other_rel = SpatialDerivation(
        source_id="src1", grid_id="grid1", role="test",
        variables=v,
        relations=[SpatialRelation(source_grid_id="grid1",
                                   target_grid_id="grid2",
                                   strategy="keepinboth")],
    )

    assert deriv_no_rel.spec_hash() == deriv_with_rel.spec_hash()
    assert deriv_with_rel.spec_hash() == deriv_other_rel.spec_hash()

if __name__ == "__main__":
    pytest.main([__file__])
