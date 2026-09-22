"""
03 — Time series and hand-off to DisSModel.

LUCC models consume a mix of time-varying variables (land use observed in
several years) and static drivers. This example derives:

  - ``forest_pct`` from two synthetic land-use maps (2010 and 2020), each
    registered as a source with its own ``time``
  - ``dist_road``  a static driver from a vector layer

``load()`` stacks the temporal slices into a ``(time, y, x)`` DataArray, and
``to_lucc_data()`` packs everything into a DisSModel ``RasterBackend``,
optionally restricted to a period.

    python examples/03_time_series.py            # temporary workspace
    python examples/03_time_series.py ./scratch  # keep the outputs
"""

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString

from disscube import CubeClient, Derivation, GridSpec, SpatialSource

CRS = "EPSG:31983"
XMIN, YMAX = 570_000.0, 9_720_000.0
FINE, N = 30.0, 400  # 12 km × 12 km at 30 m
FOREST, PASTURE = 3, 15
YEARS = (2010, 2020)


def make_inputs(raw: Path) -> None:
    """Deforestation advancing from a road along the southern edge."""
    rng = np.random.default_rng(3)
    dist_to_south = np.linspace(1, 0, N)[:, None].repeat(N, axis=1)  # 0 at the southern edge
    noise = 0.05 * rng.standard_normal((N, N))
    for year, front in zip(YEARS, (0.2, 0.5)):  # the deforestation front moves north
        landuse = np.where(dist_to_south + noise < front, PASTURE, FOREST).astype("uint8")
        with rasterio.open(
            raw / f"landuse_{year}.tif", "w", driver="GTiff", height=N, width=N, count=1,
            dtype="uint8", crs=CRS, transform=from_origin(XMIN, YMAX, FINE, FINE),
        ) as dst:
            dst.write(landuse, 1)

    road = LineString([(XMIN, YMAX - N * FINE + 300), (XMIN + N * FINE, YMAX - N * FINE + 300)])
    gpd.GeoDataFrame(geometry=[road], crs=CRS).to_file(raw / "roads.gpkg")


def main(workspace: Path) -> None:
    raw = workspace / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    make_inputs(raw)

    cube = CubeClient(catalog=str(workspace / "catalog.db"), store=str(workspace / "store"))
    grid_id = "demo/600m"
    cube.register_grid(GridSpec(
        id=grid_id, type="local", crs=CRS, resolution=600.0,
        bbox=[XMIN, YMAX - N * FINE, XMIN + N * FINE, YMAX],
    ))

    # One source per observation year; `time` stamps the derived slice.
    for year in YEARS:
        cube.register_spatial_source(SpatialSource(
            id=f"landuse_{year}", name=f"synthetic land use {year}", format="raster",
            asset_url=str(raw / f"landuse_{year}.tif"), crs=CRS, time=year,
        ))
        cube.derive_declarative(
            Derivation(
                target="forest_pct", source_id=f"landuse_{year}", operator="percentage",
                class_code=FOREST, valid_from=str(year), valid_until=str(year),
            ),
            grid_id=grid_id,
        )

    # A static driver: no time window.
    cube.register_spatial_source(SpatialSource(
        id="roads", name="synthetic road", format="vector",
        asset_url=str(raw / "roads.gpkg"), crs=CRS,
    ))
    cube.derive_declarative(
        Derivation(target="dist_road", source_id="roads", operator="min_distance"),
        grid_id=grid_id,
    )

    # Temporal variables load as (time, y, x); static ones as (y, x).
    forest = cube.load("forest_pct", grid_id=grid_id)
    print(f"forest_pct dims : {forest.dims}  times={forest['time'].values.tolist()}")
    for year in YEARS:
        print(f"  forest cover {year}: {float(forest.sel(time=year).mean()):.0%}")

    # Hand-off to DisSModel: a RasterBackend with all requested variables.
    backend = cube.to_lucc_data(["forest_pct", "dist_road"], grid_id=grid_id)
    print(f"\nRasterBackend   : temporal={backend.temporal_band_names()}  static={backend.static_band_names()}")
    print(f"  forest_pct time axis: {backend.time_axis('forest_pct').tolist()}")

    # A model reads each variable by name (and year, if temporal).
    loss = backend.get("forest_pct", time=2010) - backend.get("forest_pct", time=2020)
    dist_km = backend.get("dist_road") / 1_000
    print("  mean forest loss 2010→2020 by distance to the road:")
    for lo, hi in ((0, 3), (3, 6), (6, 12)):
        band = (dist_km >= lo) & (dist_km < hi)
        print(f"    {lo:>2}–{hi:<2} km : {loss[band].mean():.0%}")

    # `period` keeps only the slices inside the interval.
    recent = cube.to_lucc_data(["forest_pct"], grid_id=grid_id, period=("2015", "2020"))
    print(f"\nperiod 2015–2020: forest_pct time axis = {recent.time_axis('forest_pct').tolist()}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(Path(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            main(Path(tmp))
