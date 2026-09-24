# DisSCube — Examples

Self-contained, runnable examples. Examples 01–03 generate their own
synthetic input data; examples 04–06 use real data bundled in
[`data/terrame/`](data/terrame/); example 07 reads the Brazil Data Cube over
the network, 08 reads MapBiomas and 09 downloads PRODES (≈130 MB, cached). All of them work in a temporary directory; 01–06 finish in a few
seconds with no downloads:

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
| [`07_bdc_cube.py`](07_bdc_cube.py) | Brazil Data Cube: Landsat 16-day cube over Ilha do Maranhão via STAC (windowed reads), dry-season median, NDVI / MNDWI / open-water drivers on a 300 m BDC Albers grid. Needs network and `.[bdc]`; `--offline` runs a synthetic stand-in |
| [`08_mapbiomas_land_use.py`](08_mapbiomas_land_use.py) | MapBiomas Collection 11 (30 m) over Ilha do Maranhão in 2000 and 2020: windowed reads of the national files, code 0 as nodata, `majority` and `percentage` (urban, forest, mangrove) on the grid of 07, `(time, y, x)` series and `to_lucc_data()`. Needs network; `--offline` runs a synthetic stand-in |
| [`09_prodes_deforestation.py`](09_prodes_deforestation.py) | PRODES in the LuccME Lab15 area (south of Santarém) at the end of 2008, 2016 and 2024: edition downloaded once and cached, legend read from its `.qml`, forest / deforested / other, `percentage` on a 500 m BDC grid. Needs network; `--offline` runs a synthetic stand-in |

All examples are executed by the test suite (`tests/test_examples.py`), so
they are kept in sync with the API. The suite sets `DISSCUBE_OFFLINE=1`, so
07, 08 and 09 run on their synthetic stand-ins there.

## Scope

DisSCube prepares data for models; it stops at `CubeClient.to_lucc_data()`.
Examples that run simulations with the prepared data (BR-MANGUE, LUCC) belong
to the model repositories, where those dependencies live.

## Utilities (`tools/`)

| Script | Purpose |
|---|---|
| `tools/zarr_to_tif.py` | Converts a derived Zarr to GeoTIFF |
| `tools/import_bdc_tiles.py` | Registers the BDC tile grids in a catalog |
