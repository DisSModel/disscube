# Architecture Overview

DisSCube provides a high-level abstraction for building spatial data cubes derived from multiple geospatial sources. Unlike a collection of GIS scripts, the system treats **derivations as declarative objects** with guaranteed identity, traceability and reproducibility.

## Conceptual model

```
SpatialSource ──► SpatialDerivation ──► Variable ──► DerivedVariable
     │                    │
  asset_url            spec_hash (SHA-256)
  format               grid_id
  crs                  valid_from / valid_until
```

### Main entities

| Entity | Role |
|---|---|
| `GridSpec` | Mathematical definition of the space: CRS + resolution + bbox |
| `SpatialSource` | Pointer to raw data: raster (GeoTIFF) or vector (GPKG/SHP) |
| `SpatialDerivation` | Derivation intent: source + grid + operators + time window |
| `Derivation` | Declarative front end over `SpatialDerivation`, validated at construction |
| `Variable` | Name + operator + class_code for a derived variable |
| `DerivedVariable` | Materialized product: Zarr path + `spec_hash` + `content_hash` |
| `SpatialRelation` | Parent–child relation between grids (reserved for future use in the pipeline) |

### Two usage modes

**Declarative (recommended):**
```python
from disscube.derivation import Derivation

d = Derivation(
    target="forest_pct",
    source_id="mapbiomas_2020",
    operator="percentage",
    class_code=3,
    valid_from="2020", valid_until="2020",
)
cube.derive_declarative(d, grid_id="AC/5km")
```
Validates the operator and `class_code` at construction (fail-fast), before any I/O.

**Direct:**
```python
from disscube.models import SpatialDerivation, Variable

cube.derive(SpatialDerivation(
    source_id="mapbiomas_2020", grid_id="AC/5km", role="driver",
    variables=[Variable(name="forest_pct", operator="percentage", class_code=3)],
    valid_from="2020", valid_until="2020",
))
```
Both modes reach the same pipeline — `Derivation` is a front end, not an alternative path.

## Execution pipeline

```
SpatialSource
    │
    ▼ Normalizer
    │  • raster: checks that the file can be opened
    │  • vector: loads the GeoDataFrame, fixes the CRS if needed
    │
    ▼ GridAligner
    │  • raster: per-variable reprojection using the operator's Resampling
    │           → returns xr.Dataset {var_name: DataArray}
    │  • vector: reprojects the GDF + clips to the grid bbox
    │  • invariant: checks shape == (grid.rows, grid.cols)
    │
    ▼ Aggregator
    │  • delegates to operator.compute(data, var, grid) → DataArray
    │  • builds the final xr.Dataset with CRS and transform
    │
    ▼ VariableWriter
       • writes each variable as Zarr
       • computes content_hash (SHA-256 of the bytes)
       • registers the DerivedVariable in the SQLite catalog
```

## Reproducibility: `spec_hash`

Every `SpatialDerivation` has a `spec_hash` — a deterministic SHA-256 of:

- `source_id`
- `grid_id`
- `role`
- variables (name + operator + class_code, sorted by name)
- `valid_from` / `valid_until`

`SpatialRelation` is excluded from the hash: no pipeline stage uses it during computation, so including it would make the cache sensitive to metadata that has no effect on the result.

If any parameter changes, the hash changes. The pipeline checks the cache before processing: if every `DerivedVariable` with the same `spec_hash` already exists on disk, the derivation is skipped.

`Derivation.spec_hash()` delegates to `SpatialDerivation.spec_hash()` and adds `purity_threshold` when set. `bbox` is excluded — it is descriptive metadata, not a derivation parameter.

## Operator system (plugins)

Each operator is a subclass of `Operator` that registers itself in `OPERATOR_REGISTRY`:

```python
class MajorityOperator(Operator):
    name = "majority"
    _resampling = Resampling.nearest

    def compute(self, data, var, grid) -> xr.DataArray:
        ...
```

`OPERATOR_REGISTRY["majority"]` → `MajorityOperator`. `GridAligner` uses `op_cls.resampling()` to choose the resampling method per variable. `Aggregator` uses `op_cls().compute()` to compute the result. Adding an operator = creating one file; zero changes to the pipeline.

See [Operators](operators.md) for the full list and the extension guide.

## Storage

```
data/derived/{grid_id}/{partition}/{spec_hash}/{variable_name}.zarr
```

- `partition` = `tile_id` or `global`.
- Each variable is an independent Zarr dataset with `spatial_ref` and CRS metadata.
- `content_hash` (SHA-256 of the Zarr bytes) guarantees the integrity of the materialized data.
