# Examples

The [`examples/`](https://github.com/DisSModel/disscube/tree/main/examples)
folder contains runnable, self-contained examples demonstrating the two primary modes of using DisSCube:
the **Python API** (`CubeClient`, `Derivation`) and the **declarative TOML pipeline** (`disscube validate` / `disscube run`).

All examples run offline, generate or consume tiny synthetic data (< 50 KB), and complete in seconds without downloading external assets:

```bash
python examples/01_quickstart.py            # temporary workspace
python examples/01_quickstart.py ./scratch  # keep the outputs for inspection
```

All examples are executed by the test suite (`tests/test_examples.py`), ensuring they always stay in sync with the codebase.

## Python API Examples (Synthetic Data)

Examples 01–03 generate their own inputs locally, allowing every calculated number to be verified directly:

| Example | What it shows |
|---|---|
| [`01_quickstart.py`](https://github.com/DisSModel/disscube/blob/main/examples/01_quickstart.py) | Grid, raster sources and declarative derivations: `percentage`, `majority`, `mean`; loading results; cache hits via `spec_hash` |
| [`02_vector_drivers.py`](https://github.com/DisSModel/disscube/blob/main/examples/02_vector_drivers.py) | Drivers from points, lines and polygons: `min_distance`, `count`, `presence`, `attribute`; several variables per derivation |
| [`03_time_series.py`](https://github.com/DisSModel/disscube/blob/main/examples/03_time_series.py) | Time-stamped sources, `(time, y, x)` loading, and the hand-off to DisSModel with `to_lucc_data()` (including `period`) |

## Declarative Pipeline Files (TOML)

Pipelines can be declared in clean, version-controlled TOML files in
[`examples/pipelines/`](https://github.com/DisSModel/disscube/tree/main/examples/pipelines):

| Pipeline | What it shows | Execution |
|---|---|---|
| [`quickstart.toml`](https://github.com/DisSModel/disscube/blob/main/examples/pipelines/quickstart.toml) | Declarative counterpart of `01_quickstart.py`: derives `percentage`, `majority`, and `mean` on a 300 m grid | `disscube run examples/pipelines/quickstart.toml` |

Validate and execute:
```bash
disscube validate examples/pipelines/quickstart.toml
disscube run examples/pipelines/quickstart.toml --workspace outputs/quickstart
```

See [Pipeline files (TOML)](guides/pipeline_files.md) for full syntax and options.

## Real Data & Case Studies (External Repository)

Real-world datasets, historical reproductions and large-scale case studies are maintained in the dedicated
[**DisSCube Recipes and Case Studies**](https://github.com/LambdaGeo/disscube-recipes) repository:

- **TerraME Fill Parity (`cases/terrame_fill`)**: Cell-by-cell numerical parity against TerraME's C++ `fillCellularSpace` across three reference areas:
  - `itaituba`: 620 cells (5 km) with elevation, multi-class deforestation, and proximity drivers.
  - `emas`: 5 514 cells (500 m) with firebreaks, rivers, and accumulation rasters.
  - `amazonia`: 2 229 cells (50 km) with PRODES deforestation and indigenous territory area fractions.
  - See [TerraME Fill Cells Correspondence](terrame_fill_correspondence.md).
- **Ilha do Maranhão (`cases/ilha_maranhao`)**: Brazil Data Cube (Landsat-16D STAC) and MapBiomas land-cover series (2000, 2020) aligned on a 300 m BDC Albers grid with Python hand-off to DisSModel.
- **PRODES BR-163 (`cases/prodes_br163`)**: Multi-year deforestation monitoring in the Mojuí dos Campos / BR-163 corridor (500 m grid).
- **LUCCME-BR (`cases/luccme_br`)**: National-scale land-use change reconstruction for Brazil (BigEarth).

## Scope

DisSCube prepares data for models; it stops at `CubeClient.to_lucc_data()`.
Examples that run simulations with the prepared data (BR-MANGUE, LUCC) belong
to the model repositories, where those dependencies live.
