# Examples

The [`examples/`](https://github.com/DisSModel/disscube/tree/main/examples)
folder has runnable scripts that need nothing beyond `pip install -e .`: each
one works in a temporary directory and finishes in a few seconds. Pass a
directory to keep the catalog, the inputs and the derived Zarr stores for
inspection (e.g. in QGIS):

```bash
python examples/01_quickstart.py            # temporary workspace
python examples/01_quickstart.py ./scratch  # keep the outputs
```

All examples are executed by the test suite (`tests/test_examples.py`), so
they stay in sync with the API.

## Learning the API — synthetic data

Examples 01–03 generate their own inputs, so every number they print can be
checked by hand.

| Example | What it shows |
|---|---|
| [`01_quickstart.py`](https://github.com/DisSModel/disscube/blob/main/examples/01_quickstart.py) | Grid, raster sources and declarative derivations: `percentage`, `majority`, `mean`; loading results; cache hits via `spec_hash` |
| [`02_vector_drivers.py`](https://github.com/DisSModel/disscube/blob/main/examples/02_vector_drivers.py) | Drivers from points, lines and polygons: `min_distance`, `count`, `presence`, `attribute`; several variables per derivation |
| [`03_time_series.py`](https://github.com/DisSModel/disscube/blob/main/examples/03_time_series.py) | Time-stamped sources, `(time, y, x)` loading, and the hand-off to DisSModel with `to_lucc_data()` (including `period`) |

## Real data — TerraME's *Fill* examples

Examples 04–06 use the three *Fill* examples shipped with TerraME's `gis`
package, bundled in
[`examples/data/terrame/`](https://github.com/DisSModel/disscube/tree/main/examples/data/terrame)
together with the cellular spaces TerraME produced. Each derives the same
attributes with DisSCube and prints a cell-by-cell comparison with TerraME's
own output.

| Example | Cells | What it shows | Result |
|---|---|---|---|
| [`04_terrame_fill_itaituba.py`](https://github.com/DisSModel/disscube/blob/main/examples/04_terrame_fill_itaituba.py) | 620 × 5 km | TerraME's [Fill tutorial](https://github.com/TerraME/terrame/wiki/Fill): `mean`, `percentage` × `coverage_purity`, `min_distance` | averages and class coverage reproduce TerraME |
| [`05_terrame_fill_emas.py`](https://github.com/DisSModel/disscube/blob/main/examples/05_terrame_fill_emas.py) | 5 514 × 500 m | `presence` of lines, `max` / `min` of a raster; a study area defined by a limit polygon | 98.4–99.7 % of cells identical |
| [`06_terrame_fill_amazonia.py`](https://github.com/DisSModel/disscube/blob/main/examples/06_terrame_fill_amazonia.py) | 2 229 × 50 km | PRODES coverage with a declared nodata, distances to roads and ports | coverage identical wherever PRODES has data |

What the differences mean — and which TerraME operations DisSCube does not
support yet — is discussed in
[TerraME Fill Cells Correspondence](terrame_fill_correspondence.md).

## Real data — Brazil Data Cube

Example 07 reads a satellite data cube straight from the Brazil Data Cube
STAC catalog; see [BDC Integration](guides/bdc.md#reading-data-cubes-via-stac).

| Example | Cells | What it shows |
|---|---|---|
| [`07_bdc_cube.py`](https://github.com/DisSModel/disscube/blob/main/examples/07_bdc_cube.py) | 58 × 59 × 300 m | `LANDSAT-16D-1` over Ilha do Maranhão: windowed reads, dry-season median, `mean` of NDVI and MNDWI, `percentage` of open water on a grid snapped to the BDC Albers mesh |

It needs network access and `pip install -e ".[bdc]"`. Without network, or
with `--offline`, it runs on a synthetic scene with the same grid and value
ranges and prints a warning that the numbers are not BDC data.

## Real data — MapBiomas

Example 08 reads the MapBiomas annual land-cover maps straight from the
national files; see [MapBiomas](guides/mapbiomas.md).

| Example | Cells | What it shows |
|---|---|---|
| [`08_mapbiomas_land_use.py`](https://github.com/DisSModel/disscube/blob/main/examples/08_mapbiomas_land_use.py) | 58 × 59 × 300 m, 2 years | Collection 11 (30 m) over Ilha do Maranhão in 2000 and 2020: `majority` and `percentage` (urban, forest, mangrove) on the grid of example 07, `(time, y, x)` series handed to DisSModel with `to_lucc_data()` |

It needs network access. Without it, or with `--offline`, it runs on a
synthetic scene with the same grid and class codes and says so.

## Scope

DisSCube prepares data for models; it stops at `CubeClient.to_lucc_data()`.
Examples that run simulations with the prepared data (BR-MANGUE, LUCC) belong
to the model repositories, where those dependencies live.
