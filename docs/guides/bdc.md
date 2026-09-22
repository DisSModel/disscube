# Brazil Data Cube Integration

## BDC grids and tiles

The Brazil Data Cube (BDC) partitions Brazil into hierarchical tiles. DisSCube represents this with:

- **Master Grid**: definition of the resolution and CRS for the whole country.
- **Tiles**: a `SpatialSource` carrying the `bbox` of each partition.

## Registering BDC grids and tiles

The `bdc_importer` utility registers the national simulation grids (`BR/5km`, `BR/1km`) and each BDC tile as a `SpatialSource` in the catalog. The BDC Grid V2 shapefiles are bundled with DisSCube (`disscube/data/bdc_grids/`), so no download is needed:

```python
from disscube.utils.bdc_importer import import_bdc_grids

import_bdc_grids(cube)   # bundled BDC_SM_V2, BDC_MD_V2, BDC_LG_V2
```

To use another copy of the grids, pass `sm_path`, `md_path` and/or `lg_path` (any path or `zip://` URL fiona can open).

| Level | Tiles | Tile size | Source ID |
|---|---|---|---|
| SM | 871 | 105.6 km (~1°) | `BDC_SM_<tile>` |
| MD | 242 | 211.2 km (~2°) | `BDC_MD_<tile>` |
| LG | 75 | 422.4 km (~4°) | `BDC_LG_<tile>` |

Tile IDs repeat across levels (e.g. `005004` exists in both SM and MD), so a tile is identified by level and ID. Provenance, checksums and licensing of the bundled files are documented in `disscube/data/bdc_grids/README.md`; the files are © INPE and are not covered by DisSCube's MIT license.

!!! warning "STAC data ingestion — planned"
    `bdc_importer` indexes the BDC grid and its tiles (geometry and metadata), but **does not ingest data via STAC**. The registered `SpatialSource`s have a placeholder `asset_url` (`data/bdc/<LEVEL>/<tile>.tif`) that is not populated, so they cannot be loaded directly as raster data. Integration with the BDC STAC catalog is planned but not yet implemented. To use real BDC data, provide the files locally through a `SpatialSource` whose `asset_url` points to the correct file.

## Per-tile derivation

```python
from disscube.models import SpatialDerivation, Variable

derivation = SpatialDerivation(
    source_id="slope_brazil",
    grid_id="BR/5km",
    role="driver",
    variables=[Variable(name="slope", operator="mean")],
)

# Process one tile
cube.derive(derivation, tile_id="009002")

# Process all SM tiles in a loop
# Tiles are registered with IDs in the format BDC_SM_<tile> (e.g. BDC_SM_009002)
tiles = [s for s in cube.catalog.list_spatial_sources() if s.id.startswith("BDC_SM_")]
for tile_source in tiles:
    tile_id = tile_source.id.split("_")[-1]
    cube.derive(derivation, tile_id=tile_id)
```

Each tile is processed independently and can be parallelized.

## Loading a tiled result

```python
# A specific tile — always works
da = cube.load("slope", tile_id="009002")

# By grid — works only when the result has a single tile
da = cube.load("slope", grid_id="BR/5km")
```

!!! warning "Multi-tile loading"
    `load()` without `tile_id` raises `ValueError` when multiple tiles of the same variable exist on the same grid. Automatic mosaicking is not implemented. **Always pass `tile_id` in multi-tile workloads.**

## National 100 m grid

For projects that need a finer resolution than the `BR/1km` simulation grid, DisSCube supports a custom national 100 m grid. BDC tiles can still be used to partition the processing, since they only define the bbox of each subset:

```python
from disscube.utils.grids import register_local_grid

grid_100m = register_local_grid(
    cube,
    name="BR",
    bbox_geo=(-73.98, -33.75, -28.65, 5.27),  # Brazil bbox in WGS84
    resolution=100.0,
    snap=True,
)
```

## Full workflow: setup → derivation → loading

```python
from disscube.client import CubeClient
from disscube.models import SpatialSource, SpatialDerivation, Variable

cube = CubeClient(catalog="catalog.db", store="./data/")

# 1. Source
cube.register_spatial_source(SpatialSource(
    id="urban_centers",
    name="Centros Urbanos PNLT",
    format="vector",
    asset_url="data/raw/urban_centers.shp",
    crs="EPSG:5880",
))

# 2. Derivation — distance to urban centers on BR/5km
derivation = SpatialDerivation(
    source_id="urban_centers",
    grid_id="BR/5km",
    role="driver",
    variables=[Variable(name="dist_cidades", operator="min_distance")],
    valid_from="2000",
    valid_until="2014",
)

# 3. Run for one tile
cube.derive(derivation, tile_id="009002")

# 4. Load
da = cube.load("dist_cidades", tile_id="009002")
print(da.shape)   # (rows, cols)
```

## Temporal variables with tiles

For time-varying drivers, derive multiple periods and load them as a series:

```python
for start, end in [("2000", "2014"), ("2015", "2025")]:
    cube.derive(SpatialDerivation(
        source_id="urban_centers",
        grid_id="BR/5km",
        role="driver",
        variables=[Variable(name="dist_cidades", operator="min_distance")],
        valid_from=start, valid_until=end,
    ))

# Load the time series (time, y, x)
da = cube.load("dist_cidades", grid_id="BR/5km")
print(da.dims)   # ('time', 'y', 'x')
```
