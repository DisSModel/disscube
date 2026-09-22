"""
tools/import_bdc_tiles.py

Imports the BDC tiles (SM / MD / LG) as SpatialSources in the catalog.
One-time operation; may take a few minutes depending on the size of the
shapefiles.

Prerequisite:
  - python examples/setup/01_init_catalog.py

Usage:
    python tools/import_bdc_tiles.py
"""

from disscube.client import CubeClient
from disscube.utils.bdc_importer import import_bdc_grids


def main():
    cube = CubeClient(catalog="catalog.db", store="./data/")

    print("=== Importing BDC tiles (one-time, may be slow) ===")
    import_bdc_grids(
        cube,
        sm_path="zip://data/bdc_grids/BDC_SM_V2.zip",
        md_path="zip://data/bdc_grids/BDC_MD_V2.zip",
        lg_path="zip://data/bdc_grids/BDC_LG_V2.zip",
    )
    print("=== Tiles BDC registrados ===")


if __name__ == "__main__":
    main()
