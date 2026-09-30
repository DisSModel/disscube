"""Writers for cube output: multi-band GeoTIFF and CF netCDF.

Both work on plain xarray objects and need no DisSModel. They are normally
reached through :meth:`CubeClient.export_geotiff` and
:meth:`CubeClient.export_netcdf`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

import numpy as np
import xarray as xr

log = logging.getLogger("disscube.export")

#: Above this many bands a GeoTIFF is unwieldy; netCDF keeps the time axis explicit.
MANY_BANDS = 100


def software_tag() -> str:
    try:
        return f"DisSCube {version('disscube')}"
    except PackageNotFoundError:  # not installed (running from a source tree)
        return "DisSCube"


def geotiff_bands(arrays: Mapping[str, xr.DataArray]) -> list[tuple[str, xr.DataArray, int | None]]:
    """Expand variables into bands: ``(band name, 2D array, year or None)``.

    A static variable is one band named after it; a temporal one gives one band per
    year, ordered by year and named ``<variable>_<year>``.
    """
    bands: list[tuple[str, xr.DataArray, int | None]] = []
    for name, da in arrays.items():
        if "time" not in da.dims:
            bands.append((name, da, None))
            continue
        order = np.argsort(da.coords["time"].values)
        for i in order:
            year = int(da.coords["time"].values[i])
            bands.append((f"{name}_{year}", da.isel(time=int(i)), year))
    return bands


def write_geotiff(arrays: Mapping[str, xr.DataArray], output: str | os.PathLike[str], *,
                  crs=None, transform=None) -> Path:
    """Write ``arrays`` (``(y, x)`` or ``(time, y, x)``) to a multi-band GeoTIFF.

    If ``arrays`` has a ``mask`` variable, cells where it is 0 become NaN in every
    other band (and ``mask`` itself becomes 1 inside, NaN outside). Pixels are
    written as float64 with ``nodata=NaN``. Each band carries ``VARIABLE``,
    ``YEAR`` (when temporal) and ``SPEC_HASH`` tags.
    """
    import rasterio

    if not arrays:
        raise ValueError("nothing to export: no variables")
    first = next(iter(arrays.values()))
    height, width = first.sizes["y"], first.sizes["x"]

    if transform is None:
        try:
            transform = first.rio.transform()
        except Exception as exc:  # rio raises several undocumented types for degenerate coords
            raise ValueError("cannot export a GeoTIFF: the grid transform is unknown") from exc
    if crs is None:
        crs = first.rio.crs
    if crs is None:
        raise ValueError("cannot export a GeoTIFF: the CRS is unknown")

    mask_arr = None
    if "mask" in arrays:
        mask_arr = np.asarray(arrays["mask"].values, dtype=np.float64) > 0.0

    bands = geotiff_bands(arrays)
    if len(bands) > MANY_BANDS:
        log.warning("%d bands in one GeoTIFF; export_netcdf() keeps the time axis explicit", len(bands))

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", driver="GTiff", height=height, width=width, count=len(bands),
                       dtype="float64", crs=crs, transform=transform, nodata=np.nan,
                       compress="deflate") as dst:
        dst.update_tags(TIFFTAG_SOFTWARE=software_tag(), CONVENTIONS="CF-1.8",
                        BANDS=",".join(name for name, _, _ in bands))
        grid_ids = {da.attrs["grid_id"] for da in arrays.values() if "grid_id" in da.attrs}
        if len(grid_ids) == 1:
            dst.update_tags(GRID_ID=grid_ids.pop())
        for idx, (name, band, year) in enumerate(bands, start=1):
            var = name if year is None else name[: -len(str(year)) - 1]
            arr = np.asarray(band.values, dtype=np.float64).copy()
            if mask_arr is not None:
                arr = np.where(mask_arr, 1.0, np.nan) if var == "mask" else np.where(mask_arr, arr, np.nan)
            dst.write(arr, idx)
            dst.set_band_description(idx, name)
            tags = {"VARIABLE": var}
            if year is not None:
                tags["YEAR"] = str(year)
            if "spec_hash" in arrays[var].attrs:
                tags["SPEC_HASH"] = str(arrays[var].attrs["spec_hash"])
            dst.update_tags(idx, **tags)
    return out_path


def _netcdf_engine() -> Literal["h5netcdf", "netcdf4"]:
    try:
        import h5netcdf  # noqa: F401
        import h5py  # noqa: F401
        return "h5netcdf"
    except ImportError:
        pass
    try:
        import netCDF4  # noqa: F401
        return "netcdf4"
    except ImportError:
        raise ImportError("export_netcdf() needs a netCDF backend: pip install 'disscube[netcdf]'") from None


def write_netcdf(ds: xr.Dataset, output: str | os.PathLike[str]) -> Path:
    """Write a cube Dataset to a compressed CF-1.8 netCDF file.

    An integer-year ``time`` axis becomes ``datetime64`` (``YYYY-01-01``).
    """
    engine = _netcdf_engine()
    ds = ds.copy()
    if "time" in ds.coords and np.issubdtype(ds["time"].dtype, np.integer):
        years = ds["time"].values.astype("int64")
        ds = ds.assign_coords(time=np.array([f"{y:04d}-01-01" for y in years], dtype="datetime64[ns]"))
        ds["time"].attrs.update(standard_name="time", long_name="time (year of the slice)")
    ds.attrs.update(Conventions="CF-1.8", source=software_tag())

    encoding: dict[str, dict[str, Any]] = {
        str(name): {"zlib": True, "complevel": 4, "_FillValue": np.nan}
        for name, da in ds.data_vars.items() if np.issubdtype(da.dtype, np.floating)}
    if engine == "h5netcdf":  # h5netcdf spells the compression options differently
        encoding = {n: {"compression": "gzip", "compression_opts": 4, "_FillValue": np.nan}
                    for n in encoding}

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path, engine=engine, encoding=encoding)
    return out_path
