"""
Pipeline files: declare a whole data preparation — grid, sources, derived
variables — in one TOML file, the DisSCube counterpart of a TerraME
``fillCellularSpace`` script.

    disscube validate pipeline.toml     # check the file, no downloads
    disscube run pipeline.toml          # fetch the sources and derive the variables

See ``docs/guides/pipeline_files.md`` for the format.
"""

from disscube.config.runner import Plan, RunReport, load, plan, run
from disscube.config.schema import PipelineConfig

__all__ = ["PipelineConfig", "Plan", "RunReport", "load", "plan", "run"]
