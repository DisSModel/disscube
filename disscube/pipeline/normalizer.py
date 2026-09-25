import logging

import geopandas as gpd
import rasterio

from disscube.pipeline import PipelineContext, PipelineStage

log = logging.getLogger(__name__)


class Normalizer(PipelineStage):
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        url = ctx.source.asset_url
        fmt = ctx.source.format

        if fmt == "raster":
            # Validate that the file is readable; metadata is loaded lazily
            # by GridAligner to avoid double I/O.
            with rasterio.open(url):
                pass

        elif fmt == "vector":
            gdf = gpd.read_file(url, **ctx.source.read_options)

            if ctx.source.crs:
                from pyproj import CRS as ProjCRS
                from pyproj.exceptions import CRSError
                declared = ctx.source.crs
                file_crs = gdf.crs
                try:
                    crs_match = (
                        file_crs is not None
                        and ProjCRS.from_user_input(file_crs).equals(
                            ProjCRS.from_user_input(declared)
                        )
                    )
                except CRSError:
                    crs_match = False

                if not crs_match:
                    # CRS in the file header is absent or differs from the
                    # registered source CRS.  Override the metadata without
                    # transforming coordinates — the source registration is
                    # the authoritative reference.
                    log.warning(
                        "Normalizer: overriding file CRS (%s) with source CRS (%s) "
                        "for source '%s'.  Coordinates are NOT reprojected.",
                        file_crs,
                        declared,
                        ctx.source.id,
                    )
                    gdf = gdf.set_crs(declared, allow_override=True)

            ctx.data = gdf

        else:
            raise ValueError(f"Unknown source format: {fmt!r}")

        return ctx
