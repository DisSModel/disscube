"""
Value-remapping operators — per-pixel lookup, no spatial aggregation.

Unlike the zonal operators, nothing here combines several source pixels into
one target cell: each pixel's new value depends only on its own old value,
via an explicit lookup table carried on ``Variable.mapping``.

This is deliberately domain-agnostic.  The *mechanism* (apply a table) lives
here; the *table* (which source code means what) is data supplied by the
caller, so a land-cover reclassification, a soil-class grouping and a
land-tenure regrouping all use this same operator with different tables and
no changes to disscube.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
from rasterio.warp import Resampling

from disscube.operators.base import Operator
from disscube.models.variable import Variable
from disscube.models.grid import GridSpec


def _lookup(arr: np.ndarray, mapping: dict[int, int], nodata: float | None) -> np.ndarray:
    """
    Apply ``mapping`` to ``arr``, returning float64 with NaN where a value is
    nodata or absent from the table.

    Vectorized via ``np.searchsorted`` over the sorted key array rather than a
    dense 0..max lookup array, so a sparse table with large codes (e.g.
    MapBiomas-style codes in the hundreds) costs the same as a dense one.
    """
    keys = np.array(sorted(mapping), dtype=np.int64)
    vals = np.array([mapping[int(k)] for k in keys], dtype=np.float64)

    finite = np.isfinite(arr)
    valid = finite.copy()
    if nodata is not None and np.isfinite(nodata):
        valid &= arr != nodata

    # Round to integer codes only where the value is usable; searchsorted needs
    # a clean integer array, and non-finite entries would poison the cast.
    codes = np.where(valid, arr, 0).astype(np.int64)

    idx = np.searchsorted(keys, codes)
    np.clip(idx, 0, len(keys) - 1, out=idx)
    found = valid & (keys[idx] == codes)

    return np.where(found, vals[idx], np.nan)


class ReclassifyOperator(Operator):
    """
    Remap source values to new values through an explicit lookup table.

    ``Variable.mapping`` supplies ``{source_value: target_value}``.  Any pixel
    whose value is source-nodata, non-finite, or simply absent from the table
    becomes NaN — the same "no valid result here" convention the zonal
    operators use, so downstream readers need no special case.  Callers that
    need a specific sentinel (255, -9999, …) convert NaN on write.

    Resampling is NEAREST: a class code must never be averaged.  Note that
    when the target grid is coarser than the source this samples one source
    pixel per target cell rather than taking a majority — reclassify is a
    per-pixel remap, not an aggregation.  To reclassify *and* downsample,
    derive the reclassified variable on a grid at source resolution, then
    derive ``majority`` from it.

    No purity coordinates are attached (unlike the categorical zonal
    operators): with one source pixel per target cell there is no sub-cell
    composition to summarise, so coverage/dominance would be a constant 1.0
    carrying no information, at the cost of two extra full-size arrays.
    """

    name = "reclassify"
    _resampling = Resampling.nearest
    requires_mapping = True

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if not isinstance(data, xr.DataArray):
            raise TypeError(
                f"'reclassify' requires a raster source, got {type(data).__name__}"
            )
        if not var.mapping:
            raise ValueError(
                f"Operator 'reclassify' requires a non-empty mapping for "
                f"variable {var.name!r}."
            )

        da = data.isel(band=0) if "band" in data.dims else data
        da = da.transpose("y", "x")

        nodata = da.attrs.get("_disscube_nodata", None)
        if nodata is None:
            try:
                nodata = da.rio.nodata
            except Exception:
                nodata = None

        arr = np.asarray(da.values, dtype=np.float64)
        out = _lookup(arr, var.mapping, nodata)

        return xr.DataArray(
            out, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs}
        )
