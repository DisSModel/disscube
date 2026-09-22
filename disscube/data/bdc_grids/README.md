# BDC Grid V2 — bundled copy

Tile grids of the [Brazil Data Cube](https://brazil-data-cube.github.io/specifications/bdc-grid.html)
(BDC), version 2, as ESRI Shapefiles zipped by the BDC GeoServer. They are
bundled so that `disscube.utils.bdc_importer.import_bdc_grids()` works offline
and against a fixed, checksummed definition of the tiles.

| File | Tiles | Tile size (m) | Approx. size | SHA-256 |
|---|---|---|---|---|
| `BDC_SM_V2.zip` | 871 | 105 600 × 105 600 | ~1° | `beac19f96be156707361c8a8898675b8da4ab9dbced2bab6dd3221d6bb39c945` |
| `BDC_MD_V2.zip` | 242 | 211 200 × 211 200 | ~2° | `637597ae6b11af7e6946fb6bf40beda9a7e8ade71227830ad2a2fed4e375c566` |
| `BDC_LG_V2.zip` | 75 | 422 400 × 422 400 | ~4° | `142ee56c668a46f79c82d86e18d692db37651a8736408ac41f864a6ab2d0d179` |

Each shapefile has a single attribute, `tile` (e.g. `"009002"`). Tile IDs are
unique within a level but **not across levels** (e.g. `005004` exists in both
SM and MD), so a tile is identified by level + ID (`BDC_SM_005004`).

## Provenance

- Source: BDC GeoServer WFS, `outputFormat=shape-zip`, layers
  `bdc_catalog:BDC_SM_V2`, `bdc_catalog:BDC_MD_V2`, `bdc_catalog:BDC_LG_V2`,
  as listed on the BDC grid specification page:
  <https://brazil-data-cube.github.io/specifications/bdc-grid.html>
- Retrieved: 2026-05-10 (timestamps of the archive members).
- Files are unmodified.

## Coordinate reference system

Albers Equal-Area Conic, GRS80 — identical to `disscube.utils.grids.BDC_CRS`:

```
+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22
+x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs
```

The `.prj` files end with `AUTHORITY["EPSG","200000"]`, a code that does not
exist in the EPSG registry. Tools that resolve the CRS from the file (for
example `geopandas.read_file`) therefore fail on them; the importer reads the
geometries with fiona and assigns `BDC_CRS` instead.

Tile corners lie on a 100 m mesh, but **not** on the 1 km or 5 km meshes used
by the `BR/1km` and `BR/5km` simulation grids.

## License

These files are © INPE (Brazil Data Cube project). The BDC project states
that it is licensed under the GNU GPL v3
(<https://brazil-data-cube.github.io/license.html>); no separate license is
stated for the grid files themselves. They are redistributed here unmodified,
with this attribution, as data aggregated with DisSCube — **they are not
covered by DisSCube's MIT license.**
