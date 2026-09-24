"""
07 — Brazil Data Cube: model drivers from a satellite data cube.

Reads the Landsat 16-day data cube of the Brazil Data Cube (``LANDSAT-16D-1``)
over Ilha do Maranhão through the BDC STAC catalog, fetching only the pixels
of the area of interest, and turns one dry season into three drivers on a
300 m grid snapped to the BDC Albers mesh:

  - ``ndvi_mean``   mean NDVI (vegetation vigour)
  - ``mndwi_mean``  mean MNDWI, (green − SWIR1) / (green + SWIR1)
  - ``water_pct``   fraction of each cell with MNDWI > 0 (open water)

The season is reduced to a per-pixel median of the 16-day composites, which
removes most of the remaining clouds. Each source is registered with the
SHA-256 of its file as checksum and a ``<source>.provenance.json`` sidecar
(collection, items, period, reducer, scale), so re-running with another
period recomputes the drivers instead of returning cached ones.

    pip install -e ".[bdc]"                      # pystac-client for the search
    python examples/07_bdc_cube.py               # temporary workspace
    python examples/07_bdc_cube.py ./scratch     # keep the outputs
    python examples/07_bdc_cube.py --offline     # no network: synthetic stand-in

Without network access (or with ``--offline`` / ``DISSCUBE_OFFLINE=1``, as in
the test suite) the script builds a synthetic scene with the same grid, CRS
and value ranges, and says so loudly: those numbers are not BDC data.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from disscube import CubeClient, Derivation, SpatialSource
from disscube.utils.bdc_stac import Window2D, register_composite
from disscube.utils.files import sha256_file
from disscube.utils.grids import BDC_CRS, register_local_grid

COLLECTION = "LANDSAT-16D-1"
PERIOD = "2020-07-01/2020-09-30"           # dry season
BBOX = (-44.35, -2.62, -44.20, -2.47)      # São Luís, Ilha do Maranhão (WGS84)
GRID_RES = 300.0


# ---------------------------------------------------------------------------
# Inputs: real (BDC) or synthetic (offline) — both registered with provenance
# ---------------------------------------------------------------------------

def sources_from_bdc(cube: CubeClient, raw: Path) -> Window2D:
    """Register ``ndvi`` and ``mndwi`` from the BDC; return the MNDWI layer."""
    from disscube.utils.bdc_stac import (
        BDC_INDEX_SCALE,
        BDC_STAC_URL,
        items_provenance,
        normalized_difference,
        read_composite,
        register_bdc_source,
        search_items,
    )

    items = search_items(COLLECTION, BBOX, PERIOD)
    if not items:
        raise RuntimeError(f"no {COLLECTION} items over {BBOX} in {PERIOD}")
    print(f"BDC items           : {len(items)} ({items[0].id} … {items[-1].id})")

    # The catalog declares no scale; BDC stores indices as int16 × 10 000.
    register_bdc_source(cube, "ndvi", COLLECTION, "NDVI", BBOX, PERIOD, raw,
                        items=items, scale=BDC_INDEX_SCALE)

    # MNDWI is a ratio, so a common scale factor on green and SWIR cancels out.
    def season(asset):
        return read_composite(COLLECTION, asset, BBOX, PERIOD, items=items)

    mndwi = normalized_difference(season("green"), season("swir16"))
    register_composite(
        cube, "mndwi", mndwi, raw,
        provenance={
            "stac_url": BDC_STAC_URL, "collection": COLLECTION, "assets": ["green", "swir16"],
            "expression": "(green - swir16) / (green + swir16)", "bbox_geo": list(BBOX),
            "period": PERIOD, "reducer": "median", "items": items_provenance(items),
        },
        name=f"{COLLECTION} MNDWI (median of {PERIOD})", time=2020,
        tags=["bdc", f"collection:{COLLECTION}", f"period:{PERIOD}"],
    )
    return mndwi


def sources_synthetic(cube: CubeClient, raw: Path) -> Window2D:
    """Water to the north-west, an urban core, vegetation elsewhere — in BDC Albers."""
    from pyproj import Transformer

    to_albers = Transformer.from_crs("EPSG:4326", BDC_CRS, always_xy=True)
    x0, y1 = to_albers.transform(BBOX[0], BBOX[3])
    x1, y0 = to_albers.transform(BBOX[2], BBOX[1])
    res = 30.0
    cols, rows = int((x1 - x0) / res), int((y1 - y0) / res)
    transform = from_origin(x0, y1, res, res)

    rng = np.random.default_rng(7)
    x = np.linspace(0, 1, cols)[None, :].repeat(rows, axis=0)
    y = np.linspace(0, 1, rows)[:, None].repeat(cols, axis=1)
    water = (x + (1 - y)) < 0.55
    urban = (x - 0.6) ** 2 + (y - 0.5) ** 2 < 0.04
    ndvi = np.clip(0.75 - 0.45 * urban + 0.05 * rng.standard_normal((rows, cols)), -1, 1)
    ndvi[water] = -0.1
    mndwi = np.clip(-0.35 + 0.05 * rng.standard_normal((rows, cols)), -1, 1)
    mndwi[water] = 0.45

    crs = CRS.from_string(BDC_CRS)
    layers = {name: Window2D(data=arr.astype("float32"), transform=transform, crs=crs)
              for name, arr in (("ndvi", ndvi), ("mndwi", mndwi))}
    for name, window in layers.items():
        register_composite(cube, name, window, raw,
                           provenance={"synthetic": True, "note": "offline stand-in, not BDC data"},
                           name=f"{name} (synthetic stand-in)", time=2020, tags=["synthetic"])
    return layers["mndwi"]


def register_water_mask(cube: CubeClient, raw: Path, mndwi: Window2D) -> None:
    """1 = open water (MNDWI > 0), 0 = land, 255 = no observation."""
    mask = np.where(mndwi.data > 0, 1, 0).astype("uint8")
    mask[np.isnan(mndwi.data)] = 255
    rows, cols = mask.shape
    path = raw / "water.tif"
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="uint8", crs=BDC_CRS, transform=mndwi.transform, nodata=255) as dst:
        dst.write(mask, 1)
    cube.register_spatial_source(SpatialSource(
        id="water", name="open water (MNDWI > 0)", format="raster", asset_url=str(path),
        crs=BDC_CRS, time=2020, checksum=sha256_file(path), tags=["derived:mndwi>0"],
    ))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def main(workspace: Path, offline: bool) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))

    # 300 m grid snapped to the national BDC Albers mesh
    grid = register_local_grid(cube, name="ilha_do_maranhao", bbox_geo=BBOX, resolution=GRID_RES)

    label = "synthetic stand-in"
    mndwi = None
    if not offline:
        try:
            mndwi = sources_from_bdc(cube, raw)
            label = f"BDC {COLLECTION}, median of {PERIOD}"
        except Exception as exc:  # noqa: BLE001 — any network/catalog failure falls back, loudly
            print(f"!! BDC not reachable ({type(exc).__name__}: {exc})")
    if mndwi is None:
        print("!! OFFLINE — using a synthetic scene; the numbers below are NOT BDC data")
        mndwi = sources_synthetic(cube, raw)
    register_water_mask(cube, raw, mndwi)

    for d in (
        Derivation(target="ndvi_mean", source_id="ndvi", operator="mean"),
        Derivation(target="mndwi_mean", source_id="mndwi", operator="mean"),
        Derivation(target="water_pct", source_id="water", operator="percentage", class_code=1),
    ):
        cube.derive_declarative(d, grid_id=grid.id)

    ndvi = cube.load("ndvi_mean", grid_id=grid.id)
    mndwi_mean = cube.load("mndwi_mean", grid_id=grid.id)
    water = cube.load("water_pct", grid_id=grid.id)
    provenance = json.loads((raw / "ndvi.provenance.json").read_text(encoding="utf-8"))

    print(f"source              : {label}")
    print(f"grid                : {grid.id}  {ndvi.shape[-2]} × {ndvi.shape[-1]} cells")
    print(f"NDVI (cell means)   : {float(np.nanmin(ndvi)):.2f} … {float(np.nanmax(ndvi)):.2f}, "
          f"mean {float(np.nanmean(ndvi)):.2f}")
    print(f"MNDWI (cell means)  : {float(np.nanmin(mndwi_mean)):.2f} … "
          f"{float(np.nanmax(mndwi_mean)):.2f}")
    print(f"open water          : {float(np.nanmean(water)):.1%} of the area")
    print(f"cells without data  : {int(np.isnan(ndvi.values).sum())}")
    print(f"ndvi provenance     : {raw / 'ndvi.provenance.json'} "
          f"({len(provenance.get('items', []))} items, {provenance['checksum'][:19]}…)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv or os.environ.get("DISSCUBE_OFFLINE") == "1"
    if args:
        main(Path(args[0]), offline)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp), offline)
