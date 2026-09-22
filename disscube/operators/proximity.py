"""
Proximity operators — Euclidean distance and feature-count over vector sources.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import rasterio.features
import xarray as xr
from rasterio.warp import Resampling

from disscube.models.grid import GridSpec
from disscube.models.variable import Variable
from disscube.operators.base import Operator


class MinDistanceOperator(Operator):
    """
    Euclidean distance (in CRS units) from each grid cell to the nearest
    feature in the vector source.
    """
    name = "min_distance"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            from scipy.ndimage import distance_transform_edt
            shapes = ((g, 1) for g in data.geometry if g is not None)
            mask = rasterio.features.rasterize(
                shapes,
                out_shape=(grid.rows, grid.cols),
                transform=grid.transform,
                fill=0,
                all_touched=True,
            )
            dist = distance_transform_edt(1 - mask) * grid.resolution
            return xr.DataArray(
                dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs}
            )
        if isinstance(data, xr.DataArray):
            if "band" in data.dims:
                data = data.isel(band=0)
            return data.transpose("y", "x")
        raise TypeError(
            f"'min_distance' expects a vector source, got {type(data).__name__}"
        )


class CountOperator(Operator):
    """Count of vector features whose centroid falls within each grid cell."""
    name = "count"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            valid = data[data.geometry.notnull()]
            if valid.empty:
                counts = np.zeros((grid.rows, grid.cols), dtype=np.float64)
            else:
                minx, _miny, _maxx, maxy = grid.bbox
                res = grid.resolution
                centroids = valid.geometry.centroid
                cols_idx = ((centroids.x - minx) / res).astype(int)
                rows_idx = ((maxy - centroids.y) / res).astype(int)
                in_bounds = (
                    (cols_idx >= 0) & (cols_idx < grid.cols)
                    & (rows_idx >= 0) & (rows_idx < grid.rows)
                )
                flat = rows_idx[in_bounds] * grid.cols + cols_idx[in_bounds]
                counts = np.bincount(
                    flat, minlength=grid.rows * grid.cols
                ).reshape((grid.rows, grid.cols)).astype(np.float64)
            return xr.DataArray(
                counts, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs}
            )
        if isinstance(data, xr.DataArray):
            if "band" in data.dims:
                data = data.isel(band=0)
            return data.transpose("y", "x")
        raise TypeError(
            f"'count' expects a vector source, got {type(data).__name__}"
        )


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------

class ProximityAggregator:
    """
    Deprecated.  Use ``MinDistanceOperator`` / ``CountOperator`` directly.
    """

    @staticmethod
    def aggregate(data, variables, grid_spec):
        import warnings
        warnings.warn(
            "ProximityAggregator is deprecated; use OPERATOR_REGISTRY operators directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from disscube.operators.base import OPERATOR_REGISTRY
        var = variables[0]
        op_cls = OPERATOR_REGISTRY.get(var.operator)
        if op_cls is None:
            raise ValueError(f"Unknown operator: {var.operator}")
        return op_cls().compute(data, var, grid_spec)
