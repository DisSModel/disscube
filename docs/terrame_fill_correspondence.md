# From TerraME *Fill Cells* to DisSCube operators

## Lineage

DisSCube's derivation layer is the conceptual successor of TerraME's
`fillCellularSpace` (the *Fill Cells* operation), reformulated as a
reproducible, catalogued data-cube layer.

In TerraME/LuccME, populating a cellular space with attributes derived from
heterogeneous geographic layers is performed cell by cell, in Lua, through
fill *strategies* (`area`, `presence`, `count`, `distance`, `percentage`,
`majority`, `average`, etc.). DisSCube keeps the same semantic vocabulary but
expresses each strategy as a typed, auto-registered `Operator` that runs over a
catalogued data cube rather than over an in-memory cellular space.

The advance is not the set of operations — those are meant to be faithful to
TerraME, and the [Itaituba benchmark](#parity-with-terrame-the-itaituba-benchmark)
below measures how faithful they are — but the engineering around them:

- **Reproducibility.** Every derived product carries a deterministic
  `spec_hash`; identical specifications always produce the same catalogued
  product.
- **Grid-aligned aggregation.** Categorical and standard-deviation operators
  aggregate from a fine array snapped to the target grid origin, using real
  per-cell windows, so target resolutions that are not integer multiples of the
  source (e.g. 30 m → 1000 m) are well defined rather than silently
  approximated.
- **Explicit cell purity.** Coverage purity (valid / total pixels) and, for
  categorical operators, dominance purity (dominant-class fraction) are computed
  per cell. They are first-class metadata travelling with the variable, ready
  for a future masking policy. Purity is implicit in TerraME; here it is named
  and measurable — matching the parameter the INPE e-Cube design also elevates.
- **CRS robustness.** Aggregation is validated on real projected CRSs,
  including the BDC Brazil Albers grid, with named-WKT serialization to avoid
  `PROJCS["unknown"]` round-trip problems.

## Strategy → operator correspondence

| TerraME fill strategy | DisSCube operator (`name`) | Status | Notes |
|---|---|---|---|
| `presence` | `presence` | implemented | Binary mask: 1 where any feature is present. |
| `area` / `coverage` / `percentage` | `percentage` | implemented (window-based); **parity verified** | Fraction (0..1) of the target class per cell, **over valid pixels**. TerraME divides by the whole cell instead; `percentage × coverage_purity` reproduces TerraME's value (see the benchmark). Requires `class_code`. |
| `majority` / `mode` | `majority` | implemented (window-based) | Dominant class per cell; ties resolve to the smallest class value. |
| `minority` | `minority` | implemented (window-based) | Least-frequent class per cell. |
| `count` | `count` | implemented | Count of features per cell (proximity operator). |
| `distance` | `min_distance` | **approximation — semantics differ** | Rasterizes the features on the target grid and takes the Euclidean distance transform between cell centres (EDT × resolution). TerraME measures the exact distance from each cell polygon to the nearest feature, so `min_distance` overestimates it by up to about one cell (see the benchmark). |
| `average` / `mean` | `mean` | implemented; **parity verified** | Mean value per cell (continuous, area-weighted resampling). |
| `sum` (raster) | `sum` | implemented | Sum per cell (continuous). |
| `sum` with `area = true` (polygons) | — | **not implemented** | Distributes a polygon attribute (e.g. census population) over cells in proportion to the intersected area. `sum` accepts raster sources only. |
| `minimum` | `min` | implemented | Minimum per cell. |
| `maximum` | `max` | implemented | Maximum per cell. |
| `stdev` / `standardDeviation` | `std` | implemented (window-based) | True per-cell standard deviation over valid pixels. |
| `attribute` (value copy) | `attribute` | implemented (vector) | Rasterize a numeric vector column whose name matches the variable. |

"Parity verified" means the operator was compared cell by cell with TerraME's
own output in the Itaituba benchmark below; the other rows are correspondences
by design that have not yet been measured against TerraME.

## Aggregation path by operator type

- **Continuous, resampling-expressible** (`mean`, `sum`, `min`, `max`): aligned
  to the target grid directly via the corresponding rasterio resampling method.
- **Categorical** (`percentage`, `majority`, `minority`) and **`std`**: aligned
  to a fine, origin-snapped grid with nearest resampling (never averaging a
  class code), then reduced per target cell over real windows. These operators
  set `needs_fine_alignment = True`.
- **Vector** (`presence`, `attribute`, and the vector branch of the categorical
  operators): reprojected and clipped to the grid bounding box, then rasterized.

## Parity with TerraME: the Itaituba benchmark

TerraME's `gis` package ships the data of its *Fill* tutorial
([wiki](https://github.com/TerraME/terrame/wiki/Fill)) together with the
script `itaituba.lua` and its output, `itaituba.shp`: a 5 km cellular space
(31 × 20 = 620 cells, SAD69 / UTM 21S, EPSG:29191) already filled by TerraME.
That output is used here as the reference: the same inputs are derived with
DisSCube on the same grid and compared cell by cell.

**Setup.** Grid `bbox = [547177.35, 9485214.19, 702177.35, 9585214.19]`,
resolution 5000 m, taken from `itaituba.shp` (whose `.prj` is wrong —
`NAD83_Austin` — so EPSG:29191 is assigned explicitly). TerraME numbers rows
from the south (`row = 0` is the southern row); DisSCube is north-up, so
TerraME `(row, col)` maps to DisSCube `(19 − row, col)`. Coverage values are
compared in percent (DisSCube fraction × 100).

| TerraME fill | DisSCube | Mean abs. error | Max abs. error | Cells within tolerance |
|---|---|---|---|---|
| `elevation` — `average` (923 m raster) | `mean` | 0.93 m | 9.96 m | 67 % within 1 m (r = 0.9995) |
| `defor_7` — `coverage` (60 m raster) | `percentage` | 2.30 pp | 53.57 pp | 92 % within 1 pp |
| `defor_7` | `percentage × coverage_purity` | 0.09 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_87` | `percentage × coverage_purity` | 0.06 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_167` | `percentage × coverage_purity` | 0.01 pp | 0.39 pp | **100 %** within 1 pp |
| `defor_255` | `percentage × coverage_purity` | 0.10 pp | 2.42 pp | 94 % within 1 pp — see nodata issue below |
| `distroad` — `distance` (lines) | `min_distance` | 1 782 m | 5 891 m | biased +1 782 m (r = 0.983) |
| `distlocal` — `distance` (points) | `min_distance` | 2 499 m | 6 871 m | biased +2 497 m (r = 0.986) |
| `population` — `sum`, `area = true` | — | — | — | not supported |

**Reading the results.**

- **Continuous averages agree.** The residual in `elevation` comes from the
  averaging rule (area-weighted resampling vs. TerraME's per-pixel average) on
  a coarse 923 m source.
- **Coverage agrees once the denominator is made explicit.** Raw
  `percentage` differs only in the 50 border cells (last column and top row)
  that the raster covers partially: TerraME divides the class area by the
  *whole cell*, so its classes sum to less than 100 % there, while DisSCube
  divides by the *valid pixels* and reports the covered share separately as
  `coverage_purity`. Their product reproduces TerraME within 0.64 pp in every
  cell. The difference is a design choice, not an error: DisSCube keeps "how
  much of the cell is class *k*" apart from "how much of the cell has data",
  and TerraME's value is recoverable exactly.
- **Distances differ by construction.** Recomputing the exact distance from
  each cell polygon to the nearest road reproduces `distroad` exactly (±0.5 m)
  in 80 % of the cells (mean error 52 m), which identifies TerraME's semantics.
  `min_distance` instead measures between rasterized cell centres, so at 5 km
  it overestimates by about a third to a half of a cell on average.
- **Nodata collision (bug).** The deforestation raster declares no nodata.
  When such a `uint8` source is reprojected, the out-of-extent fill value is
  255 and is then treated as nodata, so the legitimate class 255 is dropped
  (94 % of cells within 1 pp instead of 100 %). With a nodata value declared
  on the source (e.g. 0, unused here), `defor_255` also reaches 100 % within
  1 pp (max 0.02 pp).

## Known gaps relative to TerraME

- **Exact vector distance.** TerraME's `distance` is measured from the cell
  polygon to the nearest feature; `min_distance` is a raster approximation
  between cell centres (see the benchmark). An exact vector operator is needed
  for parity.
- **Area-weighted vector aggregation.** TerraME's `sum` with `area = true` (and,
  more generally, fractional-coverage strategies on polygons) has no DisSCube
  equivalent yet: vector sources are rasterized rather than area-weighted, and
  `sum` accepts raster sources only. The raster-fine path remains the
  recommended route for fractional drivers.
- **Nodata on sources without a declared nodata value.** As shown above, the
  reprojection fill value can collide with a real class (255 for `uint8`).
  Until this is fixed, declare the nodata value of categorical rasters
  explicitly.
- **In-memory, single-tile by design.** The fine-alignment path materializes a
  fine array in memory; very large tiles at a high fine/target ratio are bounded
  by available memory. Distributed/lazy execution is a roadmap item, not a
  current capability.

## Positioning statement

> The filling of cellular spaces from heterogeneous geographic data — the core
> operation of TerraME's `fillCellularSpace` — is reformulated in DisSCube as a
> reproducible spatial-derivation layer: fill strategies become typed operators
> over a catalogued data cube, with aggregation on windows aligned to the target
> grid and explicit control of cell purity. On TerraME's own Itaituba tutorial
> data, raster averages and class coverage reproduce TerraME's output cell by
> cell (coverage within 0.64 pp in all 620 cells once cell purity is applied);
> exact vector distance and area-weighted polygon sums are the remaining gaps.
