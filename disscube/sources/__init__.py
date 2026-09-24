"""
Data-source adapters: bring external data into a cube as sources.

Each adapter reads only the pixels of an area of interest and registers the
result with the same contract (see :func:`register_raster`): a local GeoTIFF,
its SHA-256 as the source ``checksum`` — which keys the derivation cache — and
a ``<source>.provenance.json`` sidecar recording where the data came from.

- :mod:`disscube.sources.bdc` — Brazil Data Cube data cubes via STAC
- :mod:`disscube.sources.mapbiomas` — MapBiomas annual land-cover maps
"""

from disscube.sources._raster import (
    Window2D,
    composite,
    is_bdc_albers,
    mosaic,
    normalized_difference,
    portable_crs,
    read_window,
    register_raster,
    software_versions,
    write_geotiff,
)

__all__ = [
    "Window2D",
    "composite",
    "is_bdc_albers",
    "mosaic",
    "normalized_difference",
    "portable_crs",
    "read_window",
    "register_raster",
    "software_versions",
    "write_geotiff",
]
