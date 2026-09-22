# Tiling and Partitioned Processing

To process data at continental scale (e.g. Brazil at 100 m) without loading the whole country into memory, DisSCube uses a tile-based partitioning model.

## Concept

A **Master Grid** defines the resolution and CRS for the whole country. **Tiles** are subsets with the same CRS and resolution — only the bbox changes. The pipeline runs one tile at a time, producing isolated Zarr stores that can be processed in parallel.

```mermaid
graph TD
    MG[Master Grid: BR/5km] --> T1[Tile 001]
    MG --> T2[Tile 002]
    MG --> TN[Tile N…]
    T1 --> Z1[zarr: .../BR_5km/001/{hash}/var.zarr]
    T2 --> Z2[zarr: .../BR_5km/002/{hash}/var.zarr]
```

## How CubeClient uses tiles

```python
cube.derive(derivation, tile_id="009002")
```

Internally:
1. Fetches the master grid's `GridSpec`.
2. Fetches the `SpatialSource` with id `{grid_id}_{tile_id}` to get the tile `bbox`.
3. Creates a temporary `GridSpec`: same CRS and resolution, bbox restricted to the tile.
4. Runs the pipeline on that temporary grid.
5. Writes to `data/derived/{grid_id}/009002/{spec_hash}/{var}.zarr`.

## Registering tiles

Each tile is registered as a special `SpatialSource` carrying the tile bbox:

```python
from disscube.models import SpatialSource

cube.register_spatial_source(SpatialSource(
    id="BDC_SM_009002",          # convention: {grid_id}_{tile_id}
    name="BDC SM Tile 009002",
    format="raster",
    asset_url="data/raw/tile_009002.tif",
    crs="EPSG:...",
    bbox=[-70.0, -10.0, -65.0, -5.0],   # tile bbox in geographic coordinates
))
```

The `disscube.utils.bdc_importer` utility automates this process for BDC tiles.

## Processing in a loop

```python
tiles = cube.catalog.list_spatial_sources()
tile_ids = [s.id.split("_")[-1] for s in tiles if s.id.startswith("BDC_SM_")]

for tile_id in tile_ids:
    cube.derive(derivation, tile_id=tile_id)
```

> **Note:** `bdc_importer` registers BDC tiles as `SpatialSource`s with IDs in the format
> `BDC_SM_<tile>`. The simulation grid remains `BR/5km` or `BR/1km` — the BDC tiles
> only define the bbox of the subset to process.

Each iteration is independent. Parallel workers can process different tiles without conflicts (Zarr paths are unique per tile + spec_hash).

## Loading tiled data

```python
# Load a specific tile (tile_id always works)
da = cube.load("dist_road", tile_id="009002")

# Load by grid — works when there is only one tile
da = cube.load("dist_road", grid_id="BR/5km")
```

> **Current limitation:** `load()` without `tile_id` raises `ValueError` when multiple
> tiles of the same variable exist on the same grid. Automatic mosaicking is not
> implemented. Always pass `tile_id` in multi-tile workloads.

## Advantages

- **Bounded memory:** processes one tile at a time.
- **Trivial parallelism:** independent workers, no race conditions.
- **Guaranteed consistency:** all tiles derive from the same `GridSpec` — pixels are always aligned.
- **Per-tile caching:** re-running a tile with the same `spec_hash` is a no-op.
