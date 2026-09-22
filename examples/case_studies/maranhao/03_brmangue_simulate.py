"""
examples/case_studies/maranhao/03_brmangue_simulate.py

BR-MANGUE — runs BrmangueRasterExecutor on the derived variables.

Prerequisite:
  - python examples/case_studies/maranhao/02_brmangue_derive.py

Usage:
    python examples/case_studies/maranhao/03_brmangue_simulate.py
"""

from disscube.client import CubeClient

try:
    from brmangue.executors.raster_executor import BrmangueRasterExecutor
    from dissmodel.executor import ExperimentRecord
except ImportError as e:
    print(f"Warning: {e}. Skipping simulation step.")
    BrmangueRasterExecutor = None
    ExperimentRecord = None

GRID_ID   = "ilha_maranhao/100m"
SOURCE_ID = "maranhao_base"
VARIABLES = ["uso", "alt", "solo"]

cube = CubeClient(catalog="catalog.db", store="./data/")

print(f"\n[load] {VARIABLES} @ {GRID_ID}")
backend = cube.to_lucc_data(VARIABLES, grid_id=GRID_ID)
print(f"  bands: {backend.band_names()}")

if BrmangueRasterExecutor and ExperimentRecord:
    print("\n--- Running BrmangueRasterExecutor ---")
    source = cube.catalog.get_spatial_source(SOURCE_ID)

    record = ExperimentRecord(
        experiment_id="brmangue_cube_integration",
        parameters={"end_time": 5, "interactive": False, "bands": ["uso"]},
        source={"uri": source.asset_url if source else "unknown"},
        input_format="tiff",
    )

    executor = BrmangueRasterExecutor()
    executor_data = BrmangueRasterExecutor.from_cube(backend)
    result = executor.run(executor_data, record)
    executor.save(result, record)

    print(f"Simulation done. Output: {record.output_path}")
else:
    print("\nBrmangueRasterExecutor not available — simulation skipped.")
