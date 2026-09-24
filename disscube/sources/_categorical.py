"""
Helpers for categorical rasters: legends and reclassification.

Land-cover products ship their class codes in different ways — a QGIS style
file (``.qml``) next to the GeoTIFF (PRODES), a CSV or JSON table, or a list
of labels kept by the software that produced the map (SITS). These helpers
turn any of them into ``{code: label}`` and remap codes on a
:class:`~disscube.sources.Window2D`.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from disscube.sources._raster import Window2D

_PALETTE_ENTRY = re.compile(r"<paletteEntry\b[^>]*>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def read_qml_legend(source: str | Path) -> dict[int, str]:
    """
    ``{code: label}`` from a QGIS style file's ``<paletteEntry>`` elements.

    ``source`` is a path or the QML text itself. Labels are returned as
    written (PRODES prefixes the code: ``"7 d2007"``); see :func:`strip_code`.
    """
    text = str(source)
    if not text.lstrip().startswith("<"):
        text = Path(source).read_text(encoding="utf-8")
    legend: dict[int, str] = {}
    for entry in _PALETTE_ENTRY.findall(text):
        attrs = dict(_ATTR.findall(entry))
        if "value" in attrs:
            legend[int(float(attrs["value"]))] = attrs.get("label", "")
    if not legend:
        raise ValueError("no <paletteEntry value=... label=...> entries found in the QML")
    return legend


def read_legend(source: str | Path | Mapping) -> dict[int, str]:
    """
    ``{code: label}`` from a mapping, a ``.qml``, a ``.json`` (object or list of
    ``{"code", "label"}``) or a ``.csv`` with ``code,label`` columns.
    """
    if isinstance(source, Mapping):
        return {int(k): str(v) for k, v in source.items()}
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".qml":
        return read_qml_legend(path)
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {int(k): str(v) for k, v in data.items()}
        return {int(d["code"]): str(d["label"]) for d in data}
    if suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as fh:
            return {int(row["code"]): row["label"] for row in csv.DictReader(fh)}
    raise ValueError(f"unsupported legend format: {path.name} (use .qml, .json or .csv)")


def strip_code(label: str) -> str:
    """``"7 d2007"`` -> ``"d2007"``; labels without a leading code are returned trimmed."""
    return re.sub(r"^\s*\d+\s+", "", label).strip()


def reclassify(window: Window2D, lookup: Mapping[int, int]) -> Window2D:
    """
    Map class codes through ``lookup``; codes not in it (and NaN) become NaN.

    Codes are compared as integers, so the window may come from any integer
    raster read by :func:`~disscube.sources.read_window`.
    """
    src = window.data
    out = np.full(src.shape, np.nan, dtype="float32")
    valid = ~np.isnan(src)
    codes = src[valid].astype(np.int64)
    mapped = np.full(codes.shape, np.nan, dtype="float32")
    for old, new in lookup.items():
        mapped[codes == int(old)] = new
    out[valid] = mapped
    return Window2D(data=out, transform=window.transform, crs=window.crs)
