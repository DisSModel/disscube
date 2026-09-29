"""
Utilities and helper functions for DisSCube.

Exposes file checksum helpers, BDC coordinate reference systems and grid registration utilities.
"""

from __future__ import annotations

from disscube.utils.bdc_importer import (
    BDC_TILE_LEVELS,
    bundled_bdc_grid,
    import_bdc_grids,
)
from disscube.utils.files import sha256_file
from disscube.utils.grids import (
    BDC_CRS,
    BRAZIL_BBOX,
    SIMULATION_GRIDS,
    register_local_grid,
    register_simulation_grids,
)

__all__ = [
    "BDC_CRS",
    "BDC_TILE_LEVELS",
    "BRAZIL_BBOX",
    "SIMULATION_GRIDS",
    "bundled_bdc_grid",
    "import_bdc_grids",
    "register_local_grid",
    "register_simulation_grids",
    "sha256_file",
]
