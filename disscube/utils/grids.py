"""
Grid registration utilities (canonical implementation is in disscube.models.grid).

Preserved here for backward compatibility.
"""

from __future__ import annotations

from disscube.models.grid import (
    BDC_CRS,
    BRAZIL_BBOX,
    SIMULATION_GRIDS,
    register_local_grid,
    register_simulation_grids,
)

__all__ = [
    "BDC_CRS",
    "BRAZIL_BBOX",
    "SIMULATION_GRIDS",
    "register_local_grid",
    "register_simulation_grids",
]
