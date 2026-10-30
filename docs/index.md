# DisSCube

DisSCube is the spatial data cube engine of the **DisSModel** ecosystem. It converts raw geospatial sources into derived variables aligned to LUCC (Land Use and Cover Change) modeling grids.

## Core concept

```
SpatialSource  →  Derivation  →  Variable  →  DerivedVariable (Zarr)
```

A **source** (`SpatialSource`) goes through a **derivation** that applies an **operator** on a **grid** (`GridSpec`), producing a variable registered in the SQLite catalog and stored in Zarr.

## Quick install

```bash
pip install disscube                 # from PyPI
pip install "disscube[dissmodel]"    # + hand-off of the cube to DisSModel models
```

From a clone, for development:

```bash
git clone https://github.com/DisSModel/disscube.git
cd disscube
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Main workflow

```python
from disscube.client import CubeClient
from disscube.models import Derivation

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

---

## The DisSModel ecosystem

| Package | Role | Documentation |
|:---|:---|:---|
| [**dissmodel**](https://github.com/DisSModel/dissmodel) | Core framework: clock, spatial substrates, executor, I/O, visualization | [dissmodel.github.io/dissmodel](https://dissmodel.github.io/dissmodel/) |
| [**disscube**](https://github.com/DisSModel/disscube) | Declarative pipelines that turn raster and vector sources into model-ready grids | [dissmodel.github.io/disscube](https://dissmodel.github.io/disscube/) |
| [**disslucc**](https://github.com/DisSModel/disslucc) | Land use and cover change: demand, potential and allocation | [dissmodel.github.io/disslucc](https://dissmodel.github.io/disslucc/) |
| [dissmodel-ca](https://github.com/DisSModel/dissmodel-ca), [dissmodel-sysdyn](https://github.com/DisSModel/dissmodel-sysdyn), [dissmodel-abm](https://github.com/DisSModel/dissmodel-abm) | Ready-to-use cellular automata, system dynamics and agent-based models | GitHub |

Developed by the [LambdaGeo](https://github.com/LambdaGeo) group at the Federal University of Maranhão (UFMA). MIT licence.
