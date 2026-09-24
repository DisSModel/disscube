# DisSCube

[![CI](https://github.com/DisSModel/disscube/actions/workflows/ci.yml/badge.svg)](https://github.com/DisSModel/disscube/actions/workflows/ci.yml)
[![Docs](https://github.com/DisSModel/disscube/actions/workflows/docs.yml/badge.svg)](https://dissmodel.github.io/disscube/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> **Status: Alpha — stable APIs for the core pipeline; declarative models still evolving.**

DisSCube is the spatial data cube engine of the **DisSModel** ecosystem. It converts raw geospatial sources (rasters, vectors) into derived variables aligned to LUCC (Land Use and Cover Change) modeling grids, ready for Cellular Automata models and spatio-temporal analysis.

📖 **Documentation:** <https://dissmodel.github.io/disscube/>

## Core concept

```
SpatialSource  →  Derivation  →  Variable  →  DerivedVariable (Zarr)
```

A **source** (`SpatialSource`) goes through a **derivation** (`SpatialDerivation` or `Derivation`) that applies an **operator** on a **grid** (`GridSpec`), producing a **derived variable** registered in the SQLite catalog and stored in Zarr.

## Installation

```bash
git clone https://github.com/DisSModel/disscube.git
cd disscube
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Basic usage

### 1. Initialize the catalog and register a grid

```python
from disscube.client import CubeClient
from disscube.utils.grids import register_local_grid

cube = CubeClient(catalog="catalog.db", store="./data/")

grid = register_local_grid(
    cube,
    name="AC",
    bbox_geo=(-73.99, -11.15, -66.62, -7.11),
    resolution=5_000.0,
)
```

### 2. Register a source

```python
from disscube.models import SpatialSource

cube.register_spatial_source(SpatialSource(
    id="mapbiomas_2020",
    name="MapBiomas Acre 2020",
    format="raster",
    asset_url="data/raw/mapbiomas_2020.tif",
    crs="EPSG:4326",
    time=2020,
))
```

### 3. Derive — declarative mode (recommended)

```python
from disscube.derivation import Derivation

d = Derivation(
    target="forest_pct",
    source_id="mapbiomas_2020",
    operator="percentage",
    class_code=3,
    role="driver",
    valid_from="2020",
    valid_until="2020",
)

cube.derive_declarative(d, grid_id="AC/5km")
```

### 4. Derive — direct mode

```python
from disscube.models import SpatialDerivation, Variable

cube.derive(SpatialDerivation(
    source_id="mapbiomas_2020",
    grid_id="AC/5km",
    role="driver",
    variables=[Variable(name="forest_pct", operator="percentage", class_code=3)],
    valid_from="2020",
    valid_until="2020",
))
```

### 5. Load the result

```python
da = cube.load("forest_pct", grid_id="AC/5km")
print(da.shape)   # (rows, cols)
```

### 6. Hand off to DisSModel

```python
backend = cube.to_lucc_data(
    ["forest_pct", "dist_roads"],
    grid_id="AC/5km",
    period=("2015", "2020"),
)
```

## Examples

[`examples/`](examples/) has runnable scripts; 01–06 need no downloads and run
in seconds:

- **01–03, synthetic data** — a quickstart with raster operators, vector
  drivers, and time series handed off to DisSModel.
- **04–06, real data** — the three *Fill* examples shipped with TerraME
  (Itaituba, Emas National Park, Brazilian Amazon), derived with DisSCube and
  compared cell by cell with TerraME's own output. Data are bundled in
  [`examples/data/terrame/`](examples/data/terrame/).
- **07, Brazil Data Cube** — reads the Landsat 16-day data cube over Ilha do
  Maranhão through the BDC STAC catalog (only the pixels of the area are
  fetched) and derives NDVI, MNDWI and open-water drivers on a 300 m grid
  snapped to the BDC Albers mesh. Needs network and `pip install -e ".[bdc]"`;
  with `--offline` it runs on a synthetic stand-in.

```bash
python examples/01_quickstart.py
python examples/04_terrame_fill_itaituba.py
```

See [`examples/README.md`](examples/README.md) for the full list, and
[`docs/terrame_fill_correspondence.md`](docs/terrame_fill_correspondence.md)
for how DisSCube's operators relate to TerraME's *Fill*.

## Available operators

| Operator | Type | Resampling | Requires `class_code` |
|---|---|---|---|
| `mean` | zonal | average | no |
| `sum` | zonal | sum | no |
| `std` | zonal | nearest | no |
| `min` | zonal | min | no |
| `max` | zonal | max | no |
| `majority` | zonal | nearest¹ | no |
| `minority` | zonal | nearest¹ | no |
| `percentage` | zonal | nearest¹ | **yes** |
| `attribute` | zonal | nearest | no |
| `presence` | zonal | nearest | no |
| `min_distance` | proximity | nearest | no |
| `count` | proximity | nearest | no |

> ¹ These use `needs_fine_alignment=True`: GridAligner resamples with `nearest` at high resolution; the actual reduction (per-window counting) is done by the operator.

## Pipeline

```
SpatialSource
    │
    ▼
Normalizer        — validates / loads a GeoDataFrame (vector) or opens the raster
    │
    ▼
GridAligner       — reprojects per variable with the operator's Resampling
    │
    ▼
Aggregator        — delegates to operator.compute() → one xr.DataArray per variable
    │
    ▼
VariableWriter    — writes Zarr + registers the DerivedVariable in the catalog
```

## Storage layout

```
data/derived/{grid_id}/{partition}/{spec_hash}/{variable_name}.zarr
```

- `partition` = `tile_id`, or `global` for untiled derivations.
- `spec_hash` = SHA-256 of the derivation (source + grid + variables + time window, plus the source's `checksum` when it has one — replacing a source file and registering its new checksum recomputes instead of returning a stale product).

## Project structure

```
disscube/
├── client/           CubeClient — public entry point
├── models/           GridSpec, SpatialSource, SpatialDerivation, Variable…
├── derivation.py     Declarative Derivation (front end over SpatialDerivation)
├── operators/        Operators as classes (self-registered via __init_subclass__)
│   ├── base.py       Operator ABC + OPERATOR_REGISTRY
│   ├── zonal.py      mean, sum, majority, percentage, attribute, presence…
│   └── proximity.py  min_distance, count
├── pipeline/         Stages: Normalizer → GridAligner → Aggregator → Writer
├── catalog/          CatalogStore (Protocol) + SQLite and JSON implementations
├── storage/          AssetStore (fsspec — local and S3)
├── api/              Experimental HTTP API (optional `api` extra)
└── utils/grids.py    register_local_grid, register_simulation_grids
```

## Adding a new operator

Create a subclass of `Operator` in any module imported at startup:

```python
from rasterio.warp import Resampling
from disscube.operators.base import Operator

class WeightedMeanOperator(Operator):
    name = "weighted_mean"
    _resampling = Resampling.average

    def compute(self, data, var, grid):
        # data is an xr.DataArray (raster) or a GeoDataFrame (vector)
        ...
```

The operator is registered automatically and accepted by `Derivation` / `SpatialDerivation` with no other change.

## Experimental: HTTP API

`disscube.api` exposes the catalog over HTTP for **remote orchestration**: registering grids and sources, triggering derivations and querying what has been derived. It is experimental and ships as an optional extra:

```bash
pip install -e ".[api]"
DISSCUBE_CATALOG=./catalog.db DISSCUBE_STORE=./data/ uvicorn disscube.api.app:app
```

| Endpoint | Purpose |
|---|---|
| `GET` / `POST /grids` | List / register `GridSpec`s |
| `GET` / `POST /sources` | List / register `SpatialSource`s |
| `POST /derive` | Run a `SpatialDerivation` (errors → HTTP 400) |
| `GET /catalog?grid=&role=` | List derived variables |
| `GET /variables/{id}` | Metadata of one derived variable, including its Zarr `asset_url` |

The API does **not** serve raster data. Models load derived variables in-process with `CubeClient.load()` / `CubeClient.to_lucc_data()`, reading the same Zarr store (local, or S3 via fsspec) that the API writes to. Interactive docs are available at `/docs` once the server is running.

## Known limitations

The limitations below are scope decisions for the current version, not bugs. They are documented so that users and reviewers understand what is implemented versus what is planned.

**In-memory, single-tile processing**
Each call to `derive()` loads a tile's full data into memory. There is no lazy (Dask) or distributed processing. For continental-scale grids (e.g. `BR/1km`), use the tile loop — each tile is processed and saved independently.

**Vector aggregation by rasterization (not area-weighted)**
Operators over vector sources (`majority`, `percentage`, `attribute`, `presence`, `minority`) convert geometries to raster before aggregating pixels. Each cell's coverage fraction is estimated by pixel counting, not by computing intersection areas. For more accurate proportional coverage, use a raster source at a resolution substantially finer than the target cell.

**Tile disambiguation in `load()`**
`CubeClient.load(name)` without `tile_id` raises `ValueError` when multiple tiles of the same variable exist on the same grid. Automatic mosaicking is not implemented. **Always pass `tile_id` in multi-tile workloads.**

**`SpatialRelation` does not act in the pipeline**
The `SpatialRelation` model is persisted in the catalog, but no pipeline stage uses relations during derivation — which is why they are **excluded from `spec_hash`**. Including them would make the cache key sensitive to metadata that does not affect the result, breaking the reproducibility guarantee. Integration with hierarchical grid strategies is reserved for a future version.

**`purity_threshold` reserved**
The `purity_threshold` field on `Derivation` is included in `spec_hash` but is not applied to the output — purity masking is not implemented. Setting `purity_threshold` changes the cache key without changing the result.

**STAC: reading only**
`disscube.utils.bdc_stac` reads Brazil Data Cube cubes through their STAC catalog (search, windowed reads, per-tile composites, mosaics) and writes local GeoTIFFs that are registered as ordinary sources. Derived variables are not published back as STAC, and the `valid_from`/`valid_until` and `bbox` fields on `Derivation` only follow STAC naming conventions.

## License

DisSCube is part of the DisSModel ecosystem and is released under the MIT License. See [LICENSE](LICENSE) for details.
