import logging
import math
import re
import warnings
from typing import Any, Literal

import numpy as np
from affine import Affine
from pydantic import BaseModel
from pyproj import Transformer

log = logging.getLogger(__name__)


class GridAnchor(BaseModel):
    """
    DEPRECATED: Use GridSpec instead.
    This class is kept for backward compatibility only.
    """
    id: str
    crs: str
    resolution: float
    bbox: list[float]

    def __init__(self, **data):
        warnings.warn(
            "GridAnchor is deprecated and will be removed in a future version. Use GridSpec instead.",
            DeprecationWarning,
            stacklevel=2
        )
        super().__init__(**data)

class SpatialRelation(BaseModel):
    source_grid_id: str
    target_grid_id: str
    strategy: Literal["simple", "chooseone", "keepinboth"]
    params: dict = {}     # e.g. {"min_intersection": 0.01} for keepinboth
    metadata: dict = {}   # description, bibliographic reference, etc.

class GridSpec(BaseModel):
    id: str
    type: Literal["local", "global", "reference"]
    crs: str
    resolution: float
    bbox: list[float]  # [minx, miny, maxx, maxy]
    description: str | None = None

    @property
    def rows(self) -> int:
        return round((self.bbox[3] - self.bbox[1]) / self.resolution)

    @property
    def cols(self) -> int:
        return round((self.bbox[2] - self.bbox[0]) / self.resolution)

    @property
    def transform(self) -> Affine:
        # North-up transform: origin at (minx, maxy), negative y-scale
        return Affine.translation(self.bbox[0], self.bbox[3]) @ Affine.scale(self.resolution, -self.resolution)

    @property
    def xs(self) -> np.ndarray:
        return np.arange(self.cols) * self.resolution + self.bbox[0] + self.resolution/2

    @property
    def ys(self) -> np.ndarray:
        return self.bbox[3] - (np.arange(self.rows) * self.resolution + self.resolution/2)

    def to_toml(self) -> str:
        import toml
        return toml.dumps(self.model_dump())

    def cell_id(self, row: int, col: int) -> str:
        """Return a stable identifier: 'grid_id:R0991C0047'"""
        return f"{self.id}:R{row:04d}C{col:04d}"

    def cell_id_from_coords(self, x: float, y: float) -> str:
        """
        Given a point (x, y) in the grid CRS, return its cell_id.

        CRITICAL — North-Up origin:
        If the bbox uses the upper-left corner (north-up), row/col are computed as:
            row = int((origin_y - y) / resolution)
            col = int((x - origin_x) / resolution)
        """
        minx, miny, maxx, maxy = self.bbox
        if not (minx <= x <= maxx and miny <= y <= maxy):
            raise ValueError(f"Coordinates ({x}, {y}) out of grid bbox {self.bbox}")
        
        row = int((maxy - y) / self.resolution)
        col = int((x - minx) / self.resolution)
        
        # Boundary check for exact maxx/miny which might result in index = num_cells
        num_rows = self.rows
        num_cols = self.cols
        
        if row >= num_rows: row = num_rows - 1
        if col >= num_cols: col = num_cols - 1
        
        return self.cell_id(row, col)

    def coords_from_cell_id(self, cell_id: str) -> tuple[float, float]:
        """Return the cell centroid (x, y) in the grid CRS."""
        grid_id, row, col = self.parse_cell_id(cell_id)
        if grid_id != self.id:
            raise ValueError(f"Cell ID {cell_id} does not match grid ID {self.id}")
        
        minx, _miny, _maxx, maxy = self.bbox
        x = minx + (col + 0.5) * self.resolution
        y = maxy - (row + 0.5) * self.resolution
        return (x, y)

    @staticmethod
    def parse_cell_id(cell_id: str) -> tuple[str, int, int]:
        """Return (grid_id, row, col) parsed from a cell_id."""
        try:
            grid_id, coords = cell_id.split(":")
            # Robust parsing using regex to support any number of digits
            match = re.match(r"^R(\d+)C(\d+)$", coords)
            if not match:
                raise ValueError(f"Invalid coordinate format in cell_id: {coords}")
            
            row = int(match.group(1))
            col = int(match.group(2))
            return grid_id, row, col
        except Exception as e:
            if isinstance(e, ValueError) and "Invalid coordinate format" in str(e):
                raise
            raise ValueError(f"Invalid cell_id format: {cell_id}") from e


# ---------------------------------------------------------------------------
# Master Grid Constants (Brazil Data Cube Standard)
# ---------------------------------------------------------------------------

BDC_CRS = (
    "+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22"
    " +x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs"
)

# Full Brazil bbox in BDC Albers, snapped to 5 km mesh.
BRAZIL_BBOX: list[float] = [2_720_000, 7_500_000, 7_870_000, 11_830_000]

# National reference resolutions
SIMULATION_GRIDS = [
    ("BR/5km",  5_000.0,  "National LUCC grid — 5 km pixels, BDC Albers"),
    ("BR/1km",  1_000.0,  "National LUCC grid — 1 km pixels, BDC Albers"),
]

# ---------------------------------------------------------------------------
# Grid Registration Utilities
# ---------------------------------------------------------------------------

def register_simulation_grids(cube: Any) -> None:
    """Register national simulation grids (pixel resolution of derived output)."""
    for grid_id, resolution, description in SIMULATION_GRIDS:
        grid = GridSpec(
            id=grid_id,
            type="reference",
            crs=BDC_CRS,
            resolution=resolution,
            bbox=BRAZIL_BBOX,
            description=description,
        )
        cube.register_grid(grid)
        log.info("[grid] registered %r  (%d m pixels, %d rows × %d cols)",
                 grid_id, int(resolution), grid.rows, grid.cols)


def register_local_grid(
    cube: Any,
    name: str | None = None,
    state: str | None = None,
    bbox_geo: tuple[float, float, float, float] | None = None,
    resolution: float = 5_000.0,
    snap: bool = True,
) -> GridSpec:
    """
    Register a local simulation grid snapped to the national mesh.

    This ensures that any Area of Interest (AOI) has pixels that align
    perfectly with the national master grids, enabling interoperability
    without resampling.
    """
    name = name or state
    if not name:
        raise ValueError("Either 'name' or 'state' must be provided.")

    if bbox_geo is None:
        raise ValueError("'bbox_geo' must be provided.")

    transformer = Transformer.from_crs("EPSG:4326", BDC_CRS, always_xy=True)

    corners = [
        (bbox_geo[0], bbox_geo[1]),  # SW
        (bbox_geo[0], bbox_geo[3]),  # NW
        (bbox_geo[2], bbox_geo[3]),  # NE
        (bbox_geo[2], bbox_geo[1]),  # SE
    ]
    xs, ys = zip(*(transformer.transform(lon, lat) for lon, lat in corners))

    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)

    if snap:
        minx = math.floor(minx / resolution) * resolution
        miny = math.floor(miny / resolution) * resolution
        maxx = math.ceil(maxx  / resolution) * resolution
        maxy = math.ceil(maxy  / resolution) * resolution

    is_km = resolution >= 1000 and math.isclose(resolution % 1000, 0, abs_tol=1e-5)
    if is_km:
        res_str = f"{int(resolution // 1000)}km"
    else:
        res_str = f"{int(resolution)}m"

    grid_id = f"{name}/{res_str}"
    grid = GridSpec(
        id=grid_id,
        type="reference",
        crs=BDC_CRS,
        resolution=resolution,
        bbox=[minx, miny, maxx, maxy],
        description=f"{name} simulation grid — {resolution:.0f} m pixels, BDC Albers",
    )
    cube.register_grid(grid)

    # Back-project for human-readable summary
    back = Transformer.from_crs(BDC_CRS, "EPSG:4326", always_xy=True)
    lon_min, lat_min = back.transform(minx, miny)
    lon_max, lat_max = back.transform(maxx, maxy)

    log.info(
        "[grid] registered %r  bbox(BDC Albers)=[%.0f, %.0f, %.0f, %.0f]"
        "  bbox(geo)=lon[%.2f,%.2f] lat[%.2f,%.2f]"
        "  size=%d×%d cells  (%.0f m pixels)",
        grid_id, minx, miny, maxx, maxy,
        lon_min, lon_max, lat_min, lat_max,
        grid.rows, grid.cols, resolution,
    )
    return grid
