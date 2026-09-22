# Pipeline Stages

The pipeline is a sequence of `PipelineStage`s, each receiving and returning a `PipelineContext`. The context carries the source, grid, derivation and the data being transformed.

```python
class PipelineStage:
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        raise NotImplementedError
```

```python
class PipelineContext(BaseModel):
    source: SpatialSource
    grid: GridSpec
    derivation: SpatialDerivation
    tile_id: str | None = None
    data: Any = None
```

The sequence executed by `CubeClient.derive()`:

```python
pipeline = [Normalizer(), GridAligner(), Aggregator(), VariableWriter(store, catalog)]
for stage in pipeline:
    ctx = stage.execute(ctx)
```

---

## 1. Normalizer

**File:** `disscube/pipeline/normalizer.py`

**Responsibility:** entry point for the data source.

### Raster
Opens the file with `rasterio` to check that it is readable. It does not load data — the actual read is lazy, in `GridAligner`.

### Vector
Loads the full `GeoDataFrame` with `geopandas.read_file()`. If the CRS declared in the source (`SpatialSource.crs`) differs from the CRS in the file, it applies `set_crs(..., allow_override=True)` with an explicit `log.warning`.

!!! warning "CRS override"
    `Normalizer` **does not reproject** — it only fixes metadata. This is intentional, for sources with a malformed or missing `.prj`. If the intent is to reproject, set `SpatialSource.crs` to the data's real CRS and let `GridAligner` do the reprojection.

---

## 2. GridAligner

**File:** `disscube/pipeline/aligner.py`

**Responsibility:** align the source to the target `GridSpec`.

### Raster — per variable

For each variable in the derivation:

1. **Band selection:** via `band_map` (1-based) or by positional index.
2. **Per-operator resampling:** queries `OPERATOR_REGISTRY[var.operator].resampling()`. Each variable uses the method appropriate to its semantics (`Resampling.mode` for `majority`, `Resampling.average` for `mean`).
3. **Reprojection:** `band.rio.reproject(grid.crs, shape=(rows, cols), transform=grid.transform, resampling=...)`.
4. **Alignment invariant:** checks `aligned.rio.shape == (grid.rows, grid.cols)`. A mismatch → explicit `ValueError`.

Returns an `xr.Dataset` with one `DataArray` per variable, already with `dims=("y", "x")`.

### Vector

1. Reprojects the GeoDataFrame to the grid CRS, using `pyproj.CRS.equals()` for robust comparison.
2. Clips to the grid bbox with `shapely.geometry.box`.

Returns the processed GeoDataFrame in `ctx.data`.

### Why per variable?

Multi-variable derivations from multi-band sources often use different operators:

```python
variables=[
    Variable(name="uso",  operator="majority"),  # Resampling.mode
    Variable(name="alt",  operator="mean"),       # Resampling.average
    Variable(name="solo", operator="majority"),   # Resampling.mode
]
```

With the per-variable approach, each band is reprojected with the semantically correct method. The previous version used only `variables[0].operator` for the whole raster.

---

## 3. Aggregator

**File:** `disscube/pipeline/aggregator.py`

**Responsibility:** derive each variable by calling its operator.

For each variable in the derivation:

```python
op_cls = OPERATOR_REGISTRY[var.operator]

# Raster: ctx.data is a Dataset → select the pre-aligned DataArray
# Vector: ctx.data is a GeoDataFrame → the operator does the rasterization
var_data = ctx.data[var.name] if isinstance(ctx.data, xr.Dataset) else ctx.data

result = op_cls().compute(var_data, var, grid)
final_ds[var.name] = result
```

It contains no operator logic — it is pure dispatch. The historical `if/elif` chain was removed.

It builds the final `xr.Dataset`. `write_crs()` and `write_transform()` are called **after** the variable loop — rioxarray propagates `grid_mapping="spatial_ref"` only to the data variables already present in the Dataset at the time of the call. Calling them before the loop would leave no variable with the attribute, preventing CF readers (QGIS, GDAL) from detecting the projection automatically. The CRS is named explicitly to avoid `PROJCS["unknown"]` for CRSs without a registered EPSG code (e.g. BDC Albers).

---

## 4. VariableWriter

**File:** `disscube/pipeline/writer.py`

**Responsibility:** persist and register.

For each variable in the Dataset:

1. Adds attributes: `grid_id`, `role`, `spec_hash`, `crs`, `tile_id`.
2. Writes it as Zarr to `data/derived/{grid_id}/{partition}/{spec_hash}/{var}.zarr`.
3. Computes `content_hash` (SHA-256 of all the Zarr bytes, in deterministic order).
4. Determines `times`:
   - If `source.time` is set → `[source.time]` (e.g. MapBiomas 2020)
   - Otherwise, if `valid_from` is a year → `[int(valid_from)]`
   - No temporal information → `[]` (static variable)
5. Registers the `DerivedVariable` in the SQLite catalog.

### Tile detection

If `tile_id` is not passed explicitly and `grid.id` starts with `BDC_`, it tries to extract the tile from `source.id` (convention `BDC_LG_009002`). This is a fallback for compatibility with the BDC workflow.

---

## Idempotence and caching

`CubeClient.derive()` checks the cache before running:

```python
spec_hash = derivation.spec_hash()
cached_vars = [
    d for d in all_derived
    if d.spec_hash == spec_hash and self.store.fs.exists(d.asset_url)
]
if expected == cached_names:
    return cached_vars   # return without running the pipeline
```

Re-running the same derivation is safe and fast.
