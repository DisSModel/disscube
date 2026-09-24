"""
Read PRODES deforestation maps of the Legal Amazon (INPE).

PRODES is distributed by TerraBrasilis as one ZIP per edition holding a
national GeoTIFF and a QGIS style file (``.qml``) with its legend. This module
downloads the ZIP once into a cache (keyed by file name and checked by
SHA-256), reads the legend from the ``.qml`` instead of hard-coding it, and
turns the raster into a forest / deforested / other map for a given year::

    from disscube.sources.prodes import register_prodes_source

    register_prodes_source(cube, "prodes_2019", 2019,
                           bbox_geo=(-54.84, -3.59, -54.46, -3.17), out_dir="raw")

Codes of the local map: :data:`FOREST` (1), :data:`DEFORESTED` (2),
:data:`OTHER` (3, non-forest vegetation and water), nodata 0 (clouds and
anything the legend does not describe).

**Years.** The edition distributed today labels deforestation by year from
``d2007`` on — the first of those codes holds everything deforested up to
that year — so earlier years cannot be separated; asking for one raises an
error. Use MapBiomas (annual since 1985) for earlier periods.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from disscube.sources._categorical import read_qml_legend, reclassify, strip_code
from disscube.sources._raster import Window2D, read_window, register_raster
from disscube.utils.files import sha256_file

log = logging.getLogger(__name__)

TERRABRASILIS = "https://terrabrasilis.dpi.inpe.br"
#: TerraBrasilis' own download index (a JSON list of published files).
DOWNLOAD_INDEX = f"{TERRABRASILIS}/business/api/v1/download/all"
#: Legal Amazon PRODES raster, edition 2025 (published 2026-04-08).
DEFAULT_URL = (f"{TERRABRASILIS}/download/dataset/legal-amz-prodes/raster/"
               "prodes_amazonia_legal_2025_v20260408.zip")

FOREST, DEFORESTED, OTHER = 1, 2, 3
NODATA = 0


def default_cache_dir() -> Path:
    """``$DISSCUBE_CACHE/prodes``, or ``~/.cache/disscube/prodes``."""
    root = os.environ.get("DISSCUBE_CACHE") or Path.home() / ".cache" / "disscube"
    return Path(root) / "prodes"


def latest_url(index_url: str = DOWNLOAD_INDEX, timeout: float = 30) -> str:
    """URL of the newest Legal Amazon PRODES raster listed by TerraBrasilis."""
    with urllib.request.urlopen(index_url, timeout=timeout) as resp:
        entries = json.load(resp)
    links = [e.get("link", "") for e in entries
             if "legal-amz-prodes/raster" in e.get("link", "") and e.get("link", "").endswith(".zip")]
    if not links:
        raise RuntimeError("no Legal Amazon PRODES raster in the TerraBrasilis download index")
    newest = max(links, key=lambda link: Path(link).name)
    return urljoin(index_url, newest)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

@dataclass
class ProdesFiles:
    """A downloaded PRODES edition: the GeoTIFF, its legend and where they came from."""

    url: str
    zip_path: Path
    zip_checksum: str
    tif: Path
    qml: Path | None


def download(url: str = DEFAULT_URL, cache_dir: str | Path | None = None,
             timeout: float = 120) -> ProdesFiles:
    """
    Download and unpack a PRODES ZIP into ``cache_dir`` (once; later calls reuse it).

    The ZIP is fetched to a temporary name and renamed when complete, so an
    interrupted download never leaves a truncated file in the cache.
    """
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / Path(url.split("?")[0]).name
    if not zip_path.exists():
        log.info("downloading %s", url)
        fd, tmp = tempfile.mkstemp(dir=cache, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out, \
                    urllib.request.urlopen(url, timeout=timeout) as resp:
                shutil.copyfileobj(resp, out, length=1 << 20)
            os.replace(tmp, zip_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    folder = cache / zip_path.stem
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        tifs = [n for n in names if n.lower().endswith(".tif")]
        qmls = [n for n in names if n.lower().endswith(".qml")]
        if len(tifs) != 1:
            raise ValueError(f"{zip_path.name}: expected one .tif, found {tifs}")
        for member in tifs + qmls[:1]:
            target = folder / Path(member).name
            if not target.exists():
                folder.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1 << 20)
    return ProdesFiles(
        url=url, zip_path=zip_path, zip_checksum=sha256_file(zip_path),
        tif=folder / Path(tifs[0]).name,
        qml=folder / Path(qmls[0]).name if qmls else None,
    )


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------

@dataclass
class Scheme:
    """What each PRODES code means, parsed from the edition's legend."""

    deforestation: dict[int, int] = field(default_factory=dict)   # year -> code
    residual: dict[int, int] = field(default_factory=dict)        # year -> code
    forest: set[int] = field(default_factory=set)
    non_forest: set[int] = field(default_factory=set)
    water: set[int] = field(default_factory=set)
    clouds: set[int] = field(default_factory=set)

    @property
    def first_year(self) -> int:
        if not self.deforestation:
            raise ValueError("the legend has no yearly deforestation classes (dYYYY)")
        return min(self.deforestation)

    def lookup(self, year: int) -> dict[int, int]:
        """Code -> FOREST / DEFORESTED / OTHER for the situation at the end of ``year``."""
        if year < self.first_year:
            raise ValueError(
                f"this PRODES edition separates deforestation from {self.first_year} on "
                f"(d{self.first_year} holds everything up to that year); {year} cannot be "
                "reconstructed from it — use MapBiomas for earlier years"
            )
        table: dict[int, int] = {}
        for code in self.forest:
            table[code] = FOREST
        for code in self.non_forest | self.water:
            table[code] = OTHER
        for yr, code in {**self.deforestation, **self.residual}.items():
            table[code] = DEFORESTED if yr <= year else FOREST
        return table


def parse_legend(legend: dict[int, str]) -> Scheme:
    """Classify PRODES legend labels (``"7 d2007"``, ``"91 Hidrografia"``, …)."""
    scheme = Scheme()
    for code, raw in legend.items():
        label = strip_code(raw)
        low = label.lower()
        if m := re.fullmatch(r"d(\d{4})", low):
            scheme.deforestation[int(m.group(1))] = code
        elif m := re.fullmatch(r"r(\d{4})", low):
            scheme.residual[int(m.group(1))] = code
        elif "hidrografia" in low or "hydrograph" in low or "água" in low:
            scheme.water.add(code)
        elif "nuvem" in low or "cloud" in low:
            scheme.clouds.add(code)
        elif "não florest" in low or "nao florest" in low or "non-forest" in low or "non forest" in low:
            scheme.non_forest.add(code)
        elif "florest" in low or "forest" in low:
            scheme.forest.add(code)
        else:
            log.warning("PRODES legend: unrecognised class %s %r (treated as nodata)", code, raw)
    return scheme


# ---------------------------------------------------------------------------
# Read and register
# ---------------------------------------------------------------------------

def read_prodes(year: int, bbox_geo: Sequence[float], files: ProdesFiles) -> tuple[Window2D, Scheme]:
    """Forest / deforested / other at the end of ``year`` over ``bbox_geo``."""
    if files.qml is None:
        raise ValueError(f"{files.zip_path.name} has no .qml legend; cannot interpret the codes")
    scheme = parse_legend(read_qml_legend(files.qml))
    raw = read_window(str(files.tif), bbox_geo)
    return reclassify(raw, scheme.lookup(year)), scheme


def register_prodes_source(
    cube,
    source_id: str,
    year: int,
    bbox_geo: Sequence[float],
    out_dir: str | Path,
    *,
    url: str = DEFAULT_URL,
    cache_dir: str | Path | None = None,
    files: ProdesFiles | None = None,
    name: str | None = None,
):
    """
    Register PRODES forest / deforested / other at the end of ``year`` as a source.

    Downloads the edition on first use (see :func:`download`). The provenance
    records the edition URL, the ZIP's SHA-256, the legend as read from the
    ``.qml`` and the code mapping applied.
    """
    files = files or download(url, cache_dir)
    window, scheme = read_prodes(year, bbox_geo, files)
    provenance = {
        "dataset": "PRODES — Legal Amazon deforestation (INPE)",
        "edition": files.zip_path.stem,
        "url": files.url,
        "zip_checksum": files.zip_checksum,
        "year": year,
        "bbox_geo": list(bbox_geo),
        "codes": {"forest": FOREST, "deforested": DEFORESTED, "other": OTHER, "nodata": NODATA},
        "first_separable_year": scheme.first_year,
        "legend": {str(k): v for k, v in read_qml_legend(files.qml).items()} if files.qml else None,
        "source": "https://terrabrasilis.dpi.inpe.br/downloads/",
    }
    return register_raster(
        cube, source_id, window, out_dir, provenance,
        name=name or f"PRODES {files.zip_path.stem}, situation at the end of {year}",
        time=year, tags=["prodes", f"edition:{files.zip_path.stem}", f"year:{year}"],
        dtype="uint8", nodata=NODATA,
    )
