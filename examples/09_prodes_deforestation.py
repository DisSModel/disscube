"""
09 — PRODES: deforestation south of Santarém (LuccME Lab15 area), three years.

Downloads the current PRODES edition of the Legal Amazon from TerraBrasilis
(≈130 MB, once — cached in ``$DISSCUBE_CACHE`` or ``~/.cache/disscube``), reads
its legend from the ``.qml`` shipped in the ZIP, and turns the area of the
LuccME Lab15 cellular space (along the BR-163, near Mojuí dos Campos, Pará)
into forest / deforested / other at the end of 2008, 2016 and 2024. The maps
are aggregated onto a 500 m grid snapped to the BDC Albers mesh:

  - ``deforested_pct``  fraction of each cell deforested (cumulative)
  - ``forest_pct``      fraction still under native forest

    python examples/09_prodes_deforestation.py               # temporary workspace
    python examples/09_prodes_deforestation.py ./scratch     # keep the outputs
    python examples/09_prodes_deforestation.py --offline     # no network: synthetic stand-in

The PRODES edition distributed today separates deforestation by year from
2007 on, so earlier years are not available here (see
``disscube.sources.prodes``). Without network access (or with ``--offline`` /
``DISSCUBE_OFFLINE=1``, as in the test suite) the script uses a synthetic
PRODES-like ZIP and says so loudly: those numbers are not PRODES data.
"""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import prodes
from disscube.utils.grids import register_local_grid

YEARS = (2008, 2016, 2024)
BBOX = (-54.842, -3.587, -54.459, -3.168)   # LuccME Lab15 cellular space (cs_moju)
GRID_RES = 500.0


def synthetic_edition(folder: Path) -> prodes.ProdesFiles:
    """A PRODES-like ZIP: clearings spreading east from a road, by year."""
    folder.mkdir(parents=True, exist_ok=True)
    name = "prodes_synthetic_2025"
    res = 0.00026949458523585647
    cols = int((BBOX[2] - BBOX[0]) / res) + 20
    rows = int((BBOX[3] - BBOX[1]) / res) + 20
    x = np.linspace(0, 1, cols)[None, :].repeat(rows, axis=0)
    rng = np.random.default_rng(11)
    noise = rng.random((rows, cols)) * 0.08
    data = np.full((rows, cols), 100, dtype="uint8")                  # native forest
    for year in range(2007, 2026):                                   # the frontier moves east
        data[(x + noise < 0.05 + 0.02 * (year - 2007)) & (data == 100)] = year - 2000
    data[:, cols - 40:] = 91                                          # a river on the east edge
    tif = folder / f"{name}.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="uint8",
                       crs="EPSG:4674", transform=from_origin(BBOX[0] - 10 * res, BBOX[3] + 10 * res, res, res),
                       nodata=255) as dst:
        dst.write(data, 1)
    entries = [(y - 2000, f"{y - 2000} d{y}") for y in range(2007, 2026)]
    entries += [(91, "91 Hidrografia"), (100, "100 Vegetação nativa florestal")]
    qml = "<qgis>" + "".join(f'<paletteEntry value="{v}" label="{lab}"/>' for v, lab in entries) + "</qgis>"
    zpath = folder / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(tif, tif.name)
        zf.writestr(f"{name}.qml", qml)
    return prodes.download(zpath.as_uri(), cache_dir=folder / "cache")


def main(workspace: Path, offline: bool) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    grid = register_local_grid(cube, name="lab15", bbox_geo=BBOX, resolution=GRID_RES)

    files, label = None, "synthetic stand-in"
    if not offline:
        try:
            files = prodes.download()
            label = f"PRODES {files.zip_path.stem}"
        except Exception as exc:  # noqa: BLE001 — any network failure falls back, loudly
            print(f"!! PRODES not reachable ({type(exc).__name__}: {exc})")
    if files is None:
        print("!! OFFLINE — using a synthetic PRODES-like map; the numbers below are NOT PRODES data")
        files = synthetic_edition(workspace / "synthetic")

    for year in YEARS:
        prodes.register_prodes_source(cube, f"prodes_{year}", year, BBOX, raw, files=files)
        for target, code in (("deforested_pct", prodes.DEFORESTED), ("forest_pct", prodes.FOREST)):
            cube.derive_declarative(Derivation(target=target, source_id=f"prodes_{year}",
                                               operator="percentage", class_code=code), grid_id=grid.id)

    defor = cube.load("deforested_pct", grid_id=grid.id)
    forest = cube.load("forest_pct", grid_id=grid.id)
    print(f"source              : {label}")
    print(f"grid                : {grid.id}  {defor.sizes['y']} × {defor.sizes['x']} cells")
    print("share of the area   :  " + "  ".join(f"{y:>6}" for y in YEARS))
    print("  deforested        :  " + "  ".join(f"{float(np.nanmean(defor.sel(time=y))):6.1%}" for y in YEARS))
    print("  native forest     :  " + "  ".join(f"{float(np.nanmean(forest.sel(time=y))):6.1%}" for y in YEARS))
    prov = json.loads((raw / f"prodes_{YEARS[-1]}.provenance.json").read_text(encoding="utf-8"))
    print(f"edition             : {prov['edition']} (years separable from {prov['first_separable_year']})")
    print(f"provenance          : {raw / f'prodes_{YEARS[-1]}.provenance.json'}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv or os.environ.get("DISSCUBE_OFFLINE") == "1"
    if args:
        main(Path(args[0]), offline)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp), offline)
