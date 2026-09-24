"""
08 — MapBiomas: land use of Ilha do Maranhão in two years, on the model grid.

Reads the MapBiomas annual land-cover maps (Collection 11, 30 m) of 2000 and
2020 over Ilha do Maranhão — only the pixels of the area, straight from the
national files — and aggregates them onto the same 300 m grid as example 07
(snapped to the BDC Albers mesh):

  - ``landuse``       dominant class of each cell (``majority``)
  - ``urban_pct``     fraction of urban area (class 24)
  - ``forest_pct``    fraction of forest formation (class 3)
  - ``mangrove_pct``  fraction of mangrove (class 5)

Each year is a separate source (``time`` = year), so every variable loads as a
``(time, y, x)`` series and goes to DisSModel with ``to_lucc_data()``. Each
source carries its checksum and a ``provenance.json`` (collection, year, URL,
legend, license). MapBiomas code 0 ("not observed", e.g. open sea) is nodata.

    python examples/08_mapbiomas_land_use.py               # temporary workspace
    python examples/08_mapbiomas_land_use.py ./scratch     # keep the outputs
    python examples/08_mapbiomas_land_use.py --offline     # no network: synthetic stand-in

Without network access (or with ``--offline`` / ``DISSCUBE_OFFLINE=1``, as in
the test suite) the script builds a synthetic scene with the same grid, CRS
and class codes, and says so loudly: those numbers are not MapBiomas data.
Data © MapBiomas (CC-BY-4.0).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from rasterio.crs import CRS
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation
from disscube.sources import Window2D, register_raster
from disscube.sources.mapbiomas import CLASSES, MAPBIOMAS_NODATA, register_mapbiomas_source
from disscube.utils.grids import register_local_grid

YEARS = (2000, 2020)
BBOX = (-44.35, -2.62, -44.20, -2.47)      # São Luís, Ilha do Maranhão (WGS84)
GRID_RES = 300.0
FOREST, MANGROVE, PASTURE, URBAN, WATER = 3, 5, 15, 24, 33


# ---------------------------------------------------------------------------
# Inputs: real (MapBiomas) or synthetic (offline)
# ---------------------------------------------------------------------------

def sources_from_mapbiomas(cube: CubeClient, raw: Path) -> None:
    for year in YEARS:
        register_mapbiomas_source(cube, f"lulc_{year}", year, BBOX, raw)
        print(f"MapBiomas {year}       : read (collection 11, 30 m)")


def sources_synthetic(cube: CubeClient, raw: Path) -> None:
    """Sea to the north-west, a town that grows between the years, mangrove on the coast."""
    res = 0.00026949458523585647                      # MapBiomas 30 m pixel, in degrees
    cols = int((BBOX[2] - BBOX[0]) / res)
    rows = int((BBOX[3] - BBOX[1]) / res)
    transform = from_origin(BBOX[0], BBOX[3], res, res)
    x = np.linspace(0, 1, cols)[None, :].repeat(rows, axis=0)
    y = np.linspace(0, 1, rows)[:, None].repeat(cols, axis=1)
    rng = np.random.default_rng(3)

    for year, radius in zip(YEARS, (0.18, 0.28), strict=True):
        lulc = np.full((rows, cols), FOREST, dtype="float32")
        lulc[rng.random((rows, cols)) < 0.15] = PASTURE
        coast = x + (1 - y)
        lulc[(coast >= 0.45) & (coast < 0.55)] = MANGROVE
        lulc[(x - 0.6) ** 2 + (y - 0.5) ** 2 < radius ** 2] = URBAN
        lulc[coast < 0.45] = WATER
        lulc[coast < 0.15] = np.nan                    # "not observed" (open sea)
        register_raster(
            cube, f"lulc_{year}", Window2D(lulc, transform, CRS.from_epsg(4326)), raw,
            provenance={"synthetic": True, "note": "offline stand-in, not MapBiomas data"},
            name=f"land use {year} (synthetic stand-in)", time=year, tags=["synthetic"],
            dtype="uint8", nodata=MAPBIOMAS_NODATA,
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main(workspace: Path, offline: bool) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    grid = register_local_grid(cube, name="ilha_do_maranhao", bbox_geo=BBOX, resolution=GRID_RES)

    label = "synthetic stand-in"
    done = False
    if not offline:
        try:
            sources_from_mapbiomas(cube, raw)
            label = "MapBiomas collection 11 (30 m)"
            done = True
        except Exception as exc:  # noqa: BLE001 — any network failure falls back, loudly
            print(f"!! MapBiomas not reachable ({type(exc).__name__}: {exc})")
    if not done:
        print("!! OFFLINE — using a synthetic scene; the numbers below are NOT MapBiomas data")
        sources_synthetic(cube, raw)

    for year in YEARS:
        src = f"lulc_{year}"
        for d in (
            Derivation(target="landuse", source_id=src, operator="majority"),
            Derivation(target="urban_pct", source_id=src, operator="percentage", class_code=URBAN),
            Derivation(target="forest_pct", source_id=src, operator="percentage", class_code=FOREST),
            Derivation(target="mangrove_pct", source_id=src, operator="percentage", class_code=MANGROVE),
        ):
            cube.derive_declarative(d, grid_id=grid.id)

    landuse = cube.load("landuse", grid_id=grid.id)          # (time, y, x)
    print(f"source              : {label}")
    print(f"grid                : {grid.id}  {landuse.sizes['y']} × {landuse.sizes['x']} cells, "
          f"years {[int(t) for t in landuse['time'].values]}")
    print("share of the area   :  " + "  ".join(f"{y:>6}" for y in YEARS))
    for var, code in (("urban_pct", URBAN), ("forest_pct", FOREST), ("mangrove_pct", MANGROVE)):
        series = cube.load(var, grid_id=grid.id)
        shares = [float(np.nanmean(series.sel(time=y))) for y in YEARS]
        print(f"  {CLASSES[code]:<17} :  " + "  ".join(f"{s:6.1%}" for s in shares))

    first, last = (landuse.sel(time=y).values for y in YEARS)
    valid = ~np.isnan(first) & ~np.isnan(last)
    changed = int((first[valid] != last[valid]).sum())
    to_urban = int(((first != URBAN) & (last == URBAN) & valid).sum())
    print(f"dominant class      : changed in {changed} of {int(valid.sum())} cells "
          f"({to_urban} became urban)")

    # Hand-off to DisSModel: one RasterBackend with the (time, y, x) series.
    backend = cube.to_lucc_data(["landuse", "urban_pct"], grid_id=grid.id)
    times = [int(t) for t in backend.time_coords["landuse"]]
    print(f"to_lucc_data        : RasterBackend {backend.shape}, landuse and urban_pct at {times}")

    prov = json.loads((raw / f"lulc_{YEARS[-1]}.provenance.json").read_text(encoding="utf-8"))
    print(f"provenance          : {raw / f'lulc_{YEARS[-1]}.provenance.json'} "
          f"({prov.get('url', prov.get('note'))})")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv or os.environ.get("DISSCUBE_OFFLINE") == "1"
    if args:
        main(Path(args[0]), offline)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp), offline)
