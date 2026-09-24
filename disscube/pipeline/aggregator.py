"""
Aggregator — derives each Variable by delegating to its Operator class.

For raster sources ``ctx.data`` arrives as an ``xr.Dataset`` produced by
``GridAligner``, with one correctly-resampled DataArray per variable.
The operator's ``compute()`` receives that DataArray and returns it
(possibly with minor post-processing).

For vector sources ``ctx.data`` is a ``GeoDataFrame`` (clipped, reprojected).
Each operator's ``compute()`` performs the rasterization / distance
transform / count and returns an ``(y, x)`` DataArray.

Adding a new operator never requires touching this file.
"""

from __future__ import annotations

import rioxarray  # noqa: F401 — registers the .rio accessor on Dataset/DataArray
import xarray as xr

from disscube.operators.base import OPERATOR_REGISTRY
from disscube.pipeline import PipelineContext, PipelineStage


def _crs_to_named_wkt(crs_str: str) -> str:
    """
    Convert a CRS string to a WKT with a human-readable name.

    Prevents rioxarray from writing ``PROJCS["unknown"]`` when the CRS has
    no official EPSG code (e.g. BDC Albers).
    """
    from pyproj import CRS

    crs = CRS.from_user_input(crs_str)

    if crs.name and crs.name.lower() != "unknown":
        return crs.to_wkt()

    bdc_albers_proj4 = (
        "+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22 "
        "+x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs"
    )
    if crs.equals(CRS.from_proj4(bdc_albers_proj4)):
        return (
            'PROJCS["Brazil_Albers_Equal_Area_Conic",'
            'GEOGCS["GCS_SIRGAS_2000",'
            'DATUM["SIRGAS_2000",'
            'SPHEROID["GRS_1980",6378137,298.257222101]],'
            'PRIMEM["Greenwich",0],'
            'UNIT["Degree",0.017453292519943295]],'
            'PROJECTION["Albers_Conic_Equal_Area"],'
            'PARAMETER["False_Easting",5000000],'
            'PARAMETER["False_Northing",10000000],'
            'PARAMETER["longitude_of_center",-54],'
            'PARAMETER["Standard_Parallel_1",-2],'
            'PARAMETER["Standard_Parallel_2",-22],'
            'PARAMETER["latitude_of_center",-12],'
            'UNIT["Meter",1]]'
        )

    return crs.to_wkt()


def _on_grid_coords(result: xr.DataArray, final_ds: xr.Dataset, name: str) -> xr.DataArray:
    """
    Put ``result`` on the grid's own x/y labels, by position.

    Operators return arrays on the target grid, but their coordinates may come
    from rioxarray and differ from ``GridSpec.xs``/``ys`` by floating-point
    noise (e.g. 0.0045° cells in a geographic CRS). Assigning such an array
    into the grid-indexed Dataset would align by label and silently turn the
    mismatching rows or columns into NaN, so the labels are replaced here —
    after checking that the shape is the grid's.
    """
    expected = (final_ds.sizes["y"], final_ds.sizes["x"])
    actual = (result.sizes.get("y"), result.sizes.get("x"))
    if actual != expected:
        raise ValueError(
            f"Aggregator: operator result for {name!r} has shape {actual}, expected the grid's {expected}"
        )
    return result.assign_coords(y=final_ds["y"].values, x=final_ds["x"].values)


class Aggregator(PipelineStage):
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        grid = ctx.grid
        source_data = ctx.data

        final_ds = xr.Dataset(coords={"y": grid.ys, "x": grid.xs})

        for var in ctx.derivation.variables:
            op_cls = OPERATOR_REGISTRY.get(var.operator)
            if op_cls is None:
                available = sorted(OPERATOR_REGISTRY)
                raise ValueError(
                    f"Unknown operator {var.operator!r}. "
                    f"Available: {available}"
                )

            # For raster sources GridAligner returns a dict keyed by variable
            # name (DataArrays may be at target-grid resolution for continuous
            # operators, or at a fine resolution for categorical ones). For
            # vector sources it returns a single GeoDataFrame.
            if isinstance(source_data, (dict, xr.Dataset)):
                var_data = source_data[var.name]
            else:
                var_data = source_data

            result: xr.DataArray = op_cls().compute(var_data, var, grid)
            result = _on_grid_coords(result, final_ds, var.name)

            # Purity metrics (coverage_purity / dominance_purity) produced by
            # categorical operators are kept as COORDINATES on the variable's
            # DataArray — not as separate data variables — so that the
            # VariableWriter persists them inside the variable's own Zarr and
            # does NOT register them as standalone DerivedVariables in the
            # catalog. They travel with the variable and never pollute the
            # product catalog.
            final_ds[var.name] = result

        # Write CRS and transform AFTER all variables are present.
        # rioxarray.write_crs() sets grid_mapping="spatial_ref" on every existing
        # data variable — calling it before the loop would miss them all, causing
        # QGIS and other CF-compliant readers to not detect the projection.
        named_wkt = _crs_to_named_wkt(grid.crs)
        final_ds.rio.write_crs(named_wkt, inplace=True)
        final_ds.rio.write_transform(grid.transform, inplace=True)

        ctx.data = final_ds
        return ctx