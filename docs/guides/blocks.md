# Block-wise reading

A national raster does not fit in memory: one year of MapBiomas for Brazil has 154,470 × 146,501 pixels, 22.6 GB at one
byte per pixel. DisSCube does not load it. It processes the grid in **blocks of rows and columns**, and the peak
memory depends on the size of a block, not on the size of the source.

## What happens in a block

For each block of grid cells DisSCube

1. finds the window of the source that covers the block (in the source's CRS, clipped to the source);
2. reads only that window — MapBiomas and PRODES are tiled GeoTIFFs, so the rest is not fetched;
3. reprojects it with GDAL to the grid projection (nearest neighbour onto fine pixels for the class operators, which
   count them; averaging for `mean`);
4. aggregates the pixels into the values of the block's cells, and writes them to the product.

The blocks of one source are written to the same product, so the result is the grid, not a set of tiles.

## How big a block is

The budget is the number of **source pixels** aligned at once: 50 million by default. It is measured, not assumed:
a strip of grid rows that is wide in a projected grid can cover a far larger area of a longitude/latitude source than
its height suggests. So DisSCube starts from the whole grid and halves a block along its larger side until the block
that covers the most source pixels is within the budget. If the whole grid is within it, there are no blocks.

Change the budget with the environment variable `DISSCUBE_BLOCK_PIXELS` (or `block_pixels=` in
`CubeClient.derive`): a smaller budget lowers the peak and makes more, smaller reads.

## When a source is not read by blocks

A source is derived whole when

- it is **not a raster** (a vector layer is reprojected, clipped to the grid and used whole; it is small);
- a variable uses an operator that is not block-safe. The block-safe ones are `majority`, `minority`, `percentage`,
  `mean`, `median`, `std`, `sum`, `min` and `max`. `distance`, `min_distance`, `count`, `area`, `attribute`, `presence` and
  `network_cost` look at neighbours or at the whole layer;
- a variable uses `fill = "nearest"`, because a cell then depends on its neighbours;
- the file cannot be opened as a plain raster (a NetCDF subdataset, some URLs).

The Brazil Data Cube source (STAC) is not block-read either: the window of each item is read whole, and the items of the
period are reduced to a per-pixel `median` (or the `reducer` of the source) before the aggregation. It is the case with
the highest peak of the runs below, from a small source.

## Time

Each derived variable re-reads and re-aligns the source, so a pipeline with several variables from the same raster
makes several passes over it. Reading is what takes the time.

## Measured runs

On one core of a desktop computer (Intel Core i7-7700T, 15 GB of RAM), reported in
[disscube-grids](https://github.com/LambdaGeo/disscube-grids), one folder per case:

| Case | Source | Grid | Time | Peak memory |
|---|---|---|---|---|
| Brazil | MapBiomas C11, 2000 (22.6 × 10⁹ pixels) | 5.28 km; 900 × 1,040 | 1 h 43 min | 3.9 GB |
| Legal Amazon | PRODES, 2008 and 2024 (≈ 9.7 × 10⁹ pixels each) | 5.28 km; 680 × 520 | 27 min | 2.9 GB |
| Pará, classes | MapBiomas 2024 and PRODES 2024 | 5 km; 280 × 291 | 2 min 12 s | 1.2 GB |
| Pará, NDVI | BDC MODIS NDVI, 16-day composites | 5 km; 280 × 291 | 7 min 15 s | 4.2 GB (source not block-read) |

Each case was run once; the times show that the run is feasible, not how fast it is compared with other tools. Peak
memory is the maximum resident set size reported by `/usr/bin/time -v`.
