# DisSCube — Examples

Self-contained, runnable examples. Examples 01–03 generate their own
synthetic input data; examples 04–06 use real data bundled in
[`data/terrame/`](data/terrame/). All of them work in a temporary directory
and finish in a few seconds — no downloads, no local data folders:

```bash
pip install -e .
python examples/01_quickstart.py
```

Pass a directory to keep the catalog, the raw inputs and the derived Zarr
stores for inspection (e.g. in QGIS):

```bash
python examples/01_quickstart.py ./scratch
```

| Example | What it shows |
|---|---|
| [`01_quickstart.py`](01_quickstart.py) | Grid, raster sources and declarative derivations: `percentage`, `majority`, `mean`; loading results; cache hits via `spec_hash` |
| [`02_vector_drivers.py`](02_vector_drivers.py) | Drivers from vector layers: `min_distance`, `count`, `presence`, `attribute`; several variables per derivation |
| [`03_time_series.py`](03_time_series.py) | Time-stamped sources, `(time, y, x)` loading, and the hand-off to DisSModel with `to_lucc_data()` (including `period`) |
| [`04_terrame_fill_itaituba.py`](04_terrame_fill_itaituba.py) | TerraME's *Fill* tutorial on real data (Itaituba, Pará, 5 km): `mean`, `percentage` × `coverage_purity`, `min_distance`, compared cell by cell with TerraME's own output |
| [`05_terrame_fill_emas.py`](05_terrame_fill_emas.py) | TerraME's Emas National Park example (500 m): `presence` of lines, `max` / `min` of a raster; a study area defined by a limit polygon |
| [`06_terrame_fill_amazonia.py`](06_terrame_fill_amazonia.py) | TerraME's Brazilian Amazon example (50 km): PRODES coverage with a declared nodata, distances to roads and ports |

All examples are executed by the test suite (`tests/test_examples.py`), so
they are kept in sync with the API.

## Scope

DisSCube prepares data for models; it stops at `CubeClient.to_lucc_data()`.
Examples that run simulations with the prepared data (BR-MANGUE, LUCC) belong
to the model repositories, where those dependencies live.

## Utilities (`tools/`)

| Script | Purpose |
|---|---|
| `tools/zarr_to_tif.py` | Converts a derived Zarr to GeoTIFF |
| `tools/import_bdc_tiles.py` | Registers the BDC tile grids in a catalog |
