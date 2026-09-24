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

!!! note "Tiles index geometry, not data"
    `bdc_importer` indexes the BDC grid and its tiles (geometry and metadata). The registered tile `SpatialSource`s have a placeholder `asset_url` (`data/bdc/<LEVEL>/<tile>.tif`) and cannot be loaded as raster data. To bring BDC *data* in, use `disscube.utils.bdc_stac` (next section), which writes local GeoTIFFs you register as ordinary sources.

## Reading data cubes via STAC

The BDC publishes analysis-ready 16-day data cubes — `LANDSAT-16D-1` (30 m,
from 1990), `S2-16D-2` (10 m, from 2017) and `CBERS4-WFI-16D-2` (64 m, from
2016) — as Cloud-Optimized GeoTIFFs indexed by a public STAC API
(`https://data.inpe.br/bdc/stac/v1/`). Each item carries spectral bands and
ready-made indices (`NDVI`, `EVI`; `NBR` for Sentinel-2).
`disscube.utils.bdc_stac` reads only the pixels of an area of interest, so an
area of a few kilometres costs a few HTTP range requests per item instead of a
full tile download. Searching needs `pystac-client` (`pip install disscube[bdc]`).

```python
from disscube.utils.bdc_stac import (
    fetch_composite, normalized_difference, read_composite, search_items, write_geotiff,
)

bbox = (-44.35, -2.62, -44.20, -2.47)          # WGS84
period = "2020-07-01/2020-09-30"

# one asset, one call: search → windowed reads → per-pixel median → GeoTIFF
fetch_composite("LANDSAT-16D-1", "NDVI", bbox, period, "raw/ndvi.tif")

# several assets of the same items: search once, derive an index
items = search_items("LANDSAT-16D-1", bbox, period)
green = read_composite("LANDSAT-16D-1", "green", bbox, period, items=items)
swir = read_composite("LANDSAT-16D-1", "swir16", bbox, period, items=items)
write_geotiff(normalized_difference(green, swir), "raw/mndwi.tif")   # MNDWI
```

The GeoTIFFs are then registered as `SpatialSource`s and aggregated like any
other raster (see `examples/07_bdc_cube.py`).

What the reader does for you:

- **Physical units.** Nodata becomes NaN, and the scale/offset declared by the
  asset (STAC `raster:bands`, or the GeoTIFF itself) is applied. When neither
  declares one, raw values are kept — check the range (BDC stores indices as
  int16 × 10 000).
- **Composites and mosaics.** Items are reduced tile by tile (`median`, `mean`,
  `max` or `min`, ignoring NaN, i.e. clouds); when the area spans several BDC
  tiles, the per-tile composites are pasted into one layer (the tiles share one
  pixel mesh, so nothing is resampled).
- **A portable CRS.** The cubes declare BDC Albers as `EPSG:10857`, a code
  registered in 2023 and missing from older PROJ databases. Outputs are written
  with the proj4 definition in `disscube.utils.grids.BDC_CRS` instead, which
  every PROJ version reads.

!!! note "Not included"
    Land-use/land-cover classification products are not published in this STAC
    catalog, so the cubes serve as observations (vegetation and water indices,
    reflectance) rather than as land-use maps. Derived variables are not
    published back as STAC.

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
