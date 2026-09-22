from typing import Any

from pydantic import BaseModel, ConfigDict

from disscube.models import GridSpec, SpatialDerivation, SpatialSource


class PipelineContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    source: SpatialSource
    grid: GridSpec
    derivation: SpatialDerivation
    tile_id: str | None = None
    data: Any = None 
    # Current data in the pipeline.
    # Contract:
    # - For 'vector' format: populated by Normalizer as a geopandas.GeoDataFrame.
    # - For 'raster' format: remains None after Normalizer (lazy loading); 
    #   GridAligner is responsible for loading and aligning the raster.

class PipelineStage:
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        raise NotImplementedError
