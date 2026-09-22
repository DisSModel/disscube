# Grids and Spatial Interoperability

## The problem snapped grids solve

In traditional GIS workflows, creating one grid for Acre and another for Brazil often produces misaligned pixels — the corner of a 5 km Acre pixel does not coincide with the corner of a 5 km Brazil pixel. Aggregating from one grid to the other then introduces errors from partial pixels at the edges.

DisSCube solves this by **snapping to a virtual mesh**: every local grid is anchored to the same CRS origin, guaranteeing that pixels at multiple resolutions always line up exactly.

## National reference grids

```python
from disscube.utils.grids import register_simulation_grids

register_simulation_grids(cube)
# Registers:
#   BR/5km  — 5000 m, BDC Albers, national bbox
#   BR/1km  — 1000 m, BDC Albers, national bbox
```

## Snapped local grids

```python
from disscube.utils.grids import register_local_grid

grid = register_local_grid(
    cube,
    name="AC",                            # or state="AC"
    bbox_geo=(-73.99, -11.15, -66.62, -7.11),  # lon_min, lat_min, lon_max, lat_max
    resolution=5_000.0,
    snap=True,                            # default
)
# Produces grid "AC/5km" in BDC Albers
```

**What `snap=True` does:**

The geographic bbox is converted to BDC Albers and its bounds are rounded to the nearest multiple of the resolution:

```python
minx = math.floor(minx / resolution) * resolution
maxx = math.ceil(maxx  / resolution) * resolution
```

Result: `AC/5km` and `BR/5km` have identical pixels over the overlapping area — a 5 km Acre pixel is the same geographic square as the 5 km Brazil pixel.

## Resolution hierarchy

Multiple resolutions within the same CRS form a perfect hierarchy:

```
1 pixel at 5km
├── 25 pixels at 1km (5×5)
└── 2500 pixels at 100m (50×50)
```

This enables **zero-error aggregation**: when computing the forest `percentage` (100 m) inside a model cell (5 km), every 100 m pixel that makes up the 5 km cell is known exactly.

## Spatial relations

`SpatialRelation` records the parent–child relation between grids:

```python
from disscube.models import SpatialRelation

cube.register_relation(SpatialRelation(
    source_grid_id="AC/1km",
    target_grid_id="AC/5km",
    strategy="simple",
))
```

**Available strategies** (reserved for future use in the pipeline):

| Strategy | Intended use |
|---|---|
| `simple` | Nested grids — no ambiguity in aggregation |
| `chooseone` | Cells that belong to only one target grid |
| `keepinboth` | Cells kept in both grids (overlap) |

!!! note "Strategy status"
    The strategies are modeled in the schema but are not yet applied by the derivation pipeline. They are reserved for DisSModel's cross-scale mechanism.

## Creating a grid manually

When snapping to the BDC mesh is not needed (e.g. a project with a local CRS):

```python
from disscube.models import GridSpec

grid = GridSpec(
    id="local_project/30m",
    type="local",
    crs="EPSG:31983",
    resolution=30.0,
    bbox=[580000.0, 9700000.0, 600000.0, 9720000.0],
    description="Manual grid, not snapped to the national mesh",
)
cube.register_grid(grid)
```

!!! warning
    Unsnapped grids may not align with the national grids. Cross-grid aggregation between misaligned grids introduces partial-pixel errors.

## Derived properties of `GridSpec`

```python
grid = GridSpec(id="G", type="local", crs="EPSG:31982", resolution=100, bbox=[0,0,1000,1000])

grid.rows        # 10
grid.cols        # 10
grid.transform   # Affine (north-up, origin at the upper-left corner)
grid.xs          # array of X centroids, one per column
grid.ys          # array of Y centroids, one per row

# Cell identification
cell = grid.cell_id(row=3, col=7)          # "G:R0003C0007"
x, y = grid.coords_from_cell_id(cell)     # centroid in CRS coordinates
```
