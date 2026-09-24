"""
Read MapBiomas annual land-cover and land-use maps of Brazil.

MapBiomas publishes one GeoTIFF per year for the whole country on Google Cloud
Storage, tiled in 256 × 256 blocks, so the pixels of an area of interest can be
read over HTTP without downloading the national file::

    from disscube.sources.mapbiomas import register_mapbiomas_source

    register_mapbiomas_source(cube, "lulc_2020", 2020,
                              bbox_geo=(-44.35, -2.62, -44.20, -2.47), out_dir="raw")

Two datasets are known:

- Collection 11, 30 m (Landsat), 1985–2025 — the default
- Collection 4 of the 10 m series (Sentinel-2), 2017–2025

The maps are categorical (``uint8`` class codes, WGS84). The files declare no
nodata, but code 0 means "not observed" (e.g. sea outside the mapped area); it
is read as nodata and written as 0 with ``nodata=0``, so it never counts as a
class in ``percentage``/``majority``. Data © MapBiomas, CC-BY-4.0.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from disscube.sources._raster import Window2D, read_window, register_raster

log = logging.getLogger(__name__)

_BASE = "https://storage.googleapis.com/mapbiomas-public/initiatives/brasil"

#: MapBiomas code for "not observed"; the files do not declare it as nodata.
MAPBIOMAS_NODATA = 0

LEGEND_URL = "https://brasil.mapbiomas.org/en/codigos-de-legenda/"
LICENSE = "CC-BY-4.0"

#: Names of the classes most common in land-change studies. MapBiomas codes
#: are stable across collections, but check the legend of the collection in
#: use (:data:`LEGEND_URL`) for the full, authoritative list.
CLASSES = {
    3: "Forest Formation",
    4: "Savanna Formation",
    5: "Mangrove",
    6: "Floodable Forest",
    11: "Wetland",
    12: "Grassland",
    15: "Pasture",
    21: "Mosaic of Uses",
    23: "Beach, Dune and Sand Spot",
    24: "Urban Area",
    25: "Other non Vegetated Areas",
    32: "Hypersaline Tidal Flat",
    33: "River, Lake and Ocean",
}


@dataclass(frozen=True)
class Dataset:
    """One MapBiomas coverage series: where its annual files live and which years exist."""

    collection: int
    resolution: int          # metres
    first_year: int
    last_year: int
    url_template: str        # formatted with ``year``

    def url(self, year: int) -> str:
        if not self.first_year <= year <= self.last_year:
            raise ValueError(
                f"MapBiomas collection {self.collection} ({self.resolution} m) covers "
                f"{self.first_year}–{self.last_year}; got {year}"
            )
        return self.url_template.format(year=year)


DATASETS = {
    (11, 30): Dataset(11, 30, 1985, 2025,
                      f"{_BASE}/collection11/lulc/coverage/brazil_coverage/"
                      "brazil_coverage-col11_{year}.tif"),
    (4, 10): Dataset(4, 10, 2017, 2025,
                     f"{_BASE}/lulc_10m/collection4/coverage/brazil_coverage/"
                     "brazil_coverage-col4_10m_{year}.tif"),
}


def dataset(collection: int = 11, resolution: int = 30) -> Dataset:
    """The known coverage series for ``collection`` at ``resolution`` metres."""
    try:
        return DATASETS[(collection, resolution)]
    except KeyError:
        known = ", ".join(f"collection {c} at {r} m" for c, r in DATASETS)
        raise ValueError(f"unknown MapBiomas dataset; known: {known}") from None


def coverage_url(year: int, collection: int = 11, resolution: int = 30) -> str:
    """URL of the national coverage GeoTIFF for ``year``."""
    return dataset(collection, resolution).url(year)


def read_coverage(
    year: int,
    bbox_geo: Sequence[float],
    *,
    collection: int = 11,
    resolution: int = 30,
    url: str | None = None,
) -> Window2D:
    """
    Read the land-cover classes of ``year`` over ``bbox_geo`` (WGS84).

    Returns class codes as float32 with NaN where MapBiomas has code 0. ``url``
    overrides the published file (e.g. a local copy).
    """
    href = url or coverage_url(year, collection, resolution)
    return read_window(href, bbox_geo, nodata=MAPBIOMAS_NODATA)


def register_mapbiomas_source(
    cube,
    source_id: str,
    year: int,
    bbox_geo: Sequence[float],
    out_dir: str | Path,
    *,
    collection: int = 11,
    resolution: int = 30,
    url: str | None = None,
    name: str | None = None,
):
    """
    Read one year of MapBiomas over ``bbox_geo`` and register it as a source.

    Writes ``<out_dir>/<source_id>.tif`` as ``uint8`` with nodata 0, keyed by
    its SHA-256, next to a ``<source_id>.provenance.json`` (dataset, collection,
    resolution, year, URL, bbox, legend and license). ``time`` is ``year``.
    """
    href = url or coverage_url(year, collection, resolution)
    window = read_coverage(year, bbox_geo, collection=collection, resolution=resolution, url=href)
    provenance = {
        "dataset": "MapBiomas Brazil — annual land cover and land use",
        "collection": collection,
        "resolution_m": resolution,
        "year": year,
        "url": href,
        "bbox_geo": list(bbox_geo),
        "nodata": MAPBIOMAS_NODATA,
        "legend": LEGEND_URL,
        "license": LICENSE,
    }
    return register_raster(
        cube, source_id, window, out_dir, provenance,
        name=name or f"MapBiomas collection {collection} ({resolution} m), {year}",
        time=year,
        tags=["mapbiomas", f"collection:{collection}", f"resolution:{resolution}m", f"year:{year}"],
        dtype="uint8", nodata=MAPBIOMAS_NODATA,
    )
