# MapBiomas

[MapBiomas](https://brasil.mapbiomas.org/) publishes annual land-cover and
land-use maps of Brazil. `disscube.sources.mapbiomas` reads them straight from
the national GeoTIFFs on Google Cloud Storage: the files are tiled in 256 × 256
blocks, so only the pixels of the area of interest are fetched.

| Dataset | Resolution | Years | Call with |
|---|---|---|---|
| Collection 11 (Landsat) — default | 30 m | 1985–2025 | `collection=11, resolution=30` |
| Collection 4 of the 10 m series (Sentinel-2) | 10 m | 2017–2025 | `collection=4, resolution=10` |

For land-change modelling the 30 m collection is the natural choice: it has
the long series needed for calibration and validation years, and the same
resolution as the Landsat cubes of the Brazil Data Cube.

## Registering a year

```python
from disscube.sources.mapbiomas import register_mapbiomas_source

bbox = (-44.35, -2.62, -44.20, -2.47)          # WGS84
for year in (2000, 2020):
    register_mapbiomas_source(cube, f"lulc_{year}", year, bbox, out_dir="raw")
# raw/lulc_2020.tif               uint8 class codes, nodata 0, EPSG:4326
# raw/lulc_2020.provenance.json   dataset, collection, resolution, year, URL,
#                                 bbox, legend, license, checksum, software
```

Each year becomes a source with `time=year`, so derivations from several years
load as a `(time, y, x)` series and go to DisSModel with `to_lucc_data()`:

```python
from disscube import Derivation

for year in (2000, 2020):
    cube.derive_declarative(Derivation(target="landuse", source_id=f"lulc_{year}",
                                       operator="majority"), grid_id=grid.id)
    cube.derive_declarative(Derivation(target="urban_pct", source_id=f"lulc_{year}",
                                       operator="percentage", class_code=24), grid_id=grid.id)

backend = cube.to_lucc_data(["landuse", "urban_pct"], grid_id=grid.id)
```

## Code 0 is nodata

The files declare no nodata value, but code 0 means "not observed" (for
example open sea outside the mapped area). The reader treats 0 as nodata and
writes the local file with `nodata=0`, so it never counts as a class in
`majority` or `percentage`.

## Classes

`disscube.sources.mapbiomas.CLASSES` names the classes most common in
land-change studies (forest formation 3, savanna 4, mangrove 5, floodable
forest 6, wetland 11, grassland 12, pasture 15, mosaic of uses 21, beach and
dune 23, urban area 24, other non-vegetated 25, hypersaline tidal flat 32,
water 33). The authoritative list is the legend of the collection in use:
<https://brasil.mapbiomas.org/en/codigos-de-legenda/>.

Data © MapBiomas, licensed CC-BY-4.0; cite the collection you use.
