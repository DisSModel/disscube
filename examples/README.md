# DisSCube — Examples

A structured walkthrough of the DisSCube pipeline, from catalog bootstrap to
complete case studies.

## Execution order

### 1. Setup (one-time)
Catalog bootstrap and registration of the base data.
- `python examples/setup/01_init_catalog.py` — registers national and local grids.
- `python examples/setup/02_register_sources.py` — registers raw files as SpatialSources.

### 2. National drivers
Derives variables on the national BR/5km grid.
- `python examples/drivers/01_brazil_national.py` — slope, indigenous lands (TI), distance to cities/rivers.

### 3. Case study: Maranhão (Ilha do Maranhão, 100 m)
Two studies over the same geographic area and grid.
- `python examples/case_studies/maranhao/01_mapbiomas_temporal.py` — MapBiomas time series (`uso`, majority) + static `dist_sedes`.
- `python examples/case_studies/maranhao/02_brmangue_derive.py` — derives `uso`, `alt`, `solo` for the BR-MANGUE model.
- `python examples/case_studies/maranhao/03_brmangue_simulate.py` — runs BrmangueRasterExecutor.

### 4. Case study: Acre (AC/5km)
- `python examples/drivers/02_acre_5km.py` — regional drivers for Acre at 5 km.
- `python examples/case_studies/lucc_acre/01_derive.py` — land-use attributes from a vector source.
- `python examples/case_studies/lucc_acre/02_simulate.py` — runs LUCCRasterExecutor.
- `python examples/case_studies/lucc_acre/03_temporal_drivers.py` — simulation loop with temporal drivers.

---

## Utilities (`tools/`)

| Script | Purpose |
|---|---|
| `tools/zarr_to_tif.py` | Converts a derived Zarr to GeoTIFF |
| `tools/import_bdc_tiles.py` | Imports BDC SM/MD/LG tiles into the catalog (one-time, slow) |

```bash
python tools/zarr_to_tif.py data/derived/.../var.zarr output.tif
python tools/import_bdc_tiles.py
```
