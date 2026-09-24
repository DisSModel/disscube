# Pipeline files (TOML)

A pipeline file declares a whole data preparation — the grid, the sources and
the derived variables — in one TOML file. It is the DisSCube counterpart of a
TerraME script that builds a cellular space and fills it with
`cells:fill{...}`: the same information, as data instead of code, validated
before anything is downloaded, and recorded in the provenance of every
product.

```bash
disscube validate examples/pipelines/itaituba_fill.toml    # check the file, no downloads
disscube run      examples/pipelines/itaituba_fill.toml --workspace outputs/itaituba
```

## From `fill` to `[[derive]]`

TerraME's Fill tutorial (`examples/data/terrame/itaituba/itaituba.lua`) and
its pipeline file (`examples/pipelines/itaituba_fill.toml`), side by side:

```lua
-- TerraME
itaitubaCells:fill{operation = "average",  layer = "elevation",     attribute = "elevation"}
itaitubaCells:fill{operation = "coverage", layer = "deforestation", attribute = "defor"}
itaitubaCells:fill{operation = "distance", layer = "roads",         attribute = "distroad"}
```

```toml
# DisSCube
[[derive]]
target = "elevation"
source = "elevation"
operator = "mean"

[[derive]]
target = "defor_87"
source = "deforestation"
operator = "percentage"
class_code = 87

[[derive]]
target = "distroad"
source = "roads"
operator = "min_distance"
```

| TerraME `operation` | DisSCube `operator` |
|---|---|
| `average` | `mean` |
| `coverage` / `percentage` | `percentage` (one derivation per `class_code`) |
| `majority` / `minority` | `majority` / `minority` |
| `distance` | `min_distance` |
| `presence`, `count`, `sum`, `minimum`, `maximum`, `stdev` | `presence`, `count`, `sum`, `min`, `max`, `std` |

How faithful each operator is to TerraME — and what is not supported yet — is
measured in [TerraME Fill Cells Correspondence](../terrame_fill_correspondence.md).

## Format (schema 1)

```toml
schema = 1                    # required
name = "…"                    # optional, recorded in the provenance
workspace = "outputs/run"     # optional, relative to this file

[grid]
name = "ilha_do_maranhao"
bbox = [-44.35, -2.62, -44.20, -2.47]
resolution = 300
# crs = "EPSG:29191"          # with crs: bbox is in that CRS and the grid is used as given
# snap = true                 # without crs: BDC Albers grid snapped to the national mesh

[[source]]                    # one block per source; see the types below
id = "…"
type = "file" | "bdc" | "mapbiomas" | "prodes" | "classified"

[[derive]]                    # one block per derived variable
target = "urban_pct"
source = "lulc_{year}"
operator = "percentage"
class_code = 24
# role = "driver"
# years = [2020]              # restrict a {year} source to some years
```

Without `crs`, `bbox` is `[min_lon, min_lat, max_lon, max_lat]` in WGS84 and the
grid is a local BDC Albers grid (`register_local_grid`). Relative paths are
relative to the pipeline file, so a folder with its TOML and data is portable.

### Source types

| `type` | Fields | Adapter |
|---|---|---|
| `file` | `path` (raster, vector, or `zip://…` shapefile), `crs`, `format`, `time` | a local file, checksummed |
| `bdc` | `collection`, `asset` **or** `normalized_difference = [a, b]`, `period`, `reducer`, `scale`, `offset`, `url`, `time` | `disscube.sources.bdc` |
| `mapbiomas` | `year`, `collection` (11), `resolution` (30), `url` | `disscube.sources.mapbiomas` |
| `prodes` | `year`, `url`, `cache` | `disscube.sources.prodes` |
| `classified` | `path`, `legend` (table or `.qml`/`.json`/`.csv` file), `time`, `nodata`, `producer` | `disscube.sources.classified` |

Every source block also accepts `name` and `years`.

### Several years: `years` and `{year}`

A source block with `years = [2000, 2020]` is expanded once per year; every
`{year}` in its strings is replaced, `year` (MapBiomas, PRODES) and `time` are
set, so `id = "lulc_{year}"` yields `lulc_2000` and `lulc_2020`. A derivation
whose `source` contains `{year}` is expanded over the same years, producing one
product per year that `load()` returns as a `(time, y, x)` series. There are no
other loops or conditions: what does not fit belongs in Python.

## What a run leaves behind

```
<workspace>/
├── catalog.db          the catalog
├── store/              derived variables (Zarr)
├── raw/                every source: GeoTIFF + <id>.provenance.json
└── run.json            this run: pipeline file and checksum, sources, products
```

Each provenance file gains a `pipeline` entry with the pipeline file's path,
name and SHA-256, so every derived variable can be traced back to the exact
file that declared it.

## Example files

| File | What it does | Needs |
|---|---|---|
| `examples/pipelines/itaituba_fill.toml` | TerraME's Fill tutorial | nothing (bundled data) |
| `examples/pipelines/ilha_bdc.toml` | example 07: BDC NDVI and MNDWI | network, `.[bdc]` |
| `examples/pipelines/ilha_mapbiomas.toml` | example 08: MapBiomas 2000 and 2020 | network |
| `examples/pipelines/lab15_prodes.toml` | example 09: PRODES 2008, 2016, 2024 | network |

All of them are validated by the test suite; the Itaituba one is also run and
compared with the same derivations made through the Python API.
