# Elevation and slope (DEM)

`type = "dem"` reads a digital elevation model over the grid and gives **elevation** or **slope**
as a raster source, with a checksum and provenance. The usual next step is a zonal operator such
as `mean`.

```toml
[grid]
name = "area"
bbox = [-54.842, -3.587, -54.459, -3.168]
resolution = 500

[[source]]
id      = "slope"
type    = "dem"
dem     = "srtm"            # srtm | copernicus | topodata
product = "slope_deg"       # elevation | slope_deg | slope_pct

[[derive]]
target   = "mean_slope"
source   = "slope"
operator = "mean"
```

## Fields

| Field | Meaning |
|---|---|
| `dem` | `srtm`, `copernicus` or `topodata`. Give this **or** `tiles`. |
| `tiles` | Your own GeoTIFFs (paths relative to the pipeline file, or URLs GDAL reads) instead of a download. |
| `product` | `elevation` (m, default), `slope_deg` or `slope_pct`. |
| `margin` | Degrees added around the grid (default `0.02`): a slope needs the neighbours of the edge pixels. |
| `resolution` | Metres of the grid on which the slope is computed (default `30`). |
| `cache` | Folder for downloaded TOPODATA sheets (default `$DISSCUBE_CACHE/dem` or `~/.cache/disscube/dem`). |

## The three DEMs

| `dem` | What | How it is read |
|---|---|---|
| `srtm` | SRTM 1 arc-second, February 2000 (C-band radar), as republished in the AWS Terrain Tiles. No account. | Only the window over the grid, through HTTP range requests. |
| `copernicus` | Copernicus GLO-30, 2011–2015 (X-band). Free licence, © DLR/Airbus. | Only the window, from the public Cloud-Optimized GeoTIFFs on AWS. |
| `topodata` | INPE's TOPODATA: SRTM refined to 1 arc-second by kriging; Brazil only. | The 1° × 1.5° ZIP sheet is downloaded once into the cache. |

**For slope, prefer SRTM or TOPODATA.** Copernicus GLO-30 is a *surface* model: it includes the
forest canopy, so every forest/pasture edge looks like a ~30 m cliff and the slope is overestimated
exactly where deforestation happens. DisSCube logs a warning when you ask for a Copernicus slope.
In the Lab15 reconstruction, moving from Copernicus to SRTM raised the correlation of the mean slope
with the original from 0.82 to 0.90.

## How the slope is made

An operator aggregates the pixels of a cell and does not see their neighbours, so the slope is
computed in the source: the window is resampled (bilinear) to the UTM zone of the grid's centre at
`resolution` metres, so the slope is metric, and taken by central differences (`numpy.gradient`).
This is not Horn's 3 × 3 formula, so values differ slightly from `gdaldem slope`. Voids (SRTM marks
them −32768) and anything outside −1 000 to 9 000 m become NaN, and the slope is NaN around them.

The GeoTIFF is float32 with NaN as nodata: elevation in EPSG:4326, slopes in UTM.
`raw/<id>.provenance.json` records the DEM, its licence, the tiles, the window, the processing and
the checksum.

## Tiles are not always there

SRTM and Copernicus have no tile over the open sea. A tile that cannot be opened is skipped with a
warning; if none can be read, the run stops with the reasons. Check the warning if the grid reaches
the coast.
