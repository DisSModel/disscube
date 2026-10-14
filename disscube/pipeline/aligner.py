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
from typing import Any

import geopandas as gpd
import numpy as np
import rioxarray  # registers the .rio accessor
import shapely
import xarray as xr
from pyproj import CRS as ProjCRS
from pyproj.exceptions import CRSError
from rasterio.warp import Resampling
from shapely.geometry import box

from disscube.models.grid import GridSpec
from disscube.models.variable import Variable
from disscube.operators.base import OPERATOR_REGISTRY
from disscube.pipeline import PipelineContext, PipelineStage

log = logging.getLogger(__name__)

# Integer dtype -> next wider dtype, used to give sources without a declared
# nodata a fill value they cannot contain (the maximum of the wider dtype).
_WIDER_INT: dict[np.dtype[Any], np.dtype[np.integer[Any]]] = {
    np.dtype("uint8"): np.dtype("uint16"),
    np.dtype("int8"): np.dtype("int16"),
    np.dtype("uint16"): np.dtype("uint32"),
    np.dtype("int16"): np.dtype("int32"),
    np.dtype("uint32"): np.dtype("uint64"),
    np.dtype("int32"): np.dtype("int64"),
}


def _source_nodata(band: xr.DataArray) -> float | None:
    try:
        return band.rio.nodata
    except Exception:  # noqa: BLE001 — defensive fallback: the .rio accessor raises undocumented types (e.g. ValueError, CRSError)
        return None


def _as_float_with_nan_nodata(band: xr.DataArray) -> xr.DataArray:
    """
    Continuous path: cast to float64 and represent nodata as NaN.

    Reprojecting in the source dtype is unsafe for two reasons: GDAL computes
    ``sum`` (and ``average``) in that dtype, so integer sums saturate (e.g. at
    255 for uint8); and when the source declares no nodata, the destination
    fill value defaults to the dtype's own sentinel (255 for uint8), which
    collides with real data — GDAL then nudges valid 255 values to 254.
    """
    nodata = _source_nodata(band)
    crs = band.rio.crs
    out = band.astype("float64")
    if nodata is not None and not np.isnan(nodata):
        out = out.where(band != nodata)
    out = out.rio.write_crs(crs).rio.write_nodata(np.nan, encoded=False)
    return out


def _with_non_colliding_nodata(band: xr.DataArray) -> xr.DataArray:
    """
    Categorical path: make sure the reprojection fill value cannot be a class.

    When the source declares a nodata value it is kept (it is meant to be
    excluded). When it declares none, reprojection would fill the area outside
    the source with the dtype default (255 for uint8), and that value would be
    treated as nodata — silently dropping a legitimate class 255. Integer
    sources are therefore widened to the next dtype and filled with its
    maximum, which the original data cannot contain; class codes stay exact.
    """
    if _source_nodata(band) is not None:
        return band
    crs = band.rio.crs
    if np.issubdtype(band.dtype, np.floating):
        return band.rio.write_nodata(np.nan, encoded=False)
    wider = _WIDER_INT.get(band.dtype)
    if wider is None:  # 64-bit integers: fall back to float64 with NaN
        return _as_float_with_nan_nodata(band)
    out = band.astype(wider).rio.write_crs(crs)
    return out.rio.write_nodata(np.iinfo(wider).max, encoded=False)


def _clip_to_grid(band: xr.DataArray, grid: GridSpec, pad: int = 2) -> xr.DataArray:
    """
    Keep only the pixels of ``band`` that can reach the cells of ``grid``.

    The band is opened lazily, so slicing it reads just that window: a 9.7-billion-pixel raster
    aligned to a small grid no longer has to be loaded whole. ``pad`` extra source pixels on each side
    cover the pixels that only partly overlap the edge cells, so the aligned values are the same as
    from the whole band. A grid with no overlap with the band gets a one-pixel window at the nearest
    edge, which aligns to nodata. Bands that are not north-up are returned as they are.
    """
    try:
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        crs, tr = band.rio.crs, band.rio.transform()
        ny, nx = band.sizes["y"], band.sizes["x"]
        if crs is None or tr.b != 0 or tr.d != 0 or tr.e >= 0 or tr.a <= 0:
            return band
        left, bottom, right, top = transform_bounds(grid.crs, crs, *grid.bbox, densify_pts=21)
        w = from_bounds(left, bottom, right, top, transform=tr)
        c0, c1 = int(np.floor(w.col_off)) - pad, int(np.ceil(w.col_off + w.width)) + pad
        r0, r1 = int(np.floor(w.row_off)) - pad, int(np.ceil(w.row_off + w.height)) + pad
        c0, c1, r0, r1 = max(c0, 0), min(c1, nx), max(r0, 0), min(r1, ny)
        if c1 <= c0:
            c0 = min(max(c0, 0), nx - 1); c1 = c0 + 1
        if r1 <= r0:
            r0 = min(max(r0, 0), ny - 1); r1 = r0 + 1
        if (c0, c1, r0, r1) == (0, nx, 0, ny):
            return band
        return band.isel(y=slice(r0, r1), x=slice(c0, c1))
    except Exception:
        log.debug("could not clip the source to the grid; using the whole band", exc_info=True)
        return band



def _repair_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Repair the invalid geometries of a vector source (self-intersections, often created by the reprojection).

    ``clip`` raises ``TopologyException`` on an invalid polygon, which stopped a pipeline for one bad feature in a layer
    of thousands. Only the invalid features are changed; a polygon that becomes a collection keeps its polygonal parts.
    """
    geoms = gdf.geometry
    invalid = ~geoms.is_valid & geoms.notna()
    if not invalid.any():
        return gdf
    fixed = shapely.make_valid(geoms[invalid].values)
    keep_polygons = geoms[invalid].geom_type.isin(["Polygon", "MultiPolygon"]).values
    out = []
    for geom, polygonal in zip(fixed, keep_polygons):
        if polygonal and geom.geom_type not in ("Polygon", "MultiPolygon"):
            parts = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")]
            geom = shapely.union_all(parts) if parts else shapely.Polygon()
        out.append(geom)
    gdf = gdf.copy()
    gdf.loc[invalid.values, gdf.geometry.name] = gpd.GeoSeries(out, index=gdf.index[invalid.values], crs=gdf.crs)
    log.warning("repaired %d invalid geometries before clipping", int(invalid.sum()))
    return gdf


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
                nodata=ctx.source.nodata,
            )
        elif fmt == "vector":
            gdf: gpd.GeoDataFrame = ctx.data
            try:
                needs_reproject = not ProjCRS.from_user_input(gdf.crs).equals(
                    ProjCRS.from_user_input(grid.crs)
                )
            except CRSError:
                needs_reproject = str(gdf.crs) != str(grid.crs)
            if needs_reproject:
                gdf = gdf.to_crs(grid.crs)
            gdf = _repair_geometries(gdf)
            ops = [OPERATOR_REGISTRY.get(v.operator) for v in ctx.derivation.variables]
            if all(getattr(op, "clip_to_grid", True) for op in ops):
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
        nodata: float | None = None,
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
        nodata : float | None
            The source's no-data value, when the file does not declare it
            (``SpatialSource.nodata``); it overrides the file's own.
        """
        # decode_times=False: a NetCDF variable ("NETCDF:file:var") carries its
        # file's time metadata, and units such as "years since 2000-1-1" do not
        # decode; the time of a source is set when it is registered.
        opened = rioxarray.open_rasterio(url, decode_times=False)
        # Close the file when done: the aligned arrays are in memory, and a handle left open is
        # finalised at interpreter exit, which printed `Error in sys.excepthook` after every run.
        try:
            if not isinstance(opened, xr.DataArray):
                raise TypeError(
                    f"{url!r} opened as {type(opened).__name__}, not a single raster; "
                    "a multi-subdataset file needs an explicit variable (e.g. NETCDF:file:var)"
                )
            ds_src = opened
            # GDAL names the band axis of a NetCDF variable after its own
            # dimension (e.g. "time"); every band of a raster is a "band" here.
            extra = [d for d in ds_src.dims if d not in ("y", "x")]
            if len(extra) == 1 and extra[0] != "band":
                ds_src = ds_src.rename({extra[0]: "band"})
                ds_src = ds_src.assign_coords(band=np.arange(1, ds_src.sizes["band"] + 1))
            if nodata is not None:
                ds_src = ds_src.rio.write_nodata(nodata, encoded=False)
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

                whole_band = band
                band = _clip_to_grid(band, grid)

                # ── Per-operator resampling method ─────────────────────────
                op_cls = OPERATOR_REGISTRY.get(var.operator)
                needs_fine = bool(getattr(op_cls, "needs_fine_alignment", False))

                if needs_fine:
                    # Categorical operators must see sub-cell class composition.
                    # Reproject with NEAREST (never average a class code) onto a
                    # fine grid that shares the target grid origin, at a resolution
                    # that is an integer sub-multiple of the target cell size.
                    # The fine resolution is chosen from the whole source, not from the clipped window:
                    # the resolution GDAL proposes depends on the extent, and every strip of a grid must
                    # use the one the whole grid would.
                    aligned = self._align_fine(band, grid, var.params.get("subcells"),
                                               src_res=self._source_resolution(whole_band, grid))
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
                # Continuous operators work in float64 with NaN as nodata (see
                # _as_float_with_nan_nodata for why the source dtype is unsafe).
                band = _as_float_with_nan_nodata(band)
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
        finally:
            # open_rasterio returns a DataArray, a Dataset or a list of Datasets (multi-subdataset files)
            for handle in opened if isinstance(opened, list) else [opened]:
                handle.close()

    # ------------------------------------------------------------------
    # Fine alignment for categorical operators
    # ------------------------------------------------------------------

    @staticmethod
    def _source_resolution(band: xr.DataArray, grid: GridSpec) -> float:
        """The source resolution in the grid CRS: the one GDAL would choose to reproject ``band``."""
        from rasterio.warp import calculate_default_transform

        try:
            rows, cols = band.rio.shape
            src_transform, _, _ = calculate_default_transform(
                band.rio.crs, grid.crs, cols, rows, *band.rio.bounds()
            )
            return abs(float(src_transform.a))
        except Exception:  # noqa: BLE001 — defensive fallback: the .rio accessor raises undocumented types (e.g. ValueError, CRSError)
            return grid.resolution

    def _align_fine(
        self, band: xr.DataArray, grid: GridSpec, max_subcells: int | None = None,
        src_res: float | None = None,
    ) -> xr.DataArray:
        """
        Reproject ``band`` onto a fine grid snapped to the target grid origin.

        The fine resolution is the largest integer sub-multiple of the target
        cell size that is not coarser than the source resolution, so each
        target cell maps onto a whole number of fine pixels along each axis.
        Resampling is NEAREST to preserve class codes. The source nodata is
        carried on the result as ``_disscube_nodata`` for the operator; a
        source without one gets a fill value that cannot collide with a class
        (see _with_non_colliding_nodata).

        ``max_subcells`` (the operator's ``subcells`` param) caps the number of
        fine pixels per target cell along each axis. A 100 m source on a
        ~9 km grid would otherwise be sampled at ~90 × 90 pixels per cell —
        billions of pixels over a country; at 20 × 20 each fine pixel is the
        source pixel nearest to its centre.

        Parameters
        ----------
        band : xr.DataArray
            Single-band source (already band-selected).
        grid : GridSpec
            Target grid.
        max_subcells : int | None
            Upper bound on the fine pixels per cell along each axis.

        Returns
        -------
        xr.DataArray
            Fine, origin-snapped array (dims "y","x"), with nodata recorded
            in ``attrs["_disscube_nodata"]``.
        """
        from affine import Affine

        band = _with_non_colliding_nodata(band)
        if src_res is None:
            src_res = self._source_resolution(band, grid)

        target_res = grid.resolution
        if src_res <= 0 or src_res >= target_res:
            # Source no finer than target: one fine pixel per target cell.
            factor = 1
        else:
            # Largest integer factor whose fine res (target/factor) is >= src_res.
            factor = max(1, int(np.floor(target_res / src_res)))
        if max_subcells is not None:
            factor = max(1, min(factor, int(max_subcells)))

        fine_res = target_res / factor
        fine_rows = grid.rows * factor
        fine_cols = grid.cols * factor

        # Fine transform shares the target grid origin (north-up).
        fine_transform = (
            Affine.translation(grid.bbox[0], grid.bbox[3])
            @ Affine.scale(fine_res, -fine_res)
        )

        nodata = _source_nodata(band)

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
