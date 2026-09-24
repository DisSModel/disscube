"""
Read Brazil Data Cube (BDC) data cubes through its STAC catalog.

The BDC publishes analysis-ready data cubes — 16-day composites of Landsat
(``LANDSAT-16D-1``), Sentinel-2 (``S2-16D-2``) and CBERS-4 (``CBERS4-WFI-16D-2``)
— as Cloud-Optimized GeoTIFFs indexed by a public STAC API. This module fetches
only the pixels that cover an area of interest (windowed HTTP reads, no full
tile downloads), reduces a period to one composite, and writes a local GeoTIFF
that can be registered as an ordinary ``SpatialSource``::

    from disscube.sources.bdc import fetch_composite

    fetch_composite(
        "LANDSAT-16D-1", "NDVI",
        bbox_geo=(-44.35, -2.62, -44.20, -2.47),
        period="2020-07-01/2020-09-30",
        out_path="raw/ndvi_2020.tif",
        scale=BDC_INDEX_SCALE,
    )

To register the result in a cube with its provenance — a checksum that keys
the derivation cache and a JSON sidecar naming the collection, items, period,
reducer and scale — use :func:`register_bdc_source` (one asset) or
:func:`disscube.sources.register_raster` (a layer computed from several
assets)::

    from disscube.sources.bdc import BDC_INDEX_SCALE, register_bdc_source

    register_bdc_source(cube, "ndvi_2020", "LANDSAT-16D-1", "NDVI",
                        bbox_geo, "2020-07-01/2020-09-30", out_dir="raw",
                        scale=BDC_INDEX_SCALE)

Searching the catalog needs ``pystac-client`` (``pip install disscube[bdc]``);
reading windows and writing composites only need rasterio, so they also work
with asset URLs obtained elsewhere.

Values are returned as float32 in physical units: nodata becomes NaN and a
scale/offset is applied (argument, else STAC ``raster:bands``, else the
GeoTIFF). The BDC declares none, so pass :data:`BDC_INDEX_SCALE` for indices.
Composites are written in BDC Albers using the proj4 definition in
:data:`disscube.utils.grids.BDC_CRS`, because the EPSG code the cubes declare
(EPSG:10857) is missing from older PROJ databases.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from disscube.sources._raster import (
    Window2D,
    composite,
    mosaic,
    read_window,
    register_raster,
    write_geotiff,
)

log = logging.getLogger(__name__)

BDC_STAC_URL = "https://data.inpe.br/bdc/stac/v1/"

#: Scale of the vegetation indices (NDVI, EVI, NBR) in the BDC 16-day cubes:
#: stored as int16 × 10 000. The catalog does not declare it (neither STAC
#: ``raster:bands`` nor the GeoTIFFs, checked on LANDSAT-16D-1 in 2026-09), so
#: pass it explicitly: ``read_composite(..., "NDVI", ..., scale=BDC_INDEX_SCALE)``.
BDC_INDEX_SCALE = 1e-4


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


def tile_of(item) -> str:
    """BDC tile of a STAC item (``bdc:tiles`` property, or the tile field of its id)."""
    tiles = item.properties.get("bdc:tiles")
    if tiles:
        return str(tiles[0])
    parts = item.id.split("_")
    return parts[-2] if len(parts) >= 3 else item.id


# ---------------------------------------------------------------------------
# Composites over a period
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


# ---------------------------------------------------------------------------
# Registration with provenance
# ---------------------------------------------------------------------------

def items_provenance(items: Sequence, asset: str | None = None) -> list[dict]:
    """
    ``id``, dates and (optionally) asset URL of each item, for a provenance record.

    ``start_datetime``/``end_datetime`` (STAC common metadata) are recorded when
    the item declares them: a 16-day composite dated 2020-06-25 covers up to
    2020-07-10, which explains why it matches a July–September search.
    """
    out = []
    for item in items:
        entry = {"id": item.id,
                 "datetime": str(item.datetime) if getattr(item, "datetime", None) else None}
        props = getattr(item, "properties", None) or {}
        for key in ("start_datetime", "end_datetime"):
            if props.get(key):
                entry[key] = str(props[key])
        if asset is not None and asset in item.assets:
            entry["href"] = item.assets[asset].href
        out.append(entry)
    return out


def period_year(period: str) -> int | None:
    """First year of an ISO period (``"2020-07-01/2020-09-30"`` -> 2020), or None."""
    try:
        return int(period.split("/")[0][:4])
    except (ValueError, IndexError):
        return None


def register_bdc_source(
    cube,
    source_id: str,
    collection: str,
    asset: str,
    bbox_geo: Sequence[float],
    period: str,
    out_dir: str | Path,
    *,
    reducer: str = "median",
    scale: float | None = None,
    offset: float | None = None,
    url: str = BDC_STAC_URL,
    items: Sequence | None = None,
    name: str | None = None,
    time: int | None = None,
):
    """
    Fetch one asset of a BDC cube over a period and register it as a source.

    Combines :func:`read_composite` and :func:`~disscube.sources.register_raster`: the
    composite is written to ``out_dir``, keyed by its checksum, and described
    by a provenance sidecar (STAC URL, collection, asset, bbox, period,
    reducer, scale/offset and the items used). ``time`` defaults to the first
    year of ``period``.
    """
    if items is None:
        items = search_items(collection, bbox_geo, period, url=url)
    window = read_composite(collection, asset, bbox_geo, period, reducer=reducer,
                            url=url, items=items, scale=scale, offset=offset)
    provenance = {
        "stac_url": url,
        "collection": collection,
        "asset": asset,
        "bbox_geo": list(bbox_geo),
        "period": period,
        "reducer": reducer,
        "scale": scale,
        "offset": offset,
        "items": items_provenance(items, asset),
    }
    return register_raster(
        cube, source_id, window, out_dir, provenance,
        name=name or f"{collection} {asset} ({reducer} of {period})",
        time=time if time is not None else period_year(period),
        tags=["bdc", f"collection:{collection}", f"asset:{asset}", f"period:{period}"],
    )
