# DisSCube

DisSCube is the spatial data cube engine of the **DisSModel** ecosystem. It converts raw geospatial sources into derived variables aligned to LUCC (Land Use and Cover Change) modeling grids.

## Core concept

```
SpatialSource  →  Derivation  →  Variable  →  DerivedVariable (Zarr)
```

A **source** (`SpatialSource`) goes through a **derivation** that applies an **operator** on a **grid** (`GridSpec`), producing a variable registered in the SQLite catalog and stored in Zarr.

## Quick install

```bash
git clone https://github.com/DisSModel/disscube.git
cd disscube
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Main workflow

```python
from disscube.client import CubeClient
from disscube.derivation import Derivation

cube = CubeClient(catalog="catalog.db", store="./data/")

d = Derivation(
    target="forest_pct",
    source_id="mapbiomas_2020",
    operator="percentage",
    class_code=3,
    valid_from="2020",
    valid_until="2020",
)
cube.derive_declarative(d, grid_id="AC/5km")

da = cube.load("forest_pct", grid_id="AC/5km")
```

## Navigation

- [**Examples**](examples.md) — runnable scripts: the API on synthetic data, and TerraME's *Fill* examples reproduced on real data
- [**Architecture**](architecture/overview.md) — conceptual model, pipeline and reproducibility hash
- [**Operators**](architecture/operators.md) — plugin system, available operators, how to add new ones
- [**Pipeline**](architecture/pipeline.md) — detailed stages: Normalizer → GridAligner → Aggregator → Writer
- [**Catalog**](architecture/catalog.md) — SQLite, schema, hash and time series
- [**Grids**](guides/grids.md) — snapping to the national mesh, local grids, spatial relations
- [**BDC guide**](guides/bdc.md) — integration with Brazil Data Cube and tile-based processing
