# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **`sum` over vector sources**, with `params = {area = true}` for areal weighting: each polygon's
  attribute (e.g. census population) is shared among the cells in proportion to the intersected
  area, conserving the total. Reproduces TerraME's `sum` with `area = true` on Itaituba in all 620
  cells (max error 4×10⁻⁷). Without `area`, every feature adds its whole value to each cell it
  touches (a point to its own cell). `params = {column = …}` picks the column (default: the target
  name). Vector sources are no longer clipped to the grid for `sum`, so a polygon crossing the border
  keeps its full denominator.
- **`median` operator**: per-cell median of the valid pixels, from the fine-aligned source.
- **`std` `ddof` param** (`0` default, `1` for the sample estimate). The default is unchanged, so
  existing `spec_hash`es hold.
- **`type = "dem"` source**: elevation, or slope in degrees or percent, from SRTM, Copernicus GLO-30
  or TOPODATA (or your own `tiles`), read over the grid plus a `margin` and registered as a raster
  source with checksum and provenance. The slope is computed on a metric (UTM) grid by central
  differences; asking for a Copernicus slope logs a warning, since that model includes the forest
  canopy. TOPODATA sheets are downloaded once into a cache. Replaces the DEM script of the Lab15
  reconstruction.
- **`type = "osm"` source**: ways from OpenStreetMap through the Overpass API, clipped to the grid
  plus a `margin`, registered as a vector source with checksum and provenance (request, server,
  time retrieved, `timestamp_osm_base`, ODbL). The answer is cached by request, so a pipeline reads
  the same data (and gets the same `spec_hash`) on every run; `date` asks for an older snapshot
  (`date = "{year}-07-01"` with `years`); several public servers are tried with retries; HTTP 406
  points to `OSM_CONTACT`. Replaces the OpenStreetMap download script of the Lab15 reconstruction.

## [0.4.0] - 2026-09-30

### Changed
- **DisSModel is now optional.** DisSCube no longer depends on it: install
  `disscube[dissmodel]` only to hand a cube to a model. `CubeClient.to_lucc_data()`
  is replaced by `to_raster_backend()` (same result, needs the extra; raises an
  `ImportError` that says how to install it). Nothing in the pipeline or the CLI
  needs DisSModel any more, including exports. The package is described as
  "Declarative spatial data cubes".
- `to_raster_backend()` / `to_dataset()` raise `ValueError` when a `period`
  leaves no variable at all (the old backend came back empty).
- **Removed the experimental HTTP API** (`disscube.api` and the `api` extra). DisSCube is
  a library and a CLI; a server belongs in its own package that depends on it.
- **Slimmed `examples/` and migrated real-data cases**: Real-world datasets, bundled GIS assets (~12 MB), and TerraME parity benchmarks were moved to the dedicated [LambdaGeo/disscube-recipes](https://github.com/LambdaGeo/disscube-recipes) repository (`cases/terrame_fill`, `cases/ilha_maranhao`, `cases/prodes_br163`). The core `examples/` directory now contains strictly lightweight, offline, self-contained examples with tests for both the Python API (`01_quickstart.py`, `02_vector_drivers.py`, `03_time_series.py`) and declarative TOML pipelines (`examples/pipelines/quickstart.toml`).

### Added
- `CubeClient.to_dataset()`: the cube as an `xarray.Dataset` — `(y, x)` static and
  `(time, y, x)` temporal variables, CRS and transform via rioxarray — the
  primary, dependency-free output.
- `CubeClient.export_netcdf()` (extra `disscube[netcdf]`): CF-1.8 netCDF with a
  real `time` axis, per-variable attributes (`spec_hash`, `operator`, ...) and
  `mask` kept as a variable.
- **Provenance in the exports.** The writer now stores `source_checksum` (the input the
  slice came from) with each new variable, and `CubeClient.provenance()` lists, per time
  slice, `spec_hash`, `content_hash`, `source_id` and `source_checksum`. netCDF files carry
  it as the global JSON attribute `disscube_provenance` (and as plain attributes on
  single-slice variables), plus `history`; GeoTIFF bands carry `SPEC_HASH`, `CONTENT_HASH`,
  `SOURCE_CHECKSUM` and `SOURCE_ID` tags. Exports made by a pipeline also record
  `pipeline_file`, `pipeline_checksum` and `pipeline_name`. Variables derived by earlier
  versions lack `source_checksum` until they are derived again.
- `CubeClient.export_geotiff()`: one band per variable, and per year for temporal
  ones (`<variable>_<year>`), with `VARIABLE`/`YEAR`/`SPEC_HASH` band tags.
  Pipelines and `disscube run/export` write netCDF when the output ends in `.nc`
  or `[export] format = "netcdf"`.
- **Declarative derivations.** A `Derivation` names a source, a target grid and
  an operator; operators register themselves (`OPERATOR_REGISTRY`) and cover
  zonal statistics (`mean`, `std`, `min`, `max`, `sum`, `majority`,
  `minority`, `percentage`, `presence`, `count`, `area`, `attribute`) and
  proximity (`distance`, `min_distance`). `GridAligner`
  picks the resampling method from the operator, and categorical operators
  are aligned on a fine sub-grid so class composition is never averaged.
- **Catalog and store.** SQLite (and JSON) catalog of grids, sources, derived
  variables and spatial relations; Zarr store with tiling and temporal
  series; `spec_hash` and input checksums for reproducibility;
  `purge_stale()` for entries whose store files are gone.
- **Grids.** Universal grid snapping, the Brazil national simulation grids, and
  the Brazil Data Cube Grid V2 shapefiles bundled with the package.
- **Sources** (`disscube.sources`): Brazil Data Cube composites via STAC (with
  provenance and checksum-keyed caching), MapBiomas annual land cover, PRODES
  deforestation, classified maps (SITS) with legends, local raster/vector
  files (including NetCDF variables), and unions of sources.
- **Pipeline files.** A pipeline is one TOML file (`[grid]`, `[[source]]`,
  `[[derive]]`, optional `[export]`), validated with a schema and expanded
  over `years`; `sources_from_catalog` lets a file reuse sources registered
  by another one.
- **Command line** (`disscube`): `validate` (check a file without fetching
  anything), `fetch` (download and verify remote sources by SHA-256), `run`
  and `export` (write the derived variables to a
  multi-band GeoTIFF straight from the cube).
- `CubeClient.to_lucc_data()` hands a cube to DisSModel as a raster backend.
- Self-contained examples, the TerraME `Fill` correspondence with a
  cell-by-cell parity test suite, and the MkDocs documentation site.
- `CHANGELOG.md`, `CODE_OF_CONDUCT.md`, `CITATION.cff`, and a publish
  workflow, following the conventions of DisSModel.
- `mypy` in CI, configured per module in `pyproject.toml` (no blanket
  `ignore_missing_imports`), and the whole tree is now type-clean.

### Changed
- Remote `file` sources (those with a `url`) are downloaded and verified once
  into pooch's OS cache directory (`~/.cache/disslucc/raw` on Linux), shared by
  every pipeline and workspace on the machine. `path` is now just the logical
  filename inside that cache, so a pipeline file no longer depends on where it
  sits relative to a `data/raw/` folder. Sources without a `url` still resolve
  relative to the pipeline file.
- `Plan.grid` is typed `GridConfig | None`: a sources-only pipeline has no grid. Running a file with neither a `[grid]` nor an `extent` now raises a `PipelineError` instead of a `TypeError`.
- `GridAligner` raises a clear `TypeError` when a file opens as several subdatasets instead of one raster.
- The workspace is resolved by one helper (`resolve_workspace`) shared by
  `run()` and `export_cube()`.
- The test suite and tools now pass `ruff check .` and `bandit` with no findings.

### Fixed
- The exported GeoTIFF now declares NaN as its NoData value. Every derived
  variable is NaN outside its extent, but the file carried no NoData tag, so
  GIS tools drew those pixels as solid black instead of transparent.
- `GridAligner` no longer lets reprojection fill values collide with data.
- The aggregator keeps every row on geographic grids.
- `distance` is exact, and `min_distance` returns NaN when there are no
  features instead of a misleading number.
- The CRS is written after the variable loop, so `grid_mapping` is set on
  every data variable.
- Stale catalog entries are skipped on load instead of failing.
- `tests/test_pipeline_file.py` no longer requires `[[derive]]` blocks from
  sources-only pipelines.

### Security
- PRODES downloads check the URL scheme before `urlopen` (`http`/`https`; the
  offline stand-in used by the examples also uses `file://`).
