"""
GridAligner — reprojects source data to the target GridSpec.

For raster sources each variable is aligned independently using the
resampling method declared by its Operator class, so ``majority`` uses
``Resampling.mode`` while ``mean`` uses ``Resampling.average`` — even when
both are derived from the same multi-band file.

The output is an ``xr.Dataset`` keyed by variable name; the Aggregator
picks each DataArray and delegates to the operator's ``compute()`` method.

For vector sources the GeoDataFrame is reprojected and clipped to the grid
bounding box; the Aggregator then calls each operator's ``compute()`` to
rasterize.

Invariant enforced at the end of raster alignment:
    aligned.rio.shape == (grid.rows, grid.cols)

A mismatch raises ``ValueError`` immediately so misalignments surface as
loud errors rather than silent downstream corruption.
"""

from __future__ import annotations

import logging

import rioxarray  # noqa: F401 — registers the .rio accessor
import numpy as np
import xarray as xr
import geopandas as gpd
from pyproj import CRS as ProjCRS, Transformer
from rasterio.warp import Resampling
from shapely.geometry import box

from disscube.operators.base import OPERATOR_REGISTRY
from disscube.pipeline import PipelineStage, PipelineContext
from disscube.models.grid import GridSpec
from disscube.models.variable import Variable

log = logging.getLogger(__name__)


class GridAligner(PipelineStage):
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        grid = ctx.grid
        fmt = ctx.source.format

        if fmt == "raster":
            ctx.data = self._align_raster(
                ctx.source.asset_url,
                grid,
                ctx.derivation.variables,
                ctx.source.band_map,
            )
        elif fmt == "vector":
            gdf: gpd.GeoDataFrame = ctx.data
            try:
                needs_reproject = not ProjCRS.from_user_input(gdf.crs).equals(
                    ProjCRS.from_user_input(grid.crs)
                )
            except Exception:
                needs_reproject = str(gdf.crs) != str(grid.crs)
            if needs_reproject:
                gdf = gdf.to_crs(grid.crs)
            gdf = gdf.clip(box(*grid.bbox))
            ctx.data = gdf

        return ctx

    # ------------------------------------------------------------------
    # Raster alignment — one aligned DataArray per derived variable
    # ------------------------------------------------------------------

    def _align_raster(
        self,
        url: str,
        grid: GridSpec,
        variables: list[Variable],
        band_map: dict[str, int],
    ) -> dict[str, xr.DataArray]:
        """
        Reproject and resample the source raster for each variable.

        Returns an ``xr.Dataset`` with one data variable per ``Variable``,
        each resampled with the method appropriate for its operator
        (e.g. ``Resampling.mode`` for ``majority``,
        ``Resampling.average`` for ``mean``).

        Parameters
        ----------
        url : str
            Path or URL to the source raster.
        grid : GridSpec
            Target spatial grid.
        variables : list[Variable]
            Variables to derive; determines band selection and resampling.
        band_map : dict[str, int]
            Optional ``{variable_name: 1-based band index}`` from the source.
        """
        ds_src = rioxarray.open_rasterio(url)
        # Map of variable name -> aligned DataArray. A plain dict (not a
        # Dataset) is used because fine-aligned categorical arrays have a
        # different shape than the target grid; putting them in a Dataset
        # keyed on grid coords would trigger coordinate realignment to NaN.
        result: dict[str, xr.DataArray] = {}

        for i, var in enumerate(variables):
            # ── Band selection ─────────────────────────────────────────
            if "band" in ds_src.dims and ds_src.sizes["band"] > 1:
                if band_map and var.name in band_map:
                    band_idx = band_map[var.name] - 1  # 1-based → 0-based
                    if not (0 <= band_idx < ds_src.sizes["band"]):
                        raise ValueError(
                            f"Band index {band_idx + 1} for variable "
                            f"'{var.name}' is out of range; "
                            f"source has {ds_src.sizes['band']} bands."
                        )
                elif i < ds_src.sizes["band"]:
                    band_idx = i
                else:
                    raise ValueError(
                        f"No band available for variable '{var.name}' at "
                        f"index {i}; source has {ds_src.sizes['band']} bands "
                        "and no band_map was provided."
                    )
                band = ds_src.isel(band=band_idx)
            else:
                band = ds_src.isel(band=0) if "band" in ds_src.dims else ds_src

            # ── Crop to target grid before any reprojection ─────────────
            # Without this, .rio.reproject() (and _align_fine's internal
            # resolution-estimation reproject) forces rioxarray to read
            # the ENTIRE source raster via `self._obj.values`, regardless
            # of how small `grid` actually is. For a real multi-tile
            # mosaic this reads/reprojects the full extent per variable —
            # confirmed to OOM-kill (whole-mosaic case) or take ~30 min
            # per variable (a grid window 48x smaller than the mosaic)
            # against real MapBiomas/ANADEM data. Cropping first turns
            # this into a windowed read.
            band = self._crop_to_grid(band, grid)

            # ── Per-operator resampling method ─────────────────────────
            op_cls = OPERATOR_REGISTRY.get(var.operator)
            needs_fine = bool(getattr(op_cls, "needs_fine_alignment", False))

            if needs_fine:
                # Categorical operators must see sub-cell class composition.
                # Reproject with NEAREST (never average a class code) onto a
                # fine grid that shares the target grid origin, at a resolution
                # that is an integer sub-multiple of the target cell size.
                aligned = self._align_fine(band, grid)
                result[var.name] = aligned
                log.debug(
                    "fine-aligned '%s' via '%s' (nearest, fine shape=%s -> target=%s)",
                    var.name, var.operator, aligned.rio.shape, (grid.rows, grid.cols),
                )
                continue

            resampling: Resampling = (
                op_cls.resampling() if op_cls else Resampling.nearest
            )

            # ── Reproject to target grid ───────────────────────────────
            aligned = band.rio.reproject(
                grid.crs,
                shape=(grid.rows, grid.cols),
                transform=grid.transform,
                resampling=resampling,
            )

            # ── Alignment invariant ────────────────────────────────────
            actual = aligned.rio.shape
            expected = (grid.rows, grid.cols)
            if actual != expected:
                raise ValueError(
                    f"GridAligner: variable '{var.name}' alignment produced "
                    f"shape {actual}, expected {expected} for grid '{grid.id}'. "
                    f"Source: {url}"
                )

            result[var.name] = aligned.transpose("y", "x")
            log.debug(
                "aligned '%s' via '%s' (resampling=%s, shape=%s)",
                var.name, var.operator, resampling.name, actual,
            )

        return result

    # ------------------------------------------------------------------
    # Crop source to target grid extent (avoids reading/reprojecting the
    # whole source raster for a small target grid)
    # ------------------------------------------------------------------

    def _crop_to_grid(self, band: xr.DataArray, grid: GridSpec, buffer_px: int = 4) -> xr.DataArray:
        """
        Crop ``band`` (still in its native CRS) to the region overlapping
        ``grid``'s bbox, with a small buffer for resampling kernels, before
        any reprojection touches it.

        ``grid.bbox`` is transformed into the source's native CRS first
        (when it differs from ``grid.crs``) so the crop is correct even
        when the source raster is not already in the target CRS.

        Falls back to the unclipped ``band`` (same behaviour as before this
        fix) if the CRS is unknown or the crop fails for any reason — e.g.
        the source doesn't actually overlap the grid, which the existing
        downstream shape/coverage checks already surface clearly.
        """
        try:
            src_crs = band.rio.crs
        except Exception:
            src_crs = None

        minx, miny, maxx, maxy = grid.bbox

        if src_crs is not None:
            try:
                target_crs = ProjCRS.from_user_input(grid.crs)
                source_crs = ProjCRS.from_user_input(src_crs)
                if not source_crs.equals(target_crs):
                    transformer = Transformer.from_crs(target_crs, source_crs, always_xy=True)
                    xs, ys = transformer.transform([minx, minx, maxx, maxx], [miny, maxy, miny, maxy])
                    minx, maxx = min(xs), max(xs)
                    miny, maxy = min(ys), max(ys)
            except Exception:
                log.debug("crop-to-grid: CRS check/transform failed, skipping crop", exc_info=True)
                return band

        buffer = grid.resolution * buffer_px
        minx, miny, maxx, maxy = minx - buffer, miny - buffer, maxx + buffer, maxy + buffer

        try:
            return band.rio.clip_box(minx, miny, maxx, maxy, auto_expand=True)
        except Exception:
            log.debug(
                "crop-to-grid: clip_box failed (grid may not overlap source), "
                "falling back to unclipped read", exc_info=True,
            )
            return band

    # ------------------------------------------------------------------
    # Fine alignment for categorical operators
    # ------------------------------------------------------------------

    def _align_fine(self, band: xr.DataArray, grid: GridSpec) -> xr.DataArray:
        """
        Reproject ``band`` onto a fine grid snapped to the target grid origin.

        The fine resolution is the largest integer sub-multiple of the target
        cell size that is not coarser than the source resolution, so each
        target cell maps onto a whole number of fine pixels along each axis.
        Resampling is NEAREST to preserve class codes. The source nodata is
        carried on the result as ``_disscube_nodata`` for the operator.

        Parameters
        ----------
        band : xr.DataArray
            Single-band source (already band-selected).
        grid : GridSpec
            Target grid.

        Returns
        -------
        xr.DataArray
            Fine, origin-snapped array (dims "y","x"), with nodata recorded
            in ``attrs["_disscube_nodata"]``.
        """
        from affine import Affine

        # Estimate source resolution in target CRS units by reprojecting first
        # to the target CRS at native resolution, then deriving a sub-multiple.
        src = band.rio.reproject(grid.crs, resampling=Resampling.nearest)
        try:
            src_res = abs(float(src.rio.resolution()[0]))
        except Exception:
            src_res = grid.resolution

        target_res = grid.resolution
        if src_res <= 0 or src_res >= target_res:
            # Source no finer than target: one fine pixel per target cell.
            factor = 1
        else:
            # Largest integer factor whose fine res (target/factor) is >= src_res.
            factor = max(1, int(np.floor(target_res / src_res)))

        fine_res = target_res / factor
        fine_rows = grid.rows * factor
        fine_cols = grid.cols * factor

        # Fine transform shares the target grid origin (north-up).
        fine_transform = (
            Affine.translation(grid.bbox[0], grid.bbox[3])
            * Affine.scale(fine_res, -fine_res)
        )

        nodata = None
        try:
            nodata = band.rio.nodata
        except Exception:
            nodata = None

        aligned = band.rio.reproject(
            grid.crs,
            shape=(fine_rows, fine_cols),
            transform=fine_transform,
            resampling=Resampling.nearest,
        )
        aligned = aligned.transpose("y", "x") if "band" not in aligned.dims else aligned.isel(band=0).transpose("y", "x")
        if nodata is not None:
            aligned.attrs["_disscube_nodata"] = nodata
        return aligned
