"""
Read Brazil Data Cube (BDC) data cubes through its STAC catalog.

The BDC publishes analysis-ready data cubes — 16-day composites of Landsat
(``LANDSAT-16D-1``), Sentinel-2 (``S2-16D-2``) and CBERS-4 (``CBERS4-WFI-16D-2``)
— as Cloud-Optimized GeoTIFFs indexed by a public STAC API. This module fetches
only the pixels that cover an area of interest (windowed HTTP reads, no full
tile downloads), reduces a period to one composite, and writes a local GeoTIFF
that can be registered as an ordinary ``SpatialSource``::

    from disscube.utils.bdc_stac import fetch_composite

    fetch_composite(
        "LANDSAT-16D-1", "NDVI",
        bbox_geo=(-44.35, -2.62, -44.20, -2.47),
        period="2020-07-01/2020-09-30",
        out_path="raw/ndvi_2020.tif",
    )

Searching the catalog needs ``pystac-client`` (``pip install disscube[bdc]``);
reading windows and writing composites only need rasterio, so they also work
with asset URLs obtained elsewhere.

Values are returned as float32 in physical units: nodata becomes NaN and the
scale/offset declared by the asset (STAC ``raster:bands``, or the GeoTIFF
itself) is applied. Composites are written in BDC Albers using the proj4
definition in :data:`disscube.utils.grids.BDC_CRS`, because the EPSG code the
cubes declare (EPSG:10857) is missing from older PROJ databases.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from disscube.utils.grids import BDC_CRS

log = logging.getLogger(__name__)

BDC_STAC_URL = "https://data.inpe.br/bdc/stac/v1/"

#: EPSG code the BDC cubes declare for BDC Albers (registered in 2023).
BDC_ALBERS_EPSG = 10857

#: Scale of the vegetation indices (NDVI, EVI, NBR) in the BDC 16-day cubes:
#: stored as int16 × 10 000. The catalog does not declare it (neither STAC
#: ``raster:bands`` nor the GeoTIFFs, checked on LANDSAT-16D-1 in 2026-09), so
#: pass it explicitly: ``read_composite(..., "NDVI", ..., scale=BDC_INDEX_SCALE)``.
BDC_INDEX_SCALE = 1e-4


@dataclass
class Window2D:
    """Pixels read from one asset, in physical units (NaN where nodata)."""

    data: np.ndarray        # float32, (rows, cols)
    transform: Affine
    crs: CRS


# ---------------------------------------------------------------------------
# Catalog search (needs pystac-client)
# ---------------------------------------------------------------------------

def search_items(
    collection: str,
    bbox_geo: Sequence[float],
    period: str,
    *,
    url: str = BDC_STAC_URL,
    max_items: int | None = None,
) -> list:
    """
    Return the STAC items of ``collection`` that intersect ``bbox_geo``.

    ``bbox_geo`` is ``(min_lon, min_lat, max_lon, max_lat)`` in WGS84 and
    ``period`` an ISO interval such as ``"2020-07-01/2020-09-30"``. Items are
    returned oldest first.
    """
    try:
        from pystac_client import Client
    except ImportError as exc:  # pragma: no cover — exercised only without the extra
        raise ImportError(
            "Searching the BDC catalog requires pystac-client: pip install disscube[bdc]"
        ) from exc

    client = Client.open(url)
    search = client.search(collections=[collection], bbox=list(bbox_geo),
                           datetime=period, max_items=max_items)
    items = sorted(search.items(), key=lambda i: i.datetime or 0)
    log.info("[bdc] %s: %d items over %s in %s", collection, len(items), bbox_geo, period)
    return items


def asset_scale_offset(item, asset: str) -> tuple[float | None, float | None]:
    """Scale and offset declared for ``asset`` by the STAC raster extension, if any."""
    bands = item.assets[asset].extra_fields.get("raster:bands") or []
    if not bands:
        return None, None
    return bands[0].get("scale"), bands[0].get("offset")


# ---------------------------------------------------------------------------
# Windowed reads (rasterio only)
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
    must share shape, transform and CRS (true for items of one BDC tile).
    """
    windows = list(windows)
    if not windows:
        raise ValueError("composite() needs at least one window")
    first = windows[0]
    for w in windows[1:]:
        if w.data.shape != first.data.shape or w.transform != first.transform:
            raise ValueError(
                "windows cover different grids (shape or transform differ); "
                "composite items of a single BDC tile, or mosaic them first"
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
    Merge windows from neighbouring BDC tiles into one layer.

    BDC tiles of a collection share one pixel mesh, so this is a paste, not a
    resampling; where tiles overlap the first non-NaN value wins.
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


def tile_of(item) -> str:
    """BDC tile of a STAC item (``bdc:tiles`` property, or the tile field of its id)."""
    tiles = item.properties.get("bdc:tiles")
    if tiles:
        return str(tiles[0])
    parts = item.id.split("_")
    return parts[-2] if len(parts) >= 3 else item.id


def normalized_difference(a: Window2D, b: Window2D) -> Window2D:
    """``(a - b) / (a + b)`` — e.g. MNDWI from green and SWIR, NDVI from NIR and red."""
    if a.data.shape != b.data.shape or a.transform != b.transform:
        raise ValueError("normalized_difference() needs windows on the same grid")
    with np.errstate(divide="ignore", invalid="ignore"):
        nd = (a.data - b.data) / (a.data + b.data)
    nd[~np.isfinite(nd)] = np.nan
    return Window2D(data=nd.astype("float32"), transform=a.transform, crs=a.crs)


def write_geotiff(window: Window2D, path: str | Path) -> Path:
    """Write ``window`` as a float32 GeoTIFF (NaN nodata) in a portable CRS."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows, cols = window.data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="float32",
        crs=portable_crs(window.crs), transform=window.transform, nodata=np.nan,
        compress="deflate",
    ) as dst:
        dst.write(window.data, 1)
    return path


# ---------------------------------------------------------------------------
# One-call helper
# ---------------------------------------------------------------------------

def fetch_composite(
    collection: str,
    asset: str,
    bbox_geo: Sequence[float],
    period: str,
    out_path: str | Path,
    *,
    reducer: str = "median",
    url: str = BDC_STAC_URL,
    scale: float | None = None,
    offset: float | None = None,
) -> Path:
    """
    Search, read and reduce one asset over a period, and write the composite.

    Items are reduced tile by tile; when the area spans several BDC tiles the
    per-tile composites are mosaicked. ``scale``/``offset`` override whatever
    the catalog or the files declare (see :data:`BDC_INDEX_SCALE`).
    """
    return write_geotiff(
        read_composite(collection, asset, bbox_geo, period, reducer=reducer, url=url,
                       scale=scale, offset=offset),
        out_path,
    )


def read_composite(
    collection: str,
    asset: str,
    bbox_geo: Sequence[float],
    period: str,
    *,
    reducer: str = "median",
    url: str = BDC_STAC_URL,
    items: Sequence | None = None,
    scale: float | None = None,
    offset: float | None = None,
) -> Window2D:
    """Like :func:`fetch_composite`, but return the composite instead of writing it.

    Pass ``items`` (e.g. from an earlier :func:`search_items`) to avoid searching
    the catalog again when several assets of the same items are needed.
    """
    if items is None:
        items = search_items(collection, bbox_geo, period, url=url)
    if not items:
        raise ValueError(f"no {collection} items over {bbox_geo} in {period}")
    by_tile: dict[str, list] = {}
    for item in items:
        by_tile.setdefault(tile_of(item), []).append(item)
    per_tile = [
        composite([read_item_window(i, asset, bbox_geo, scale=scale, offset=offset)
                   for i in tile_items], reducer)
        for _, tile_items in sorted(by_tile.items())
    ]
    return mosaic(per_tile)


def read_item_window(
    item,
    asset: str,
    bbox_geo: Sequence[float],
    *,
    scale: float | None = None,
    offset: float | None = None,
) -> Window2D:
    """
    Read ``asset`` of a STAC ``item`` over ``bbox_geo``.

    Scale and offset come, in order of precedence, from the arguments, from the
    item's STAC ``raster:bands``, and from the GeoTIFF itself.
    """
    stac_scale, stac_offset = asset_scale_offset(item, asset)
    return read_window(
        item.assets[asset].href, bbox_geo,
        scale=scale if scale is not None else stac_scale,
        offset=offset if offset is not None else stac_offset,
    )


class _quiet_all_nan:
    """Silence numpy's 'All-NaN slice' / 'Mean of empty slice' warnings (cloud-covered pixels)."""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore", category=RuntimeWarning)

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)
