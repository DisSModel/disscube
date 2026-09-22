import logging
import math

from pyproj import Transformer

from disscube.client import CubeClient
from disscube.models import GridSpec

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Master Grid Constants (Brazil Data Cube Standard)
# ---------------------------------------------------------------------------

# EPSG:10857 is the official code for BDC Albers (SIRGAS 2000 / Brazil Albers
# Equal Area Conic), registered in 2023. Using it instead of a raw proj4 string
# ensures QGIS and other tools recognise the CRS automatically without requiring
# manual configuration or a custom CRS entry.
#BDC_CRS = "EPSG:10857"
BDC_CRS = (
    "+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22"
    " +x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs"
)

# Full Brazil bbox in BDC Albers, snapped to 5 km mesh.
BRAZIL_BBOX = [2_720_000, 7_500_000, 7_870_000, 11_830_000]

# National reference resolutions
SIMULATION_GRIDS = [
    ("BR/5km",  5_000.0,  "National LUCC grid — 5 km pixels, BDC Albers"),
    ("BR/1km",  1_000.0,  "National LUCC grid — 1 km pixels, BDC Albers"),
]

# ---------------------------------------------------------------------------
# Grid Registration Utilities
# ---------------------------------------------------------------------------

def register_simulation_grids(cube: CubeClient) -> None:
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
    cube: CubeClient,
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
