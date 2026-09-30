# DisSCube — Examples

Self-contained, offline and lightweight examples illustrating the two ways of using DisSCube:
the **Python API** and declarative **TOML pipelines**.

All examples run in a few seconds without downloading external data:

```bash
pip install -e .
python examples/01_quickstart.py
```

Pass a directory as argument to keep the catalog and generated Zarr stores:

```bash
python examples/01_quickstart.py ./scratch
```

---

## Two Ways to Use DisSCube

DisSCube supports two equivalent, interoperable workflows for preparing spatial data cubes:

````carousel
```python
# (a) Python API (CubeClient & Derivation)
# examples/01_quickstart.py

from disscube import CubeClient, Derivation, GridSpec, SpatialSource

# 1. Initialize client
cube = CubeClient("catalog.db", "store")

# 2. Register grid (300 m cells, SIRGAS 2000 / UTM 23S)
cube.register_grid(GridSpec(
    id="demo/300m", type="local", crs="EPSG:31983", resolution=300.0,
    bbox=[570000.0, 9708000.0, 582000.0, 9720000.0],
))

# 3. Register spatial sources
cube.register_spatial_source(SpatialSource(
    id="landuse", format="raster", asset_url="landuse.tif", crs="EPSG:31983",
))
cube.register_spatial_source(SpatialSource(
    id="elevation", format="raster", asset_url="elevation.tif", crs="EPSG:31983",
))

# 4. Declarative derivations
cube.derive_declarative(
    Derivation(target="forest_pct", source_id="landuse", operator="percentage", class_code=3),
    grid_id="demo/300m",
)
cube.derive_declarative(
    Derivation(target="landuse_major", source_id="landuse", operator="majority"),
    grid_id="demo/300m",
)
cube.derive_declarative(
    Derivation(target="elev_mean", source_id="elevation", operator="mean"),
    grid_id="demo/300m",
)

# 5. Load model-ready DataArrays
forest = cube.load("forest_pct", grid_id="demo/300m")
```
<!-- slide -->
```toml
# (b) Declarative Pipeline (TOML)
# examples/pipelines/quickstart.toml

schema = 1
name = "Quickstart — Synthetic Land Use and Elevation"

[grid]
name = "demo/300m"
crs = "EPSG:31983"
bbox = [570000.0, 9708000.0, 582000.0, 9720000.0]
resolution = 300

[[source]]
id = "landuse"
type = "file"
path = "../data/quickstart/landuse.tif"
crs = "EPSG:31983"

[[source]]
id = "elevation"
type = "file"
path = "../data/quickstart/elevation.tif"
crs = "EPSG:31983"

[[derive]]
target = "forest_pct"
source = "landuse"
operator = "percentage"
class_code = 3

[[derive]]
target = "landuse_major"
source = "landuse"
operator = "majority"

[[derive]]
target = "elev_mean"
source = "elevation"
operator = "mean"
```
````

### Running the TOML Pipeline

Validate without I/O or downloads:
```bash
disscube validate examples/pipelines/quickstart.toml
```

Execute and record provenance:
```bash
disscube run examples/pipelines/quickstart.toml --workspace outputs/quickstart
```

Export directly to multi-band GeoTIFF:
```bash
disscube export examples/pipelines/quickstart.toml --output outputs/cellspace.tif
```

---

## Python API Examples

| Example | What it shows |
|---|---|
| [`01_quickstart.py`](01_quickstart.py) | Grid, raster sources and declarative derivations: `percentage`, `majority`, `mean`; loading results; cache hits via `spec_hash` |
| [`02_vector_drivers.py`](02_vector_drivers.py) | Drivers from vector layers: `min_distance`, `count`, `presence`, `attribute`; multiple variables per derivation |
| [`03_time_series.py`](03_time_series.py) | Time-stamped sources, `(time, y, x)` loading, and the hand-off to DisSModel with `to_dataset()` and `to_raster_backend()` |

All examples are tested automatically on CI (`tests/test_examples.py`).

---

## Real-World Cases and Recipes

Real data workflows and parity benchmarks are maintained in the dedicated repository
[**LambdaGeo/disscube-recipes**](https://github.com/LambdaGeo/disscube-recipes):

- `cases/terrame_fill`: Cell-by-cell numerical parity against TerraME's C++ `fillCellularSpace` (Itaituba, Emas, Amazônia).
- `cases/ilha_maranhao`: BDC Landsat-16D (STAC) and MapBiomas land-cover time series on a 300 m BDC Albers grid.
- `cases/prodes_br163`: Multi-year PRODES deforestation along the BR-163 corridor (Pará).
- `cases/luccme_br`: National LUCC reconstruction for Brazil (BigEarth).
