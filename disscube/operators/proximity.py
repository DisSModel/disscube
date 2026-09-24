"""
Proximity operators — Euclidean distance and feature-count over vector sources.
"""

from __future__ import annotations

import warnings

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
            if not mask.any():
                # No feature inside the grid: the distance transform has no
                # target. Use ``distance`` for features outside the grid.
                warnings.warn(
                    f"min_distance: no feature of '{var.name}' falls inside the grid; "
                    "returning NaN — use operator 'distance' for features outside the grid",
                    RuntimeWarning, stacklevel=2,
                )
                dist = np.full((grid.rows, grid.cols), np.nan)
            else:
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


class DistanceOperator(Operator):
    """
    Exact Euclidean distance (in CRS units) from each cell centre to the
    nearest feature of a vector source.

    Unlike ``min_distance`` (a raster approximation between rasterized cell
    centres), this measures the true distance from the cell centre to the
    geometry, and the source is not clipped to the grid, so features outside
    it — a town 50 km away, a road beyond the edge — count. Distances are in
    the grid CRS units (degrees on a geographic grid), measured from cell
    centres; TerraME's ``distance`` fill measures from the cell polygon, so
    it is smaller by up to half a cell diagonal.
    """

    name = "distance"
    _resampling = Resampling.nearest
    clip_to_grid = False

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            import shapely

            geoms = [g for g in data.geometry if g is not None and not g.is_empty]
            if not geoms:
                dist = np.full((grid.rows, grid.cols), np.nan)
            else:
                xx, yy = np.meshgrid(grid.xs, grid.ys)
                centres = shapely.points(xx.ravel(), yy.ravel())
                tree = shapely.STRtree(geoms)
                (points_idx, _), d = tree.query_nearest(centres, return_distance=True, all_matches=False)
                flat = np.full(centres.shape, np.nan)
                flat[points_idx] = d
                dist = flat.reshape((grid.rows, grid.cols))
            return xr.DataArray(dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})
        raise TypeError(f"'distance' expects a vector source, got {type(data).__name__}")


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
