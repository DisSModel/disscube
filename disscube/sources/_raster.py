"""
Shared raster machinery for the data-source adapters in :mod:`disscube.sources`.

Every adapter follows the same contract: read only the pixels of an area of
interest (windowed reads, also over HTTP for Cloud-Optimized GeoTIFFs), turn
them into a :class:`Window2D` in physical units, and register the result with
:func:`register_raster` — a local GeoTIFF, its SHA-256 as the source
``checksum`` (which keys the derivation cache) and a ``<source>.provenance.json``
sidecar saying where the data came from.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from disscube.utils.files import sha256_file
from disscube.utils.grids import BDC_CRS

log = logging.getLogger(__name__)

#: EPSG code the BDC cubes declare for BDC Albers (registered in 2023).
BDC_ALBERS_EPSG = 10857


@dataclass
class Window2D:
    """Pixels read from one raster, in physical units (NaN where nodata)."""

    data: np.ndarray        # float32, (rows, cols); NaN where nodata
    transform: Affine
    crs: CRS


# ---------------------------------------------------------------------------
# CRS and windowed reads
# ---------------------------------------------------------------------------

def is_bdc_albers(crs: CRS | None) -> bool:
    """True when ``crs`` is BDC Albers, whether declared by EPSG code or by parameters."""
    if crs is None:
        return False
    try:
        if crs.to_epsg() == BDC_ALBERS_EPSG:
            return True
    except Exception:  # noqa: BLE001, S110 — to_epsg raises CRSError on unknown codes; fall through to the parameter check
        pass
    if f'"EPSG",{BDC_ALBERS_EPSG}' in crs.to_wkt().replace(" ", ""):
        return True
    try:
        return crs == CRS.from_string(BDC_CRS)
    except Exception:  # noqa: BLE001 — comparison failures mean "not the same CRS"
        return False


def portable_crs(crs: CRS) -> CRS:
    """Replace a BDC Albers CRS by its proj4 definition, so any PROJ version can read it."""
    return CRS.from_string(BDC_CRS) if is_bdc_albers(crs) else crs


def read_window(
    href: str,
    bbox_geo: Sequence[float],
    *,
    scale: float | None = None,
    offset: float | None = None,
) -> Window2D:
    """
    Read the pixels of ``href`` that cover ``bbox_geo`` (WGS84).

    Only that window is fetched, so remote Cloud-Optimized GeoTIFFs are read
    with a few HTTP range requests. ``scale``/``offset`` override the values
    stored in the file; when neither is available the raw values are kept.
    """
    with rasterio.open(href) as ds:
        bounds = transform_bounds("EPSG:4326", ds.crs, *bbox_geo, densify_pts=21)
        win = from_bounds(*bounds, transform=ds.transform)
        win = win.round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, ds.width, ds.height))
        raw = ds.read(1, window=win)
        transform = ds.window_transform(win)
        nodata = ds.nodata
        file_scale = ds.scales[0] if ds.scales else 1.0
        file_offset = ds.offsets[0] if ds.offsets else 0.0
        crs = ds.crs

    data = raw.astype("float32")
    if nodata is not None and not np.isnan(nodata):
        data[raw == nodata] = np.nan
    s = scale if scale is not None else file_scale
    o = offset if offset is not None else file_offset
    if s != 1.0 or o != 0.0:
        data = data * np.float32(s) + np.float32(o)
    return Window2D(data=data, transform=transform, crs=crs)


def composite(windows: Iterable[Window2D], reducer: str = "median") -> Window2D:
    """
    Reduce windows of the same grid to one layer, ignoring NaN.

    ``reducer`` is ``"median"``, ``"mean"``, ``"max"`` or ``"min"``. All windows
    must share shape, transform and CRS (true for items of one tile).
    """
    windows = list(windows)
    if not windows:
        raise ValueError("composite() needs at least one window")
    first = windows[0]
    for w in windows[1:]:
        if w.data.shape != first.data.shape or w.transform != first.transform:
            raise ValueError(
                "windows cover different grids (shape or transform differ); "
                "composite items of a single tile, or mosaic them first"
            )
    funcs: dict[str, Callable] = {"median": np.nanmedian, "mean": np.nanmean,
                                  "max": np.nanmax, "min": np.nanmin}
    if reducer not in funcs:
        raise ValueError(f"unknown reducer {reducer!r}; choose one of {sorted(funcs)}")
    stack = np.stack([w.data for w in windows])
    with np.errstate(all="ignore"), _quiet_all_nan():
        out = funcs[reducer](stack, axis=0).astype("float32")
    return Window2D(data=out, transform=first.transform, crs=first.crs)


def mosaic(windows: Iterable[Window2D]) -> Window2D:
    """
    Merge windows from neighbouring tiles into one layer.

    Tiles of one collection share a pixel mesh (true for the BDC cubes), so
    this is a paste, not a resampling; where tiles overlap the first non-NaN
    value wins.
    """
    from rasterio.io import MemoryFile
    from rasterio.merge import merge

    windows = list(windows)
    if not windows:
        raise ValueError("mosaic() needs at least one window")
    if len(windows) == 1:
        return windows[0]
    files, datasets = [], []
    try:
        for w in windows:
            mem = MemoryFile()
            rows, cols = w.data.shape
            with mem.open(driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
                          crs=portable_crs(w.crs), transform=w.transform, nodata=np.nan) as dst:
                dst.write(w.data, 1)
            files.append(mem)
            datasets.append(mem.open())
        data, transform = merge(datasets, nodata=np.nan)
    finally:
        for ds in datasets:
            ds.close()
        for mem in files:
            mem.close()
    return Window2D(data=data[0].astype("float32"), transform=transform, crs=windows[0].crs)


def normalized_difference(a: Window2D, b: Window2D) -> Window2D:
    """``(a - b) / (a + b)`` — e.g. MNDWI from green and SWIR, NDVI from NIR and red."""
    if a.data.shape != b.data.shape or a.transform != b.transform:
        raise ValueError("normalized_difference() needs windows on the same grid")
    with np.errstate(divide="ignore", invalid="ignore"):
        nd = (a.data - b.data) / (a.data + b.data)
    nd[~np.isfinite(nd)] = np.nan
    return Window2D(data=nd.astype("float32"), transform=a.transform, crs=a.crs)


def write_geotiff(
    window: Window2D,
    path: str | Path,
    *,
    dtype: str = "float32",
    nodata: float = np.nan,
) -> Path:
    """
    Write ``window`` as a GeoTIFF in a portable CRS.

    The default is float32 with NaN as nodata (continuous layers). For
    categorical layers pass an integer ``dtype`` and ``nodata`` value: NaN
    pixels are written as ``nodata`` and the rest are cast, so class codes
    stay exact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows, cols = window.data.shape
    data = window.data
    if np.issubdtype(np.dtype(dtype), np.integer):
        if nodata is None or (isinstance(nodata, float) and np.isnan(nodata)):
            raise ValueError("integer rasters need an integer nodata value")
        data = np.where(np.isnan(data), nodata, data).astype(dtype)
    else:
        data = data.astype(dtype)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype=dtype,
        crs=portable_crs(window.crs), transform=window.transform, nodata=nodata,
        compress="deflate",
    ) as dst:
        dst.write(data, 1)
    return path




# ---------------------------------------------------------------------------
# Registration with provenance
# ---------------------------------------------------------------------------

def register_raster(
    cube,
    source_id: str,
    window: Window2D,
    out_dir: str | Path,
    provenance: dict,
    *,
    name: str | None = None,
    time: int | None = None,
    tags: Sequence[str] = (),
    dtype: str = "float32",
    nodata: float = np.nan,
):
    """
    Write ``window`` as ``<out_dir>/<source_id>.tif`` and register it as a source.

    The file's SHA-256 becomes the source ``checksum`` — so new data (another
    period, year, reducer or scale) gets a new ``spec_hash`` downstream — and
    ``provenance`` is written next to it as ``<source_id>.provenance.json``
    together with the checksum, the retrieval time and the software versions.
    ``dtype``/``nodata`` are passed to :func:`write_geotiff` (integer types for
    categorical layers). Returns the ``SpatialSource``.
    """
    from disscube.models import SpatialSource

    out_dir = Path(out_dir)
    tif = write_geotiff(window, out_dir / f"{source_id}.tif", dtype=dtype, nodata=nodata)
    checksum = sha256_file(tif)
    record = {
        **provenance,
        "source_id": source_id,
        "file": tif.name,
        "checksum": checksum,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "software": software_versions(),
    }
    prov_path = out_dir / f"{source_id}.provenance.json"
    prov_path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    source = SpatialSource(
        id=source_id,
        name=name or source_id,
        format="raster",
        asset_url=str(tif),
        checksum=checksum,
        crs=BDC_CRS if is_bdc_albers(window.crs) else window.crs.to_string(),
        time=time,
        tags=[*tags, f"provenance:{prov_path}"],
    )
    cube.register_spatial_source(source)
    log.info("registered %s (%s) — provenance in %s", source_id, checksum[:15], prov_path)
    return source


def software_versions() -> dict[str, str | None]:
    """Versions of the packages that shape a source: reading, reduction, search."""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for pkg in ("disscube", "rasterio", "numpy", "pystac-client"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    out["gdal"] = rasterio.__gdal_version__
    return out


class _quiet_all_nan:
    """Silence numpy's 'All-NaN slice' / 'Mean of empty slice' warnings (cloud-covered pixels)."""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore", category=RuntimeWarning)

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)
