# Land-cover maps: PRODES and classified maps (SITS)

Besides MapBiomas, two adapters bring categorical land-cover maps into a cube.
Both follow the same contract as every source: a local GeoTIFF (`uint8`,
nodata 0 by default), its SHA-256 as the source `checksum`, and a
`<source>.provenance.json` that records the legend used.

## PRODES (`disscube.sources.prodes`)

[PRODES](https://data.inpe.br/biomasbr/prodes-monitoramento-anual-da-supressao-de-vegetacao-nativa/)
maps deforestation in the Legal Amazon every year. TerraBrasilis distributes
one ZIP per edition with a national GeoTIFF and a QGIS style file (`.qml`)
holding its legend.

```python
from disscube.sources import prodes

files = prodes.download()                     # once; cached in ~/.cache/disscube/prodes
for year in (2008, 2016, 2024):
    prodes.register_prodes_source(cube, f"prodes_{year}", year, bbox, out_dir="raw",
                                  files=files)
```

- **Download and cache.** `download()` fetches the ZIP to a temporary name and
  renames it when complete, so an interrupted download never leaves a broken
  file; later calls reuse it. Set `DISSCUBE_CACHE` to move the cache.
  `latest_url()` asks TerraBrasilis' own download index for the newest edition.
- **Legend from the file.** Codes are read from the `.qml` (`"7 d2007"`,
  `"91 Hidrografia"`, `"100 Vegetação nativa florestal"`, …), not hard-coded,
  so a new edition with another encoding is read correctly or fails loudly.
- **One map per year.** For a year Y the local map has `FOREST` (1),
  `DEFORESTED` (2, any `dYYYY` or residual `rYYYY` up to Y) and `OTHER` (3,
  non-forest vegetation and water); clouds and unknown codes are nodata.
- **Years.** The edition distributed today separates deforestation by year
  from 2007 on (`d2007` holds everything cleared up to then). Earlier years
  raise an error; use MapBiomas for them.

The provenance records the edition, its URL, the ZIP's SHA-256, the legend and
the code mapping.

## Any classified map, e.g. from SITS (`disscube.sources.classified`)

[SITS](https://e-sensing.github.io/sitsbook/) classifies satellite image time
series — including the Brazil Data Cube cubes — into land-cover maps written
as GeoTIFFs. `register_classified_map()` reads such a map (or any categorical
GeoTIFF, local or a Cloud-Optimized URL) over an area and registers it with
its legend:

```python
from disscube.sources.classified import register_classified_map

register_classified_map(
    cube, "lulc_sits_2020", "classified/santarem_class_2020.tif", bbox, out_dir="raw",
    time=2020, legend={1: "Forest", 2: "Pasture", 3: "Water"},   # or .qml / .json / .csv
    producer="SITS",
)
```

The legend may be a mapping, a QGIS `.qml`, a `.json` (`{"1": "Forest"}` or a
list of `{"code", "label"}`) or a `.csv` with `code,label` columns. A map that
uses class 0 needs an explicit `nodata=` so that 0 is not taken as missing.

To remap codes before registering — e.g. to merge classes — read the map with
`disscube.sources.read_window`, apply `disscube.sources.reclassify(window,
{old: new})` and register the result with `disscube.sources.register_raster`.
