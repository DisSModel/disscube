import logging
from importlib.resources import files

from shapely.geometry import shape

from disscube.client import CubeClient
from disscube.models import SpatialSource

from .grids import BDC_CRS, register_simulation_grids

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BDC Specific Constants
# ---------------------------------------------------------------------------

# Tile sizes of BDC Grid V2 (Albers equal-area, metres); ~1°, ~2° and ~4°.
BDC_TILE_LEVELS = [
    ("SM", "sm_path",  "BDC Small tile grid  (105.6 km × 105.6 km, ~1°)"),
    ("MD", "md_path",  "BDC Medium tile grid (211.2 km × 211.2 km, ~2°)"),
    ("LG", "lg_path",  "BDC Large tile grid  (422.4 km × 422.4 km, ~4°)"),
]


def bundled_bdc_grid(level: str) -> str:
    """
    Path to the BDC Grid V2 shapefile bundled with DisSCube, as a GDAL
    ``zip://`` URL readable by fiona.

    ``level`` is one of ``"SM"``, ``"MD"`` or ``"LG"``. Provenance, checksums
    and licensing notes are in ``disscube/data/bdc_grids/README.md``.

    Note: the bundled ``.prj`` files carry a non-existent authority code
    (``EPSG:200000``, emitted by the BDC GeoServer), so readers that resolve
    the CRS from the file — e.g. ``geopandas.read_file`` — fail on them. The
    importer does not read the file CRS; it uses :data:`BDC_CRS`, which is the
    same projection.
    """
    level = level.upper()
    if level not in {lvl for lvl, _, _ in BDC_TILE_LEVELS}:
        raise ValueError(f"Unknown BDC grid level {level!r}; expected SM, MD or LG")
    path = files("disscube") / "data" / "bdc_grids" / f"BDC_{level}_V2.zip"
    return f"zip://{path}"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_bdc_grids(
    cube: CubeClient,
    sm_path: str | None = None,
    md_path: str | None = None,
    lg_path: str | None = None,
):
    """
    Import BDC tiles and national simulation grids into the catalog.

    Each path defaults to the BDC Grid V2 shapefile bundled with DisSCube
    (see :func:`bundled_bdc_grid`); pass a path to use another copy.
    """
    register_simulation_grids(cube)
    paths = {
        "sm_path": sm_path or bundled_bdc_grid("SM"),
        "md_path": md_path or bundled_bdc_grid("MD"),
        "lg_path": lg_path or bundled_bdc_grid("LG"),
    }
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
