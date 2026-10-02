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

A TerraME fill script can also be written as a DisSCube pipeline file —
see [Pipeline files (TOML)](guides/pipeline_files.md),
the [quickstart pipeline](https://github.com/DisSModel/disscube/blob/main/examples/pipelines/quickstart.toml),
and the parity cases in the recipes repository (`cases/terrame_fill`).

## Strategy → operator correspondence

| TerraME fill strategy | DisSCube operator (`name`) | Status | Notes |
|---|---|---|---|
| `presence` | `presence` | implemented; parity measured (Emas) | Binary mask: 1 where any feature is present. Matches TerraME in 98.4–99.7 % of cells: lines are rasterized through cell centres, while TerraME marks every cell a line touches. |
| `coverage` / `percentage` (raster) | `percentage` | implemented (window-based); **parity verified** (Itaituba, Amazônia) | Fraction (0..1) of the target class per cell, **over valid pixels** — the denominator TerraME 2.0.1 uses too, so the value reproduces its goldens with no correction (Itaituba: max 0.0064; Amazônia: identical). `coverage_purity` stays available as metadata. Requires `class_code`. |
| `area` (polygons) | `area` | implemented (exact); **parity verified** (Amazônia) | Fraction (0..1) of each cell covered by polygons (e.g. protected areas): intersection area / cell area, overlapping polygons counted once. Reproduces TerraME's `protected` within 0.01 in at least 99 % of the cells. |
| `majority` / `mode` | `majority` | implemented (window-based) | Dominant class per cell; ties resolve to the smallest class value. |
| `minority` | `minority` | implemented (window-based) | Least-frequent class per cell. |
| `count` | `count` | implemented | Count of features per cell (proximity operator). |
| `distance` | `distance` | implemented (exact, from the cell centre); **parity verified** for points, lines differ by the vertex rule | Euclidean distance from each cell centre to the nearest feature, in CRS units (or in the CRS given as `params = {crs = …}`, e.g. metres on a geographic grid), without clipping the source to the grid (features outside it count). TerraME 2.0.1 measures from the cell centre to the nearest *vertex* of the feature, so for points the two are identical (error < 1 mm on Itaituba and Amazônia) and for lines `distance`, which measures to the segment, is smaller where a line passes between vertices (Itaituba: mean 24 m, 73.7 % of cells within 1 m; Amazônia: mean 300 m, 63.8 %). The LuccME Lab15 cellular space was built with centre distances, and `distance` reproduces its fields. |
| `distance` | `min_distance` | **approximation — semantics differ** | Rasterizes the features on the target grid and takes the Euclidean distance transform between cell centres (EDT × resolution). TerraME measures from the cell centre to the nearest vertex, so `min_distance` differs from it by up to about one cell: against the 2.0.1 goldens the mean error is 1.2 km on Itaituba (5 km cells) and 11–12 km on Amazônia (50 km cells), biased low because `all_touched` marks the cells a line only touches. Prefer `distance`. |
| `average` / `mean` | `mean` | implemented; **parity verified** | Mean value per cell (continuous, area-weighted resampling). |
| `sum` (raster) | `sum` | implemented | Sum per cell (continuous). |
| `sum` (vector, `area = false`) | `sum` | implemented | Adds the numeric column named like the target (or `params = {column = …}`) over the features that reach each cell: a point to the cell containing it, any other geometry to every cell it touches. |
| `sum` with `area = true` (polygons) | `sum`, `params = {area = true}` | implemented; **parity verified** (Itaituba) | Distributes a polygon attribute (e.g. census population) over cells in proportion to the intersected area, so the total is conserved. Reproduces TerraME's `population` within 4×10⁻⁷ in all 620 cells. Polygons are not clipped to the grid: one that crosses the border distributes only its inside share. |
| `minimum` | `min` | implemented; parity measured (Emas) | Minimum per cell. Matches TerraME in 99.0 % of cells: pixels that straddle a cell border count for both cells, while TerraME assigns each pixel to the cell containing its centre. |
| `maximum` | `max` | implemented; parity measured (Emas) | Maximum per cell. Matches TerraME in 98.7 % of cells, for the same reason as `min`. |
| `stdev` / `standardDeviation` | `std` | implemented (window-based) | True per-cell standard deviation over valid pixels. Population (`ddof = 0`) by default; `params = {ddof = 1}` gives the sample estimate (NaN for a cell with a single valid pixel). |
| `median` | `median` | implemented (window-based) | Median of the valid pixels per cell; not a TerraME fill strategy, offered for outlier-robust aggregation. |
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
Amazônia — each with its input layers and the Lua script. The reference is the
cellular space that TerraME 2.0.1 produces from them, archived as the goldens of
[`LambdaGeo/luccme-goldens`](https://github.com/LambdaGeo/luccme-goldens)
(v1.0.0, DOI [10.5281/zenodo.23107748](https://doi.org/10.5281/zenodo.23107748)):
the same inputs are derived with DisSCube on the same grid and compared cell by
cell.

Earlier versions of this page compared against a different reference file, whose
provenance was not recorded and which TerraME 2.0.1 does not reproduce (coverage as
a percentage divided by the whole cell, distance 0 in every cell that contains a
feature). The explanations drawn from it — that TerraME measures distance from the
cell polygon and divides coverage by the whole cell — do not hold for the goldens
and were removed. `elevation`, `population` and all of Emas are identical in both
files.

The full parity benchmark suite is maintained and executed on CI in the
[DisSCube Case Studies and Recipes](https://github.com/LambdaGeo/disscube-recipes)
repository (`cases/terrame_fill`), where input datasets are fetched from TerraME
with cryptographic checksum verification (`./run.sh all <dataset>`).

Below is the summary of the correspondences and cell-by-cell numerical parity
verified across the datasets.

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
| `defor_7` — `coverage` (60 m raster) | `percentage` | 0.054 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_87` | `percentage` | 0.054 pp | 0.64 pp | **100 %** within 1 pp |
| `defor_167` | `percentage` | 0.006 pp | 0.39 pp | **100 %** within 1 pp |
| `defor_255` | `percentage` | 0.000 pp | 0.002 pp | **100 %** within 1 pp |
| `distlocal` — `distance` (points) | `distance` | 1×10⁻⁶ m | 5×10⁻⁶ m | **100 %** within 1 mm |
| `distroad` — `distance` (lines) | `distance` | 24 m | 1 885 m | 73.7 % within 1 m; TerraME measures to the nearest vertex |
| `distroad`, `distlocal` | `min_distance` | 1 180 m, 1 244 m | 3 449 m, 3 396 m | biased −552 m, −273 m (not recommended) |
| `population` — `sum`, `area = true` | `sum`, `area = true` | 0.000 | 0.000 (4×10⁻⁷) | **100 %** within 0.01; total 60 693 conserved |

**Reading the results.**

- **Continuous averages agree.** The residual in `elevation` comes from the
  averaging rule (area-weighted resampling vs. TerraME's per-pixel average) on
  a coarse 923 m source.
- **Coverage agrees with no correction.** TerraME 2.0.1 divides the class area
  by the *valid pixels* of the cell, as DisSCube does; the residual is at most
  0.64 pp. Multiplying by
  `coverage_purity` — right for the earlier reference — would now be wrong.
- **TerraME measures distance from the cell centre to the nearest vertex of the
  feature.** For the localities (points) that is the feature itself, so
  `distance` is identical. For roads TerraME ignores the middle of the
  segments, so `distance` (to the segment) is smaller wherever a road passes
  between vertices; recomputing the distance to the nearest vertex reproduces
  `distroad` in 100 % of the cells, to the millimetre. The explanation is
  inferred from the output; TerraME's source was not read.
- **`min_distance` is an approximation.** It rasterizes the features with
  `all_touched`, takes the Euclidean distance transform between cell centres
  and multiplies by the resolution, so its error reaches about one cell.
- **Rasters without a declared nodata.** The deforestation raster declares
  none, and its classes include 255. Earlier versions reprojected it with the
  `uint8` default fill value (255) and then treated that value as nodata,
  dropping the legitimate class 255. Since the fix, the fill value can no longer
  collide with the data, and `defor_255` agrees with TerraME in every cell.

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
| `prodes_10`, `prodes_208` — `coverage` | `percentage` | **identical** (max 5×10⁻¹¹) in every cell with PRODES data; in the 55 cells without any, DisSCube reports NaN where TerraME reports 0 |
| `distports` — `distance` (points) | `distance` | **identical** (max 5×10⁻⁴ m) |
| `distroads` — `distance` (lines) | `distance` | mean 300 m, max 18 km; 63.8 % of cells within 1 m. TerraME measures to the nearest vertex (reproduced in 100 % of cells) |
| `distroads`, `distports` | `min_distance` | mean 12.0 km, 11.2 km; biased low (−6.8 km, −2.7 km); not recommended |
| `protected` — `area` (polygons) | `area` | intersection area / cell area reproduces TerraME within 0.01 in ≥ 99 % of cells (`tests/test_terrame_parity.py`) |

## Known gaps relative to TerraME

- **Distance to lines.** TerraME 2.0.1 measures from the cell centre to the
  nearest *vertex* of a line; `distance` measures to the segment, which is the
  geometrically correct value and is smaller where a line passes between
  vertices. There is no vertex mode yet; for points the two coincide.
- **Cell assignment of pixels and lines.** `min`/`max` count pixels that
  straddle a cell border, and `presence` rasterizes lines through cell
  centres; TerraME assigns each pixel to the cell containing its centre and
  marks every cell a line touches (Emas).
- **In-memory, single-tile by design.** The fine-alignment path materializes a
  fine array in memory; very large tiles at a high fine/target ratio are bounded
  by available memory — `params = {subcells = n}` caps the ratio.
  Distributed/lazy execution is a roadmap item, not a current capability.

## Positioning statement

> The filling of cellular spaces from heterogeneous geographic data — the core
> operation of TerraME's `fillCellularSpace` — is reformulated in DisSCube as a
> reproducible spatial-derivation layer: fill strategies become typed operators
> over a catalogued data cube, with aggregation on windows aligned to the target
> grid and explicit control of cell purity. On the three Fill examples shipped
> with TerraME (Itaituba, Emas, Amazônia), raster averages and class coverage
> reproduce TerraME's output cell by cell, distance to points is identical and
> the areal-weighted `sum` conserves TerraME's totals, while `presence`, `min`
> and `max` agree in 98–99.7 % of cells and polygon `area` in ≥ 99 %; distance
> to lines follows TerraME's vertex rule only approximately.
