# Operator System

DisSCube implements operators as self-registering Python classes. Adding a new operator requires no change to the pipeline.

## How it works

Every subclass of `Operator` that defines `name` is automatically inserted into `OPERATOR_REGISTRY` via `__init_subclass__`:

```python
# operators/base.py
class Operator:
    name: ClassVar[str]
    requires_class_code: ClassVar[bool] = False
    _resampling: ClassVar[Resampling] = Resampling.nearest

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name"):
            OPERATOR_REGISTRY[cls.name] = cls   # self-registration

    @classmethod
    def resampling(cls) -> Resampling:
        return cls._resampling

    def compute(self, data, var, grid) -> xr.DataArray:
        raise NotImplementedError
```

`GridAligner` queries `op_cls.resampling()` to choose the resampling method before reprojecting the raster. `Aggregator` calls `op_cls().compute(data, var, grid)` to compute the result. Neither of them contains a list of operators.

## Available operators

### Zonal, with direct resampling

These operators receive from `GridAligner` a `DataArray` already reprojected to the target resolution. `_resampling` determines the reprojection method.

| Operator | `_resampling` | Raster | Vector | `requires_class_code` |
|---|---|---|---|---|
| `mean` | `average` | mean of the pixels on upscale | — | no |
| `sum` | `sum` | sum of the pixels on upscale | — | no |
| `min` | `min` | minimum on upscale | — | no |
| `max` | `max` | maximum on upscale | — | no |
| `attribute` | `nearest` | passthrough | rasterizes with the value of column `var.name` | no |
| `presence` | `nearest` | passthrough | rasterizes binary with `class_code` (or 1) | no |

### Zonal, with fine alignment (`needs_fine_alignment = True`)

These operators receive a high-resolution array snapped to the target grid origin. `GridAligner` reprojects with `Resampling.nearest` at a fine resolution (an integer sub-multiple of the cell size); the operator then reduces over real windows of the raw pixels.

| Operator | Raster | Vector | `requires_class_code` |
|---|---|---|---|
| `std` | true per-cell standard deviation | — | no |
| `majority` | dominant class by count | rasterizes with `class_code` (or 1) | no |
| `minority` | least-frequent class by count | rasterizes with `class_code` (or 1) | no |
| `percentage` | fraction of pixels of the target class | rasterizes with `class_code` | **yes** |

The last three also produce `coverage_purity` and `dominance_purity` as coordinates of the output `DataArray` (persisted in the Zarr alongside the variable).

### Proximity — vector (and passthrough for raster)

| Operator | Description | `requires_class_code` |
|---|---|---|
| `distance` | Exact Euclidean distance (CRS units) from each cell centre to the nearest feature; the source is not clipped to the grid | no |
| `min_distance` | Raster approximation of the distance to the nearest feature inside the grid (NaN, with a warning, if none is inside) | no |
| `count` | Number of features whose centroid falls in each cell | no |

## The `compute()` contract

```python
def compute(
    self,
    data: xr.DataArray | gpd.GeoDataFrame,
    var: Variable,
    grid: GridSpec,
) -> xr.DataArray:
```

- `data`: for raster sources, the `DataArray` already reprojected and resampled by `GridAligner`; for vectors, the `GeoDataFrame` reprojected and clipped to the bbox.
- Return value: an `xr.DataArray` with `dims=("y", "x")` and `coords` aligned to `grid.ys` / `grid.xs`.

## Adding a new operator

Create the file `disscube/operators/my_operator.py`:

```python
from rasterio.warp import Resampling
import xarray as xr
import numpy as np
from disscube.operators.base import Operator

class WeightedMeanOperator(Operator):
    """Mean weighted by intersection area (illustrative example)."""
    name = "weighted_mean"
    _resampling = Resampling.average  # used by GridAligner

    def compute(self, data, var, grid) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            if "band" in data.dims:
                data = data.isel(band=0)
            return data.transpose("y", "x")
        raise TypeError(f"'weighted_mean' requires a raster source")
```

Import the module so that `__init_subclass__` runs — just add it to `disscube/operators/__init__.py`:

```python
from . import my_operator  # noqa: F401
```

Done. The operator shows up in `OPERATOR_REGISTRY["weighted_mean"]` and is accepted by `Derivation(operator="weighted_mean")` and by `Variable(operator="weighted_mean")`.

## `attribute` — implicit contract

The `attribute` operator rasterizes a vector using the value of a numeric column as the pixel value. **The column must have the same name as the variable (`Variable.name`)**:

```python
# Vector source with columns "f" and "d"
Variable(name="f", operator="attribute")   # uses gdf["f"]
Variable(name="d", operator="attribute")   # uses gdf["d"]
```

If the column does not exist in the GeoDataFrame, the result is a raster of zeros with no explicit error. Make sure the variable name matches the column name in the source.

## Construction-time validation (`Derivation`)

`Derivation` validates the operator name and required fields at creation time:

```python
# Immediate error — unknown operator
Derivation(target="x", source_id="s", operator="bogus")
# ValueError: Unknown operator 'bogus'. Available: ['attribute', 'count', ...]

# Immediate error — percentage without class_code
Derivation(target="x", source_id="s", operator="percentage")
# ValueError: Operator 'percentage' requires class_code to be set.
```

`SpatialDerivation` and `Variable` do not validate — the error surfaces in `Aggregator` at run time. Use `Derivation` for early validation.
