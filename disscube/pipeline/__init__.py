"""
DisSCube pipeline package: declarative pipeline files, planning, execution and raster alignment.
"""

from __future__ import annotations

import sys

# Backward-compatibility alias for legacy code importing disscube.config
from disscube.pipeline import runner as _runner
from disscube.pipeline import schema as _schema
from disscube.pipeline.context import PipelineContext, PipelineStage
from disscube.pipeline.runner import (
    ExportReport,
    PipelineError,
    Plan,
    RunReport,
    export_cube,
    load,
    plan,
    run,
)
from disscube.pipeline.schema import PipelineConfig

sys.modules["disscube.config"] = sys.modules[__name__]
sys.modules["disscube.config.runner"] = _runner
sys.modules["disscube.config.schema"] = _schema

__all__ = [
    "ExportReport",
    "PipelineConfig",
    "PipelineContext",
    "PipelineError",
    "PipelineStage",
    "Plan",
    "RunReport",
    "export_cube",
    "load",
    "plan",
    "run",
]
