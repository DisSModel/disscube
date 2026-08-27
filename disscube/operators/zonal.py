"""
Zonal operators — window-based aggregates over raster and vector sources.

Each class registers itself automatically into ``OPERATOR_REGISTRY`` via
``Operator.__init_subclass__``.  No changes to the pipeline are required
when a new operator is added here.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
import geopandas as gpd
import rasterio.features
from rasterio.warp import Resampling

from disscube.operators.base import Operator
from disscube.models.variable import Variable
from disscube.models.grid import GridSpec


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _rasterize(shapes: list, grid: GridSpec) -> np.ndarray:
    """Rasterize ``(geometry, value)`` pairs onto ``grid``, returning float32."""
    if not shapes:
        return np.zeros((grid.rows, grid.cols), dtype=np.float32)
    return rasterio.features.rasterize(
        shapes,
        out_shape=(grid.rows, grid.cols),
        transform=grid.transform,
        fill=0,
        all_touched=False,
    ).astype(np.float32)


def _wrap(arr: np.ndarray, grid: GridSpec) -> xr.DataArray:
    """Wrap a 2-D array as a grid-aligned DataArray."""
    return xr.DataArray(arr, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})


def _passthrough(data: xr.DataArray) -> xr.DataArray:
    """Return the raster band as a plain (y, x) DataArray."""
    if "band" in data.dims:
        data = data.isel(band=0)
    return data.transpose("y", "x")


# ---------------------------------------------------------------------------
# Window-based categorical aggregation (fine grid -> target grid)
# ---------------------------------------------------------------------------

def _fine_array(data: xr.DataArray) -> tuple[np.ndarray, float | None]:
    """
    Extract the fine 2-D values and nodata from an origin-snapped DataArray.

    The aligner annotates fine arrays for categorical operators; this returns
    the (rows, cols) ndarray plus the nodata sentinel (if any).
    """
    if "band" in data.dims:
        data = data.isel(band=0)
    data = data.transpose("y", "x")
    arr = np.asarray(data.values, dtype=np.float64)
    nodata = data.attrs.get("_disscube_nodata", None)
    if nodata is None:
        try:
            nodata = data.rio.nodata
        except Exception:
            nodata = None
    return arr, nodata


def _windows(n_fine: int, n_target: int) -> list[tuple[int, int]]:
    """
    Build per-target-cell (start, stop) index ranges along one axis.

    Splits ``n_fine`` fine pixels into ``n_target`` contiguous windows. When
    ``n_fine`` is not an exact multiple of ``n_target`` the remainder pixels
    are distributed so every fine pixel belongs to exactly one window (the
    last windows absorb the extra pixels). This makes non-multiple ratios
    (e.g. 10 m -> 30 m over a 100 m extent) well-defined without a reshape.
    """
    if n_target <= 0:
        return []
    edges = np.linspace(0, n_fine, n_target + 1)
    edges = np.round(edges).astype(int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n_target)]


def _categorical_reduce(
    fine: np.ndarray,
    nodata: float | None,
    grid: GridSpec,
    mode: str,
    class_code: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Reduce a fine categorical array into the target grid.

    Parameters
    ----------
    fine : np.ndarray
        Fine-resolution (rows, cols) class array, origin-snapped to the grid.
    nodata : float | None
        Sentinel marking invalid fine pixels.
    grid : GridSpec
        Target grid (provides ``rows`` / ``cols``).
    mode : {"percentage", "majority", "minority"}
        Aggregation to compute for the primary output.
    class_code : int | None
        Target class for ``percentage``.

    Returns
    -------
    value : np.ndarray
        Primary output, shape (grid.rows, grid.cols).
        ``percentage`` -> fraction in 0..1; ``majority``/``minority`` -> class.
    coverage_purity : np.ndarray
        valid_pixels / total_pixels per cell (universal metric).
    dominance_purity : np.ndarray
        count(dominant_class) / valid_pixels per cell (categorical metric).
    """
    fr, fc = fine.shape
    tr, tc = grid.rows, grid.cols

    if fr == tr and fc == tc:
        # Fast path: fine resolution already equals target resolution
        # (GridAligner._align_fine's factor == 1, the common case whenever
        # the source is already at the target's native resolution) -- every
        # target cell maps to exactly ONE fine pixel, so this is an
        # identity remap, not a real window reduction. The general loop
        # below computes "mode of 1 value" via np.unique per target cell;
        # for a multi-million-cell real grid that Python-level per-cell
        # cost dominates total derive() wall time (confirmed: ~30us/cell,
        # so minutes per variable on a real MapBiomas-sized grid even after
        # GridAligner stopped reading/reprojecting the whole source raster).
        # Vectorize the n=1 case directly instead of looping.
        if nodata is not None and not np.isnan(nodata):
            valid_mask = fine != nodata
        else:
            valid_mask = np.ones_like(fine, dtype=bool)
        valid_mask &= ~np.isnan(fine)

        coverage = valid_mask.astype(np.float64)
        dominance = np.where(valid_mask, 1.0, np.nan)

        if mode == "percentage":
            hit = valid_mask & (fine == class_code)
            value = np.where(valid_mask, hit.astype(np.float64), np.nan)
        else:  # majority / minority: with n=1 both equal the single value
            value = np.where(valid_mask, fine.astype(np.float64), np.nan)

        return value, coverage, dominance

    value = np.full((tr, tc), np.nan, dtype=np.float64)
    coverage = np.zeros((tr, tc), dtype=np.float64)
    dominance = np.full((tr, tc), np.nan, dtype=np.float64)

    row_windows = _windows(fr, tr)
    col_windows = _windows(fc, tc)

    for ti, (r0, r1) in enumerate(row_windows):
        for tj, (c0, c1) in enumerate(col_windows):
            block = fine[r0:r1, c0:c1]
            total = block.size
            if total == 0:
                continue

            if nodata is not None and not np.isnan(nodata):
                valid_mask = block != nodata
            else:
                valid_mask = ~np.isnan(block)
            valid_mask &= ~np.isnan(block)

            n_valid = int(valid_mask.sum())
            coverage[ti, tj] = n_valid / total
            if n_valid == 0:
                continue

            vals = block[valid_mask].astype(np.int64)
            classes, counts = np.unique(vals, return_counts=True)

            # dominant class = highest count; tie -> smallest class value
            dom_idx = np.lexsort((classes, -counts))[0]
            dom_class = int(classes[dom_idx])
            dom_count = int(counts[dom_idx])
            dominance[ti, tj] = dom_count / n_valid

            if mode == "percentage":
                hit = int(counts[classes == class_code].sum()) if class_code in classes else 0
                value[ti, tj] = hit / n_valid
            elif mode == "majority":
                value[ti, tj] = dom_class
            elif mode == "minority":
                min_idx = np.lexsort((classes, counts))[0]
                value[ti, tj] = int(classes[min_idx])

    return value, coverage, dominance


def _attach_purity(
    value: np.ndarray,
    coverage: np.ndarray,
    dominance: np.ndarray,
    grid: GridSpec,
) -> xr.DataArray:
    """Wrap the primary value and attach purity arrays as coordinates."""
    da = xr.DataArray(value, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})
    da = da.assign_coords(
        coverage_purity=(("y", "x"), coverage),
        dominance_purity=(("y", "x"), dominance),
    )
    return da


def _continuous_reduce(
    fine: np.ndarray,
    nodata: float | None,
    grid: GridSpec,
    stat: str,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Reduce a fine continuous array into the target grid by real windows.

    Used by operators that need a per-cell statistic not directly available
    from a single rasterio resampling pass (notably ``std``). Returns the
    statistic and the coverage purity (valid_pixels / total_pixels) per cell.

    Parameters
    ----------
    fine : np.ndarray
        Fine-resolution (rows, cols) value array, origin-snapped to the grid.
    nodata : float | None
        Sentinel marking invalid fine pixels.
    grid : GridSpec
        Target grid.
    stat : {"std", "mean", "sum", "min", "max"}
        Statistic to compute per target cell over valid pixels.

    Returns
    -------
    value : np.ndarray
        Statistic per cell, shape (grid.rows, grid.cols); NaN where no valid
        pixels.
    coverage_purity : np.ndarray
        valid_pixels / total_pixels per cell.
    """
    fr, fc = fine.shape
    tr, tc = grid.rows, grid.cols

    value = np.full((tr, tc), np.nan, dtype=np.float64)
    coverage = np.zeros((tr, tc), dtype=np.float64)

    row_windows = _windows(fr, tr)
    col_windows = _windows(fc, tc)

    for ti, (r0, r1) in enumerate(row_windows):
        for tj, (c0, c1) in enumerate(col_windows):
            block = fine[r0:r1, c0:c1].astype(np.float64)
            total = block.size
            if total == 0:
                continue
            if nodata is not None and not np.isnan(nodata):
                mask = (block != nodata) & ~np.isnan(block)
            else:
                mask = ~np.isnan(block)
            n_valid = int(mask.sum())
            coverage[ti, tj] = n_valid / total
            if n_valid == 0:
                continue
            vals = block[mask]
            if stat == "std":
                value[ti, tj] = float(np.std(vals))
            elif stat == "mean":
                value[ti, tj] = float(np.mean(vals))
            elif stat == "sum":
                value[ti, tj] = float(np.sum(vals))
            elif stat == "min":
                value[ti, tj] = float(np.min(vals))
            elif stat == "max":
                value[ti, tj] = float(np.max(vals))

    return value, coverage


# ---------------------------------------------------------------------------
# Raster-only operators
# ---------------------------------------------------------------------------

class MeanOperator(Operator):
    name = "mean"
    _resampling = Resampling.average

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        raise TypeError(f"'mean' requires a raster source, got {type(data).__name__}")


class SumOperator(Operator):
    name = "sum"
    _resampling = Resampling.sum

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        raise TypeError(f"'sum' requires a raster source, got {type(data).__name__}")


class StdOperator(Operator):
    name = "std"
    # Standard deviation has no rasterio resampling equivalent; it requires a
    # real per-cell window pass, so this operator uses fine alignment.
    _resampling = Resampling.nearest
    needs_fine_alignment = True

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            fine, nodata = _fine_array(data)
            value, cov = _continuous_reduce(fine, nodata, grid, "std")
            da = xr.DataArray(value, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})
            return da.assign_coords(coverage_purity=(("y", "x"), cov))
        raise TypeError(f"'std' requires a raster source, got {type(data).__name__}")


class MinOperator(Operator):
    name = "min"
    _resampling = Resampling.min

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        raise TypeError(f"'min' requires a raster source, got {type(data).__name__}")


class MaxOperator(Operator):
    name = "max"
    _resampling = Resampling.max

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        raise TypeError(f"'max' requires a raster source, got {type(data).__name__}")


# ---------------------------------------------------------------------------
# Raster + vector operators
# ---------------------------------------------------------------------------

class MajorityOperator(Operator):
    name = "majority"
    _resampling = Resampling.nearest
    needs_fine_alignment = True

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            fine, nodata = _fine_array(data)
            value, cov, dom = _categorical_reduce(fine, nodata, grid, "majority", None)
            return _attach_purity(value, cov, dom, grid)
        if isinstance(data, gpd.GeoDataFrame):
            val = var.class_code if var.class_code is not None else 1
            shapes = [(g, val) for g in data.geometry if g is not None]
            return _wrap(_rasterize(shapes, grid), grid)
        raise TypeError(f"'majority' got unexpected type {type(data).__name__}")


class MinorityOperator(Operator):
    name = "minority"
    _resampling = Resampling.nearest
    needs_fine_alignment = True

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            fine, nodata = _fine_array(data)
            value, cov, dom = _categorical_reduce(fine, nodata, grid, "minority", None)
            return _attach_purity(value, cov, dom, grid)
        if isinstance(data, gpd.GeoDataFrame):
            val = var.class_code if var.class_code is not None else 1
            shapes = [(g, val) for g in data.geometry if g is not None]
            return _wrap(_rasterize(shapes, grid), grid)
        raise TypeError(f"'minority' got unexpected type {type(data).__name__}")


class PercentageOperator(Operator):
    name = "percentage"
    requires_class_code = True
    _resampling = Resampling.nearest
    needs_fine_alignment = True

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            if var.class_code is None:
                raise ValueError("'percentage' requires class_code to be set.")
            fine, nodata = _fine_array(data)
            value, cov, dom = _categorical_reduce(
                fine, nodata, grid, "percentage", var.class_code
            )
            return _attach_purity(value, cov, dom, grid)
        if isinstance(data, gpd.GeoDataFrame):
            if var.class_code is None:
                raise ValueError("'percentage' requires class_code to be set.")
            shapes = [(g, var.class_code) for g in data.geometry if g is not None]
            return _wrap(_rasterize(shapes, grid), grid)
        raise TypeError(f"'percentage' got unexpected type {type(data).__name__}")


class AttributeOperator(Operator):
    """
    Rasterize a vector layer using a numeric column as the pixel value.

    The column name must match ``Variable.name`` (explicit contract:
    register the source with a column that matches the variable you intend
    to derive).
    """
    name = "attribute"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        if isinstance(data, gpd.GeoDataFrame):
            column = var.name
            if column in data.columns:
                valid = data[data.geometry.notnull() & data[column].notnull()]
                shapes = list(zip(valid.geometry, valid[column]))
            else:
                shapes = []
            return _wrap(_rasterize(shapes, grid), grid)
        raise TypeError(f"'attribute' got unexpected type {type(data).__name__}")


class PresenceOperator(Operator):
    """Binary mask: 1 where any feature is present, 0 elsewhere."""
    name = "presence"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, xr.DataArray):
            return _passthrough(data)
        if isinstance(data, gpd.GeoDataFrame):
            val = var.class_code if var.class_code is not None else 1
            shapes = [(g, val) for g in data.geometry if g is not None]
            return _wrap(_rasterize(shapes, grid), grid)
        raise TypeError(f"'presence' got unexpected type {type(data).__name__}")


# ---------------------------------------------------------------------------
# Legacy compatibility shim — kept so existing imports don't break
# ---------------------------------------------------------------------------

class ZonalAggregator:
    """
    Deprecated.  Use operator classes directly via ``OPERATOR_REGISTRY``.

    Kept as a thin compatibility shim; delegates to the appropriate
    ``Operator.compute()`` for each variable.
    """

    @staticmethod
    def aggregate(data, variables, grid_spec):
        import warnings
        warnings.warn(
            "ZonalAggregator is deprecated; use OPERATOR_REGISTRY operators directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from disscube.operators.base import OPERATOR_REGISTRY
        ds = xr.Dataset(coords={"y": grid_spec.ys, "x": grid_spec.xs})
        for var in variables:
            op_cls = OPERATOR_REGISTRY.get(var.operator)
            if op_cls is None:
                raise ValueError(f"Unknown operator: {var.operator}")
            da = op_cls().compute(data, var, grid_spec)
            ds[var.name] = da
        return ds
