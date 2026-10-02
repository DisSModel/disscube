# OpenStreetMap

`type = "osm"` takes roads, rivers and other **ways** from OpenStreetMap (Overpass API) and
registers them as a vector source, clipped to the grid plus a margin, with a checksum and
provenance. It is the usual input of the `distance` and `count` operators.

```toml
[grid]
name = "area"
bbox = [-54.842, -3.587, -54.459, -3.168]
resolution = 500

[[source]]
id     = "br163"
type   = "osm"
query  = 'way["highway"]["ref"~"BR-163"]'
margin = 0.7                              # degrees; distances need features beyond the grid

[[derive]]
target   = "dist_br"
source   = "br163"
operator = "distance"
```

```bash
export OSM_CONTACT="you@example.org"      # Overpass answers HTTP 406 to anonymous clients
disscube run area.toml --workspace outputs/area
```

## Fields

| Field | Meaning |
|---|---|
| `query` | Overpass statements **without** the bounding box, as a string separated by `;` or a list. Only `way[...]` filters are read. |
| `margin` | Degrees added around the grid (default `0`). A `distance` to a road outside the grid needs it; a `count` of roads inside does not. |
| `date` | The map as it was on that day (`2019-01-01` or a full ISO timestamp), through Overpass's `[date:"…"]`. |
| `endpoints` | Overpass servers to try, in order (default: four public ones). Use it for your own instance. |
| `cache` | Cache folder, relative to the pipeline file (default `$DISSCUBE_CACHE/osm` or `~/.cache/disscube/osm`). |
| `time` | Time of the source; set from `years` when you use `{year}`. |

Features come back as `LineString` in EPSG:4326 with a few tags (`name`, `ref`, `highway`,
`waterway`, `natural`, `railway`, `boundary`). A closed way, such as a reservoir, stays as its outline.

## Reproducibility

OpenStreetMap changes every minute, so DisSCube fetches **once** and keeps the answer in the
cache, keyed by what was asked (statements, bounding box, date). Every later run, in any
workspace, reuses it: the same bytes, the same checksum, the same `spec_hash`. To get fresh
data, delete the cache file. `raw/<id>.provenance.json` records the request, the server, the
time it was retrieved, the `timestamp_osm_base` of the data, the number of features, the
checksum and the licence (ODbL, © OpenStreetMap contributors).

An empty answer is an error and is not cached: it almost always means a wrong query or area.

## Maps of the past

OpenStreetMap is today's map by default, so roads opened after the period you study leak into
the drivers. With `date` the query asks for an older state; with `years` you get one snapshot
per year:

```toml
[[source]]
id     = "roads_{year}"
type   = "osm"
query  = 'way["highway"]'
date   = "{year}-07-01"
years  = [2010, 2015, 2020]
margin = 0.1
```

OpenStreetMap is sparse in many regions before the mid-2000s, so an old snapshot is a
lower bound, not the road network of the time.

## Etiquette

The public Overpass servers are shared. Keep queries small, set `OSM_CONTACT`, and rely on the
cache instead of running a pipeline repeatedly with a cleared one. Failures (HTTP 5xx,
timeouts) move on to the next server and retry; HTTP 406 stops at once with a message about
`OSM_CONTACT`.
