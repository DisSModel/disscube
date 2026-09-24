# Catalog and Persistence

The catalog is the registry of everything the system knows about. It stores no pixels — only metadata, pointers and hashes.

## Implementations

The interface is defined by the `CatalogStore` Protocol (`disscube/catalog/protocol.py`):

| Implementation | File | Use |
|---|---|---|
| `SqliteCatalogStore` | `catalog/sqlite_store.py` | Default — used by `CubeClient` |
| `JsonCatalogStore` | `catalog/json_store.py` | Legacy / simple tests |

## SQLite schema

```sql
grids    (id TEXT PRIMARY KEY, data TEXT)          -- GridSpec as JSON
sources  (id TEXT PRIMARY KEY, data TEXT)          -- SpatialSource as JSON
derived  (id, grid_id, spec_hash, tile_id, role, data TEXT)
relations (source_grid_id, target_grid_id, data TEXT)
```

Indexes: `idx_derived_grid`, `idx_derived_hash`, `idx_derived_tile`.

All objects are serialized as JSON in the `data` field. The extracted columns (`grid_id`, `spec_hash`, `tile_id`) exist for indexing and efficient lookup — the complete data always lives in the JSON.

## Specification hash (`spec_hash`)

A deterministic SHA-256 of the `SpatialDerivation`:

```python
relevant_data = {
    "source_id":   ...,
    "grid_id":     ...,
    "role":        ...,
    "variables":   [...],   # sorted by name
    "valid_from":  ...,
    "valid_until": ...,
}
if source_checksum is not None:          # copied from SpatialSource.checksum
    relevant_data["source_checksum"] = source_checksum
encoded = json.dumps(relevant_data, sort_keys=True).encode("utf-8")
return hashlib.sha256(encoded).hexdigest()
```

**What changes the hash:**

- Changing the source (`source_id`)
- Changing the grid (`grid_id`)
- Adding/removing/renaming variables
- Changing the operator or `class_code`
- Changing `valid_from` / `valid_until`
- Changing the source's `checksum` — `CubeClient.derive()` copies
  `SpatialSource.checksum` into the derivation, so a replaced source file
  registered with a new checksum yields a new product instead of a stale cache
  hit. Use `disscube.utils.files.sha256_file()` to compute it;
  `disscube.utils.bdc_stac.register_bdc_source()` does it for you.

**What does not change the hash:**

- The order of the variables in the list (they are sorted by name)
- Anything about a source registered **without** a checksum: its file can
  change and the cached product is still returned. Sources without a checksum
  keep the hash they had before checksums entered it, so older catalogs stay
  valid.
- The `bbox` of a `Derivation` (descriptive metadata, not a parameter)
- `SpatialRelation` — relations are persisted in the catalog but excluded from the hash because no pipeline stage uses them during computation. Including them would make the cache key sensitive to metadata that does not affect the result.

## Content hash (`content_hash`)

SHA-256 of all the bytes of the files in the Zarr directory, in deterministic order (`sorted(root.rglob("*"))`). It guarantees the integrity of the materialized data independently of `spec_hash`.

## Time series

The `times` field of `DerivedVariable` is a list of integers (years):

- `times = []` → static variable
- `times = [2020]` → 2020 time slice

`CubeClient.load()` automatically detects whether there are multiple slices and stacks them into `(time, y, x)`, ordered by the first value of `times`.

`CubeClient.to_lucc_data()` accepts `period=("2000", "2020")` to keep only the slices within the interval.

## Querying the catalog

```python
# By grid and role
cube.catalog.search_derived_variables(grid_id="AC/5km", role="driver")

# By tile
cube.catalog.search_derived_variables(tile_id="009002")

# By spec_hash (exact)
cube.catalog.get_derived_by_hash("a3f9...")

# Delete an entry by ID
cube.catalog.delete_derived("a3f9..._slope")
```

## Cleaning up orphan entries

The catalog accumulates entries whose Zarr files were deleted (common when wiping the store to re-test). `purge_stale()` removes those entries:

```python
n = cube.purge_stale()   # returns the number of entries removed
print(f"Removed {n} orphan entries")
```

`load()` already silently ignores entries with no file on disk — `purge_stale()` is an explicit cleanup to keep the catalog lean.

## Schema evolution

The schema uses `CREATE TABLE IF NOT EXISTS` — safe for idempotence. New columns require `ALTER TABLE` or a manual migration. Extra descriptive model fields (such as `purity_threshold` on `Derivation`) live only in the Python model, not in the database — this protects backward compatibility for reserved fields.
