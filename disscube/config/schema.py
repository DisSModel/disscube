"""Schema (version 1) of DisSCube pipeline files."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GridConfig(_Strict):
    """
    The target grid.

    Without ``crs`` the grid is a local grid in BDC Albers snapped to the
    national mesh, and ``bbox`` is ``[min_lon, min_lat, max_lon, max_lat]`` in
    WGS84 (``disscube.utils.grids.register_local_grid``). With ``crs`` the grid
    is taken as given and ``bbox`` is in that CRS.
    """

    name: str
    bbox: list[float] = Field(min_length=4, max_length=4)
    resolution: float = Field(gt=0)
    crs: str | None = None
    snap: bool = True


class _SourceBase(_Strict):
    id: str
    name: str | None = None
    years: list[int] | None = None
    """Expand this block once per year; every ``{year}`` in its strings is replaced."""

    @model_validator(mode="after")
    def _years_need_placeholder(self):
        if self.years is not None and "{year}" not in self.id:
            raise ValueError(f"source {self.id!r}: 'years' requires '{{year}}' in the id")
        return self


class FileSource(_SourceBase):
    """A local raster or vector file (path relative to the pipeline file)."""

    type: Literal["file"]
    path: str
    crs: str | None = None
    format: Literal["raster", "vector"] | None = None
    time: int | None = None


class BdcSource(_SourceBase):
    """A Brazil Data Cube composite (one asset, or a normalized difference of two)."""

    type: Literal["bdc"]
    collection: str
    asset: str | None = None
    normalized_difference: list[str] | None = Field(default=None, min_length=2, max_length=2)
    period: str
    reducer: Literal["median", "mean", "max", "min"] = "median"
    scale: float | None = None
    offset: float | None = None
    url: str | None = None
    time: int | None = None

    @model_validator(mode="after")
    def _one_input(self):
        if (self.asset is None) == (self.normalized_difference is None):
            raise ValueError(f"source {self.id!r}: give exactly one of 'asset' or 'normalized_difference'")
        return self


class MapbiomasSource(_SourceBase):
    """One year of a MapBiomas land-cover collection."""

    type: Literal["mapbiomas"]
    year: int | str | None = None
    collection: int = 11
    resolution: int = 30
    url: str | None = None


class ProdesSource(_SourceBase):
    """PRODES forest / deforested / other at the end of a year."""

    type: Literal["prodes"]
    year: int | str | None = None
    url: str | None = None
    cache: str | None = None


class ClassifiedSource(_SourceBase):
    """Any classified map (e.g. from SITS) with its legend."""

    type: Literal["classified"]
    path: str
    legend: dict[str, str] | str | None = None
    time: int | None = None
    nodata: float | None = None
    producer: str | None = None


Source = Annotated[
    FileSource | BdcSource | MapbiomasSource | ProdesSource | ClassifiedSource,
    Field(discriminator="type"),
]


class DeriveConfig(_Strict):
    """One derived variable: ``operator`` applied to ``source`` on the grid."""

    target: str
    source: str
    operator: str
    class_code: int | None = None
    role: str = "driver"
    years: list[int] | None = None


class PipelineConfig(_Strict):
    """A whole pipeline file."""

    schema_version: int = Field(alias="schema")
    name: str | None = None
    workspace: str | None = None
    grid: GridConfig
    source: list[Source] = Field(default_factory=list)
    derive: list[DeriveConfig] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _known_schema(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported pipeline schema {v}; this DisSCube reads schema 1")
        return v
