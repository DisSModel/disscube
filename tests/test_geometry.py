import numpy as np
from affine import Affine

from disscube.models import GridSpec


def test_gridspec_properties():
    # Example grid: 100m resolution, 1km x 1km box
    grid = GridSpec(
        id="test_grid",
        type="local",
        crs="EPSG:31982",
        resolution=100.0,
        bbox=[0.0, 0.0, 1000.0, 1000.0] # minx, miny, maxx, maxy
    )
    
    assert grid.rows == 10
    assert grid.cols == 10
    
    # North-up transform: origin at (0, 1000), dx=100, dy=-100
    expected_transform = Affine.translation(0, 1000) @ Affine.scale(100, -100)
    assert grid.transform == expected_transform
    
    # xs should be [50, 150, ..., 950]
    expected_xs = np.arange(10) * 100 + 50
    np.testing.assert_array_equal(grid.xs, expected_xs)
    
    # ys should be [950, 850, ..., 50]
    expected_ys = 1000 - (np.arange(10) * 100 + 50)
    np.testing.assert_array_equal(grid.ys, expected_ys)

def test_gridspec_rounding():
    # Case where division results in something like 10.0000000001 or 9.999999999
    grid = GridSpec(
        id="round_test",
        type="local",
        crs="EPSG:4326",
        resolution=0.0001,
        bbox=[0.0, 0.0, 0.0010000000001, 0.0010000000001]
    )
    assert grid.rows == 10
    assert grid.cols == 10
