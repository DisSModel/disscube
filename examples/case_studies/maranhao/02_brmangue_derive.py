"""
examples/case_studies/maranhao/02_brmangue_derive.py

BR-MANGUE — derives land-use variables for Ilha do Maranhão (100 m):
  - uso, alt, solo  (majority / mean over maranhao_base)

Prerequisites:
  - python examples/setup/01_init_catalog.py
  - python examples/setup/02_register_sources.py

Usage:
    python examples/case_studies/maranhao/02_brmangue_derive.py
"""

from disscube.client import CubeClient
from disscube.models import SpatialDerivation, Variable

GRID_ID   = "ilha_maranhao/100m"
SOURCE_ID = "maranhao_base"
cube = CubeClient(catalog="catalog.db", store="./data/")

if not cube.catalog.get_grid(GRID_ID):
    raise RuntimeError(f"Grid {GRID_ID!r} not found. Run examples/setup/01_init_catalog.py first.")
if not cube.catalog.get_spatial_source(SOURCE_ID):
    raise RuntimeError(f"Source {SOURCE_ID!r} not found. Run examples/setup/02_register_sources.py first.")

print(f"\n[derive] {SOURCE_ID} @ {GRID_ID}...")
cube.derive(SpatialDerivation(
    source_id=SOURCE_ID,
    grid_id=GRID_ID,
    role="luc_observation",
    variables=[
        Variable(name="uso",  operator="majority"),
        Variable(name="alt",  operator="mean"),
        Variable(name="solo", operator="majority"),
    ],
))

print("\n=== BR-MANGUE derivation complete ===")
