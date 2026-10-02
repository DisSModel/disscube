"""Elevation and slope from a digital elevation model (SRTM, Copernicus GLO-30, TOPODATA).

A pipeline names the DEM and the product and gets a raster source with a checksum and
provenance, ready for the zonal operators (``mean``, ``min``...)::

    [[source]]
    id      = "slope"
    type    = "dem"
    dem     = "srtm"            # srtm | copernicus | topodata
    product = "slope_deg"       # elevation | slope_deg | slope_pct

    [[derive]]
    target   = "media_decl"
    source   = "slope"
    operator = "mean"

Only the window over the grid (plus ``margin``) is read: SRTM and Copernicus tiles are read
straight from public cloud storage, TOPODATA sheets are downloaded once into a cache
(``$DISSCUBE_CACHE/dem``, or ``~/.cache/disscube/dem``). ``tiles`` takes your own GeoTIFFs
instead.

**Slope** needs the neighbours of every pixel, which an operator that aggregates a cell does
not have, so it is computed here: the window is resampled (bilinear) to the UTM zone of the
grid at ``resolution`` metres, so the slope is metric, and taken by central differences
(``numpy.gradient``), in degrees or percent. That is not Horn's 3 × 3 formula, so values differ
slightly from ``gdaldem slope``.

**Which DEM.** SRTM (C-band radar, February 2000) and TOPODATA (INPE's refinement of it)
measure close to the ground; Copernicus GLO-30 (X-band, 2011–2015) is a *surface* model that
includes the forest canopy, so every forest/pasture edge becomes a ~30 m "cliff" and the slope
is overestimated exactly where deforestation happens. For slope prefer SRTM or TOPODATA.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

from disscube.sources._raster import Window2D, mosaic, read_window, register_raster

log = logging.getLogger("disscube.sources.dem")

#: Copernicus DEM GLO-30, public Cloud-Optimized GeoTIFFs on AWS (no account), named by the SW corner.
COPERNICUS_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
                  "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
#: SRTM 1 arc-second as republished in the AWS Terrain Tiles ("skadi", public, no key), gzip-ed HGT.
SRTM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{ns}{lat:02d}/{ns}{lat:02d}{ew}{lon:03d}.hgt.gz"
#: TOPODATA (INPE): 1° × 1.5° sheets named by their north-west corner, e.g. ``03S555`` = 3–4°S, 55.5–54°W. ZN = altitude.
TOPODATA_URL = "http://www.dsr.inpe.br/topodata/data/geotiff/{sheet}ZN.zip"

DATASETS: dict[str, dict[str, str]] = {
    "srtm": {"description": "SRTM 1 arc-second (Feb 2000, C-band radar), AWS Terrain Tiles (skadi)",
             "license": "public domain (NASA/USGS)", "surface": "near ground (canopy partly penetrated)"},
    "copernicus": {"description": "Copernicus DEM GLO-30 (2011-2015, X-band): a surface model",
                   "license": "Copernicus DEM, © DLR e.V. and Airbus Defence and Space GmbH; free licence",
                   "surface": "surface: includes the forest canopy"},
    "topodata": {"description": "TOPODATA (INPE): SRTM refined to 1 arc-second by kriging",
                 "license": "INPE, free access", "surface": "near ground (refined SRTM)"},
}
PRODUCTS: dict[str, str] = {"elevation": "m", "slope_deg": "degrees", "slope_pct": "percent"}
#: Plausible elevations in metres; anything else is a void (SRTM marks them -32768).
VALID_RANGE = (-1000.0, 9000.0)


class DemError(RuntimeError):
    """The elevation data could not be obtained or does not cover the area."""


def default_cache_dir() -> Path:
    """``$DISSCUBE_CACHE/dem``, or ``~/.cache/disscube/dem``."""
    root = os.environ.get("DISSCUBE_CACHE") or Path.home() / ".cache" / "disscube"
    return Path(root) / "dem"


def utm_epsg(bbox_geo: Sequence[float]) -> int:
    """EPSG code of the WGS84 / UTM zone of the centre of ``bbox_geo`` (``[w, s, e, n]``)."""
    lon, lat = (bbox_geo[0] + bbox_geo[2]) / 2, (bbox_geo[1] + bbox_geo[3]) / 2
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def padded(bbox_geo: Sequence[float], margin: float) -> list[float]:
    w, s, e, n = (float(v) for v in bbox_geo)
    return [max(w - margin, -180.0), max(s - margin, -90.0), min(e + margin, 180.0), min(n + margin, 90.0)]


def _degree_range(lo: float, hi: float) -> range:
    """Integer degrees of the 1° tiles (named by their lower edge) that ``[lo, hi]`` touches."""
    first = math.floor(lo)
    return range(first, max(math.ceil(hi) - 1, first) + 1)


def tile_urls(bbox_geo: Sequence[float], dem: str) -> list[str]:
    """Where the tiles of ``dem`` covering ``bbox_geo`` (``[w, s, e, n]``, WGS84) live."""
    if dem not in DATASETS:
        raise ValueError(f"unknown DEM {dem!r}; choose from {sorted(DATASETS)}")
    w, s, e, n = bbox_geo
    if dem == "topodata":
        sheets: set[str] = set()
        for lat in range(math.ceil(s), math.ceil(n) + 1):  # north edge of the sheet
            for k in range(math.floor(w / 1.5), math.floor(e / 1.5) + 1):
                west = -k * 1.5  # west edge, degrees W
                sheets.add(f"{abs(lat):02d}{'S' if lat <= 0 else 'N'}{round(west * 10):03d}")
        return [TOPODATA_URL.format(sheet=sh) for sh in sorted(sheets)]
    template = COPERNICUS_URL if dem == "copernicus" else SRTM_URL
    return [template.format(ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon))
            for lat in _degree_range(s, n) for lon in _degree_range(w, e)]


def _require_http(url: str) -> str:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"DEM URL must be http(s): {url!r}")
    return url


def _download(url: str, folder: Path, timeout: float = 300) -> Path:
    """Fetch ``url`` once into ``folder`` (atomically); an existing file is reused."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / url.rsplit("/", 1)[-1]
    if target.exists():
        return target
    log.info("downloading %s", url)
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(_require_http(url), timeout=timeout) as resp, open(tmp, "wb") as out:  # nosec B310
            shutil.copyfileobj(resp, out, length=1 << 20)
    except OSError as exc:  # URLError, HTTPError and timeouts are all OSError
        tmp.unlink(missing_ok=True)
        raise DemError(f"could not download {url}: {exc}") from exc
    tmp.replace(target)
    return target


def openable(url: str, dem: str, cache: str | Path | None = None) -> str:
    """A path GDAL can open for a tile: the COG itself, gzip over HTTP, or a downloaded ZIP."""
    if dem == "copernicus":
        return url
    if dem == "srtm":
        return f"/vsigzip//vsicurl/{url}" if "://" in url else f"/vsigzip/{url}"
    archive = _download(url, Path(cache) if cache else default_cache_dir())
    with zipfile.ZipFile(archive) as zf:
        tif = next((nm for nm in zf.namelist() if nm.lower().endswith(".tif")), None)
    if tif is None:
        raise DemError(f"{archive.name} holds no GeoTIFF")
    return f"/vsizip/{archive}/{tif}"


def read_dem(bbox_geo: Sequence[float], dem: str | None = None, tiles: Sequence[str] | None = None,
             cache: str | Path | None = None) -> tuple[Window2D, list[str]]:
    """Elevation (metres, NaN at voids) over ``bbox_geo``, and the tiles it came from.

    ``tiles`` (GeoTIFFs readable by GDAL) replace the download of ``dem``. A tile that cannot be
    opened is skipped with a warning (the sea has none), but at least one must be read.
    """
    if (dem is None) == (tiles is None):
        raise ValueError("give exactly one of 'dem' or 'tiles'")
    sources = list(tiles) if tiles is not None else tile_urls(bbox_geo, dem)  # type: ignore[arg-type]
    windows: list[Window2D] = []
    used: list[str] = []
    failures: list[str] = []
    for src in sources:
        try:
            href = src if tiles is not None else openable(src, dem, cache)  # type: ignore[arg-type]
            win = read_window(href, bbox_geo)
        except (RasterioIOError, rasterio.errors.WindowError) as exc:
            failures.append(f"{src}: {exc}")
            log.warning("tile skipped: %s", failures[-1])
            continue
        if win.data.size == 0:
            continue
        data = win.data
        data[(data < VALID_RANGE[0]) | (data > VALID_RANGE[1])] = np.nan
        windows.append(win)
        used.append(src)
    if not windows:
        raise DemError("no elevation data over the area: " + ("; ".join(failures) or "no tile intersects it"))
    return mosaic(windows), used


def to_utm(window: Window2D, epsg: int, resolution: float) -> Window2D:
    """Resample ``window`` (bilinear) to the UTM zone ``epsg`` with square ``resolution``-metre pixels."""
    rows, cols = window.data.shape
    dst_crs = CRS.from_epsg(epsg)
    transform, width, height = calculate_default_transform(
        window.crs, dst_crs, cols, rows, *array_bounds(rows, cols, window.transform), resolution=resolution)
    out = np.full((height, width), np.nan, dtype="float32")
    reproject(window.data, out, src_transform=window.transform, src_crs=window.crs, src_nodata=np.nan,
              dst_transform=transform, dst_crs=dst_crs, dst_nodata=np.nan, resampling=Resampling.bilinear)
    return Window2D(data=out, transform=transform, crs=dst_crs)


def slope(window: Window2D) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and in percent of a DEM in metric coordinates (central differences)."""
    dzdy, dzdx = np.gradient(window.data, abs(window.transform.e), abs(window.transform.a))
    rise = np.hypot(dzdx, dzdy)
    return np.degrees(np.arctan(rise)).astype("float32"), (100 * rise).astype("float32")


def make_product(elevation: Window2D, product: str, bbox_geo: Sequence[float], resolution: float) -> Window2D:
    """The raster of ``product`` from an elevation window (see :data:`PRODUCTS`)."""
    if product not in PRODUCTS:
        raise ValueError(f"unknown DEM product {product!r}; choose from {sorted(PRODUCTS)}")
    if product == "elevation":
        return elevation
    metric = to_utm(elevation, utm_epsg(bbox_geo), resolution)
    degrees, percent = slope(metric)
    return Window2D(data=degrees if product == "slope_deg" else percent, transform=metric.transform, crs=metric.crs)


def register_dem_source(cube, source_id: str, bbox_geo: Sequence[float], out_dir: str | Path, *,
                        dem: str | None = None, tiles: Sequence[str] | None = None, product: str = "elevation",
                        margin: float = 0.02, resolution: float = 30.0, cache: str | Path | None = None,
                        name: str | None = None):
    """Read the DEM over the grid, make ``product`` and register it as a raster source.

    The GeoTIFF is ``<out_dir>/<source_id>.tif`` (float32, NaN nodata; elevation in EPSG:4326,
    slopes in the UTM zone of the grid) with a ``<source_id>.provenance.json`` holding the DEM,
    its licence, the tiles, the window, the processing and the checksum.
    """
    if product not in PRODUCTS:
        raise ValueError(f"unknown DEM product {product!r}; choose from {sorted(PRODUCTS)}")
    if dem == "copernicus" and product != "elevation":
        log.warning("%s: Copernicus GLO-30 includes the forest canopy, so slopes at forest edges are overestimated; "
                    "SRTM or TOPODATA are closer to the ground", source_id)
    bbox = padded(bbox_geo, margin)
    elevation, used = read_dem(bbox, dem, tiles, cache)
    layer = make_product(elevation, product, bbox, resolution)

    info: dict[str, Any] = DATASETS.get(dem or "", {"description": "user-supplied tiles", "license": "see the data's provider"})
    processing = ("elevation as read, EPSG:4326" if product == "elevation" else
                  f"bilinear to UTM {layer.crs.to_string()} at {resolution:g} m, central-difference slope (numpy.gradient)")
    provenance = {"type": "dem", "dem": dem or "tiles", "description": info["description"], "license": info["license"],
                  "product": product, "unit": PRODUCTS[product], "tiles": used, "bbox_geo": bbox, "margin": margin,
                  "processing": processing, "crs": layer.crs.to_string()}
    if "surface" in info:
        provenance["surface"] = info["surface"]
    return register_raster(cube, source_id, layer, out_dir, provenance, name=name, tags=["dem", dem or "tiles", product])
