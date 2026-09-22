import logging

from shapely.geometry import shape

from disscube.client import CubeClient
from disscube.models import SpatialSource

from .grids import BDC_CRS, register_simulation_grids

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BDC Specific Constants
# ---------------------------------------------------------------------------

BDC_TILE_LEVELS = [
    ("SM", "sm_path",  "BDC Small tile grid  (~1.5° × 1.5°)"),
    ("MD", "md_path",  "BDC Medium tile grid (~3° × 3°)"),
    ("LG", "lg_path",  "BDC Large tile grid  (~6° × 6°)"),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_bdc_grids(cube: CubeClient, sm_path: str, md_path: str, lg_path: str):
    """Import BDC tiles and national simulation grids into the catalog."""
    register_simulation_grids(cube)
    paths = {"sm_path": sm_path, "md_path": md_path, "lg_path": lg_path}
    _register_tile_sources(cube, paths)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _register_tile_sources(cube: CubeClient, paths: dict[str, str]) -> None:
    """Register BDC tile envelopes as SpatialSources."""
    try:
        import fiona
    except ImportError as exc:
        raise ImportError(
            "fiona is required for BDC import; install disscube[bdc]"
        ) from exc

    level_paths = [
        ("SM", paths["sm_path"]),
        ("MD", paths["md_path"]),
        ("LG", paths["lg_path"]),
    ]
    for label, path in level_paths:
        log.info("Importing BDC_%s tiles from %s", label, path)
        count = 0
        with fiona.open(path) as src:
            for rec in src:
                tile_id: str = rec["properties"]["tile"]
                geom = shape(rec["geometry"])
                bbox = list(geom.bounds)

                source = SpatialSource(
                    id=f"BDC_{label}_{tile_id}",
                    name=f"BDC {label} Tile {tile_id}",
                    format="raster",
                    asset_url=f"data/bdc/{label}/{tile_id}.tif",
                    crs=BDC_CRS,
                    bbox=bbox,
                )
                cube.register_spatial_source(source)
                count += 1

        log.info("[tiles] registered %d BDC_%s tiles", count, label)
