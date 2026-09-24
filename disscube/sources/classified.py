"""
Register any classified land-cover map — e.g. one produced by SITS — as a source.

`SITS <https://e-sensing.github.io/sitsbook/>`_ classifies satellite image time
series (including the Brazil Data Cube cubes) into land-cover maps written as
GeoTIFFs. This adapter reads such a map, or any other categorical GeoTIFF,
over an area of interest and registers it with its legend in the provenance
record, so it enters the pipeline like the MapBiomas and PRODES maps::

    from disscube.sources.classified import register_classified_map

    register_classified_map(
        cube, "lulc_sits_2020", "classified/santarem_class_2020.tif",
        bbox_geo, out_dir="raw", time=2020,
        legend={1: "Forest", 2: "Pasture", 3: "Water"},   # or a .qml/.json/.csv
        producer="SITS (sits_classify + sits_label_classification)",
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

from disscube.sources._categorical import read_legend
from disscube.sources._raster import read_window, register_raster
from disscube.utils.files import sha256_file


def register_classified_map(
    cube,
    source_id: str,
    href: str | Path,
    bbox_geo: Sequence[float],
    out_dir: str | Path,
    *,
    legend: Mapping | str | Path | None = None,
    time: int | None = None,
    nodata: float | None = None,
    producer: str | None = None,
    name: str | None = None,
    tags: Sequence[str] = (),
):
    """
    Read a categorical GeoTIFF over ``bbox_geo`` and register it as a source.

    ``href`` is a local path or a URL (Cloud-Optimized GeoTIFFs are read by
    window). ``legend`` is a mapping or a ``.qml``/``.json``/``.csv`` file and
    goes into the provenance record; ``nodata`` overrides the file's own. The
    local copy is ``uint8`` (``uint16`` if a class code exceeds 254), with
    nodata 0 unless another value is given; a class coded 0 therefore needs an
    explicit ``nodata``.
    """
    window = read_window(str(href), bbox_geo, nodata=nodata)
    codes = window.data[~np.isnan(window.data)]
    out_nodata = int(nodata) if nodata is not None else 0
    if out_nodata == 0 and codes.size and (codes == 0).any():
        raise ValueError("the map has class 0; pass nodata= with the value that means 'no data'")
    top = int(codes.max()) if codes.size else 0
    dtype = "uint8" if max(top, out_nodata) <= 254 else "uint16"

    labels = read_legend(legend) if legend is not None else None
    provenance = {
        "dataset": "classified map",
        "producer": producer,
        "href": str(href),
        "bbox_geo": list(bbox_geo),
        "nodata": out_nodata,
        "legend": {str(k): v for k, v in labels.items()} if labels else None,
    }
    if Path(str(href)).exists():
        provenance["href_checksum"] = sha256_file(href)
    return register_raster(
        cube, source_id, window, out_dir, provenance,
        name=name or f"classified map {source_id}", time=time,
        tags=["classified", *(["producer:" + producer] if producer else []), *tags],
        dtype=dtype, nodata=out_nodata,
    )
