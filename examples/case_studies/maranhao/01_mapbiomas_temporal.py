"""
examples/case_studies/maranhao/01_mapbiomas_temporal.py

Derives a MapBiomas time series for Ilha do Maranhão (100 m):
  - uso       (majority, temporal 2010-2022)
  - dist_sedes (min_distance, static)

Prerequisites:
  - python examples/setup/01_init_catalog.py
  - python examples/setup/02_register_sources.py
  - Files data/raw/ilha_maranhao_mapbiomas_{2010,2022}.tif present

Usage:
    python examples/case_studies/maranhao/01_mapbiomas_temporal.py
"""

from disscube.client import CubeClient
from disscube.models import SpatialSource, SpatialDerivation
from disscube.models.variable import Variable
from disscube.utils.grids import register_local_grid


def main():
    # ── 1. Client ────────────────────────────────────────────────────────────
    cube = CubeClient(catalog="catalog.db", store="data/")

    # ── 2. Local grid ────────────────────────────────────────────────────────
    register_local_grid(
        cube,
        name="ilha_maranhao",
        bbox_geo=(-44.42, -2.80, -44.02, -2.40),
        resolution=100,
        snap=True,
    )

    # ── 3. Temporal derivation (MapBiomas 2010, 2022) ────────────────────────
    for year in [2010, 2022]:
        cube.register_spatial_source(SpatialSource(
            id=f"mapbiomas_ilha_ma_{year}",
            name=f"MapBiomas Ilha do Maranhão — {year}",
            format="raster",
            asset_url=f"data/raw/ilha_maranhao_mapbiomas_{year}.tif",
            crs="EPSG:4326",
            time=year,
        ))
        print(f"\n[pipeline] Processando ano {year}...")
        cube.derive(SpatialDerivation(
            source_id=f"mapbiomas_ilha_ma_{year}",
            grid_id="ilha_maranhao/100m",
            role="land_use",
            variables=[Variable(name="uso", operator="majority")],
        ))

    # ── 4. Static variable ───────────────────────────────────────────────────
    print("\n[pipeline] Processing distance to municipal seats...")
    cube.derive(SpatialDerivation(
        source_id="urban_centers",
        grid_id="ilha_maranhao/100m",
        role="driver",
        variables=[Variable(name="dist_sedes", operator="min_distance")],
    ))

    # ── 5. Verification ──────────────────────────────────────────────────────
    # "uso" returns (time, y, x) because it is temporal; "dist_sedes" returns (y, x)
    da_uso = cube.load("uso", grid_id="ilha_maranhao/100m")
    print(f"\nuso:       {da_uso.dims}  anos={list(da_uso.coords['time'].values)}")

    da_sedes = cube.load("dist_sedes", grid_id="ilha_maranhao/100m")
    print(f"dist_sedes:{da_sedes.dims}  shape={da_sedes.shape}")

    # ── 6. DisSModel integration ─────────────────────────────────────────────
    backend = cube.to_lucc_data(["uso", "dist_sedes"], grid_id="ilha_maranhao/100m")
    print(f"\nBackend pronto: {backend}")


if __name__ == "__main__":
    main()
