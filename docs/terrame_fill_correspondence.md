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
TerraME, and the [parity benchmarks](#parity-with-terrame) below measure
how faithful they are — but the engineering around them:

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
| `presence` | `presence` | implemented; parity measured (Emas) | Binary mask: 1 where any feature is present. Matches TerraME in 98.4–99.7 % of cells: lines are rasterized through cell centres, while TerraME marks every cell a line touches. |
| `coverage` / `percentage` (raster) | `percentage` | implemented (window-based); **parity verified** (Itaituba, Amazônia) | Fraction (0..1) of the target class per cell, **over valid pixels**. TerraME divides by the whole cell instead; `percentage × coverage_purity` reproduces TerraME's value (see the benchmarks). Requires `class_code`. |
| `area` (polygons) | — | **not implemented** | Fraction of each cell covered by polygons (e.g. protected areas). Semantics confirmed on Amazônia: intersection area / cell area reproduces TerraME in every cell. |
| `majority` / `mode` | `majority` | implemented (window-based) | Dominant class per cell; ties resolve to the smallest class value. |
| `minority` | `minority` | implemented (window-based) | Least-frequent class per cell. |
| `count` | `count` | implemented | Count of features per cell (proximity operator). |
| `distance` | `min_distance` | **approximation — semantics differ** | Rasterizes the features on the target grid and takes the Euclidean distance transform between cell centres (EDT × resolution). TerraME measures the distance from each cell polygon to the nearest feature, so `min_distance` overestimates it by up to about one cell (see the benchmarks). |
| `average` / `mean` | `mean` | implemented; **parity verified** | Mean value per cell (continuous, area-weighted resampling). |
| `sum` (raster) | `sum` | implemented | Sum per cell (continuous). |
| `sum` with `area = true` (polygons) | — | **not implemented** | Distributes a polygon attribute (e.g. census population) over cells in proportion to the intersected area. `sum` accepts raster sources only. |
| `minimum` | `min` | implemented; parity measured (Emas) | Minimum per cell. Matches TerraME in 99.0 % of cells: pixels that straddle a cell border count for both cells, while TerraME assigns each pixel to the cell containing its centre. |
| `maximum` | `max` | implemented; parity measured (Emas) | Maximum per cell. Matches TerraME in 98.7 % of cells, for the same reason as `min`. |
| `stdev` / `standardDeviation` | `std` | implemented (window-based) | True per-cell standard deviation over valid pixels. |
| `attribute` (value copy) | `attribute` | implemented (vector) | Rasterize a numeric vector column whose name matches the variable. |

"Parity verified" means the operator reproduces TerraME's own output cell by
cell in the benchmarks below; "parity measured" means it was compared and
the residual difference is explained; the other rows are correspondences by
design that have not yet been measured against TerraME.

## Aggregation path by operator type

- **Continuous, resampling-expressible** (`mean`, `sum`, `min`, `max`): aligned
  to the target grid directly via the corresponding rasterio resampling method.
- **Categorical** (`percentage`, `majority`, `minority`) and **`std`**: aligned
  to a fine, origin-snapped grid with nearest resampling (never averaging a
  class code), then reduced per target cell over real windows. These operators
  set `needs_fine_alignment = True`.
- **Vector** (`presence`, `attribute`, and the vector branch of the categorical
  operators): reprojected and clipped to the grid bounding box, then rasterized.

## Parity with TerraME

TerraME's `gis` package ships three *Fill* examples — Itaituba (the
[Fill tutorial](https://github.com/TerraME/terrame/wiki/Fill)), Emas and
Amazônia — each with its input layers, the Lua script and the cellular space
TerraME produced. Those outputs are the reference: the same inputs are derived
with DisSCube on the same grid and compared cell by cell.

The data are bundled in `examples/data/terrame/` (provenance, license and
checksums in its README). The comparison runs in CI in
`tests/test_terrame_parity.py` — passing tests pin the parity below, strict
`xfail` tests record the known gaps — and
`examples/04_terrame_fill_itaituba.py` prints the Itaituba table.

### Itaituba — 620 cells, 5 km

A 31 × 20 cellular space in SAD69 / UTM 21S (EPSG:29191), filled from a
923 m elevation raster, a 60 m land-cover raster, roads, localities and census
tracts.

**Setup.** Grid `bbox = [547177.35, 9485214.19, 702177.35, 9585214.19]`,
resolution 5000 m, taken from `itaituba.shp` (whose `.prj` is wrong —
`NAD83_Austin` — so EPSG:29191 is assigned explicitly). TerraME numbers rows
from the south (`row = 0` is the southern row); DisSCube is north-up, so
TerraME `(row, col)` maps to DisSCube `(19 − row, col)`. Coverage values are
compared in percent (DisSCube fraction × 100).

| TerraME fill | DisSCube | Mean abs. error | Max abs. error | Cells within tolerance |
|---|---|---|---|---|
| `elevation` — `average` (923 m raster) | `mean` | 0.89 m | 10.13 m | 72 % within 1 m (r = 0.9995) |
| `defor_7` — `coverage` (60 m raster) | `percentage` | 2.17 pp | 50.13 pp | 92 % within 1 pp |
| `defor_7` | `percentage × coverage_purity` | 0.09 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_87` | `percentage × coverage_purity` | 0.06 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_167` | `percentage × coverage_purity` | 0.01 pp | 0.39 pp | **100 %** within 1 pp |
| `defor_255` | `percentage × coverage_purity` | 0.001 pp | 0.02 pp | **100 %** within 1 pp |
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
- **Rasters without a declared nodata.** The deforestation raster declares
  none, and its classes include 255. Earlier versions reprojected it with the
  `uint8` default fill value (255) and then treated that value as nodata,
  dropping the legitimate class 255 (94 % of cells within 1 pp). Since the
  fix, the fill value can no longer collide with the data, and `defor_255`
  agrees with TerraME in every cell.

### Emas — 5 514 cells, 500 m

Cells of the Emas National Park (EPSG:29192) that touch the park limit,
filled from firebreak and river lines and a 30 m fire-accumulation raster.

| TerraME fill | DisSCube | Cells identical | Cause of the residual |
|---|---|---|---|
| `firebreak` — `presence` (lines) | `presence` | 98.4 % | TerraME marks every cell a line touches (polygon ∩ line reproduces it in 100 % of cells); DisSCube rasterizes through cell centres |
| `river` — `presence` (lines) | `presence` | 99.7 % | same |
| `maxcover` — `maximum` | `max` | 98.7 % | TerraME takes the pixels whose centre falls in the cell (this rule reproduces it in 100 % of cells); resampling also counts pixels straddling the border (500 m / 30 m is not an integer) |
| `mincover` — `minimum` | `min` | 99.0 % | same |

### Amazônia — 2 229 cells, 50 km

Cells of the Brazilian Amazon (EPSG:29191) that touch its limit, filled from
the 5 km PRODES raster, roads, ports and indigenous lands.

| TerraME fill | DisSCube | Result |
|---|---|---|
| `prodes_10`, `prodes_208` — `coverage` | `percentage × coverage_purity` | **identical** in every cell with PRODES data; in the 55 cells without any, DisSCube reports NaN (purity 0) where TerraME reports 0 |
| `distroads` — `distance` (lines) | `min_distance` | mean error 17 km; the exact polygon distance matches TerraME in 73 % of cells (83 % within 100 m) |
| `distports` — `distance` (points) | `min_distance` | mean error 28 km; the exact polygon distance matches in 89 % of cells (91 % within 100 m) |
| `protected` — `area` (polygons) | — | no operator; intersection area / cell area reproduces TerraME in every cell (within 0.01) |

The exact polygon distance explains most but not all of TerraME's `distance`
values on Amazônia (the largest residuals reach 13–16 km), so TerraME's rule
needs to be pinned down before an exact operator is implemented.

## Known gaps relative to TerraME

- **Exact vector distance.** TerraME's `distance` is measured from the cell
  polygon to the nearest feature; `min_distance` is a raster approximation
  between cell centres (see the benchmarks). An exact vector operator is
  needed for parity; the Amazônia residuals show TerraME's rule is not only
  the plain geometric distance.
- **Cell assignment of pixels and lines.** `min`/`max` count pixels that
  straddle a cell border, and `presence` rasterizes lines through cell
  centres; TerraME assigns each pixel to the cell containing its centre and
  marks every cell a line touches (Emas).
- **Area-weighted vector aggregation.** TerraME's `sum` with `area = true` and
  `area` (fraction of the cell covered by polygons) have no DisSCube
  equivalent yet: vector sources are rasterized rather than area-weighted, and
  `sum` accepts raster sources only. The raster-fine path remains the
  recommended route for fractional drivers.
- **In-memory, single-tile by design.** The fine-alignment path materializes a
  fine array in memory; very large tiles at a high fine/target ratio are bounded
  by available memory. Distributed/lazy execution is a roadmap item, not a
  current capability.

## Positioning statement

> The filling of cellular spaces from heterogeneous geographic data — the core
> operation of TerraME's `fillCellularSpace` — is reformulated in DisSCube as a
> reproducible spatial-derivation layer: fill strategies become typed operators
> over a catalogued data cube, with aggregation on windows aligned to the target
> grid and explicit control of cell purity. On the three Fill examples shipped
> with TerraME (Itaituba, Emas, Amazônia), raster averages and class coverage
> reproduce TerraME's output cell by cell once cell purity is applied, and
> `presence`, `min` and `max` agree in 98–99.7 % of cells; exact vector
> distance and area-weighted polygon operations are the remaining gaps.
