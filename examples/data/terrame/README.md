# TerraME *Fill* datasets

Input layers and reference outputs of the three *Fill* examples shipped with
TerraME's `gis` package. They are used by examples
[`04_terrame_fill_itaituba.py`](../../04_terrame_fill_itaituba.py),
[`05_terrame_fill_emas.py`](../../05_terrame_fill_emas.py) and
[`06_terrame_fill_amazonia.py`](../../06_terrame_fill_amazonia.py),
and by the parity tests (`tests/test_terrame_parity.py`), which compare
DisSCube cell by cell with the cellular spaces TerraME itself produced.

## Source

- Repository: <https://github.com/TerraME/terrame>, directory `packages/gis/data/`
- Commit: `84faddc4e5149a89d7486444b61b8c50474e199c` (2023-08-08)
- License: GNU LGPL — version 3 according to the repository's `LICENSE`;
  the headers of the `.lua` scripts say version 2.1. © INPE and TerraLAB/UFOP.
  These files are redistributed unmodified with this attribution and are
  **not** covered by DisSCube's MIT license.

## Layout

Each shapefile is stored as one zip (`.shp`, `.shx`, `.dbf`, `.prj`) and is
read directly, without unpacking, through a `zip://` path, e.g.
`zip:///…/itaituba/itaituba-roads.zip`. The members are byte-identical to
the files in the TerraME repository; the spatial-index `.qix` files and the
`.tme` window layouts were left out. Rasters are the original GeoTIFFs. Each
directory also keeps TerraME's script (`<name>.lua`) that produced the
reference cells.

| Dataset | TerraME script | Reference cells | Resolution | CRS | Fill operations |
|---|---|---|---|---|---|
| `itaituba/` | `itaituba.lua` | `itaituba.zip` (620) | 5 km | EPSG:29191 | `average`, `coverage`, `distance` (lines, points), `sum` with `area = true` |
| `emas/` | `emas.lua` | `emas.zip` (5 514) | 500 m | EPSG:29192 | `presence` (lines), `maximum`, `minimum` |
| `amazonia/` | `amazonia.lua` | `amazonia.zip` (2 229) | 50 km | EPSG:29191 | `coverage`, `distance` (lines, points), `area` |

## Notes for reproducing TerraME's grids

- **The `.prj` of the reference cells is wrong.** `itaituba` and `amazonia`
  declare `NAD83_Austin` (a Lambert conformal projection for Texas) and
  `emas` a generic UTM 22S; the cells are actually in the CRS of the input
  layers (EPSG:29191 or EPSG:29192), which must be assigned explicitly.
- **Grid.** Cells are 5 km / 500 m / 50 km squares whose extent is the total
  bounds of the reference layer. With `input = "limit"` (Emas, Amazônia)
  TerraME keeps only the cells that touch the study-area limit, so the
  reference is a subset of the full rectangle; match cells by centroid.
- **Rows.** TerraME numbers rows from the south (`row = 0` is the southern
  row); DisSCube grids are north-up.

## Checksums (SHA-256)

| File | SHA-256 |
|---|---|
| `itaituba/itaituba-census.zip` | `24f1c5afcf4b17a1c9b4d30a721067354dc56697654c754f309471eb55a605f8` |
| `itaituba/itaituba-deforestation.tif` | `b31c9b9cf492e56609a859fa82566066aec34f51c977c922f341f1ef1be5dbf6` |
| `itaituba/itaituba-elevation.tif` | `f9fac6f61ed6ac29c1e543c8c6cfc239148eb6b308b2bcb0ff915f42abe2879c` |
| `itaituba/itaituba-localities.zip` | `0f50cd16bf2ff4c9aaa6347484eef9e848c5f2285741d199461db9d6831bf00e` |
| `itaituba/itaituba-roads.zip` | `f15b8ccc4ce484eb42393519e333607a8d93e4cbfa73055e3c1d775d4cf66749` |
| `itaituba/itaituba.lua` | `6ac758ce249779ee26f613f46098d557ffa7f382e0598263220c13bf079d8060` |
| `itaituba/itaituba.zip` | `76ac2b17b8aafbc90a0d7e3939e4602adbe76331c720f470009d255f09d09856` |
| `emas/emas-accumulation.tif` | `d3ef918861c36c46a7333d79346dbb54a3e48b80468787715ddd5ef317e2e58a` |
| `emas/emas-firebreak.zip` | `618859dbf0b7a3a22919d7180c7a7f3026712c2dc2eca2aed700d25acd5f77ba` |
| `emas/emas-limit.zip` | `66aaffc986d5ac2e6d14e543f0aaa27ab02fdf849005deaa6b73efbd3486e652` |
| `emas/emas-river.zip` | `f58b31befd67e716bf9a5c79502988b470b986673dd9e586924177f8ea4c6ab6` |
| `emas/emas.lua` | `b3fdfab361c9007691c893a7fb7de4ae33cee557fe26aa41b64ad1c64793805a` |
| `emas/emas.zip` | `cce329dc25d5d6243a0233b7d8adb993b94a637ac167c6b4bd553277dcb82483` |
| `amazonia/amazonia-indigenous.zip` | `46674013fd6b3bcfd9fee46595913a1d80ded53f4d0c82f5510ed332aa0c3574` |
| `amazonia/amazonia-limit.zip` | `d44ac5514302a09005b39215e859b53b353a08da2267fb841959f7dfc3e2674b` |
| `amazonia/amazonia-ports.zip` | `c7e537ca771ad1d7cfa0b17a050179ed5686825b685615f34d7304d975628b35` |
| `amazonia/amazonia-prodes.tif` | `28fef2ada04460ce05abf1ce40582cf08c928e6bc766610473c5ff7968ab365a` |
| `amazonia/amazonia-roads.zip` | `a5f503e80acb3328d9dd815125acbf32f2a9320a32438af748e28bf3cbfd3495` |
| `amazonia/amazonia.lua` | `0bbd3d0885fff24e58914c6205ac649347c0385a3f95c05360984c727dd34477` |
| `amazonia/amazonia.zip` | `97c01fa6efbee8e0775054176728e023e86b7ad47e31cf496ab944674f561893` |
