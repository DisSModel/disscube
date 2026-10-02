"""Schema (version 1) of DisSCube pipeline files."""

from __future__ import annotations

from typing import Annotated, Any, Literal

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
    url: str | None = None
    sha256: str | None = None
    """
    A local raster or vector file.

    Without ``url``, ``path`` is a hand-placed file resolved relative to the
    pipeline file itself. With ``url``, ``path`` is just the logical filename
    inside disscube's shared cache (pooch's OS cache directory) — the same
    file works unmodified on any machine or workspace; DisSCube fetches and
    verifies it there (via ``sha256``) once, the first time it's missing, and
    every workspace reuses that one copy afterwards.

    ``variable`` reads one variable of a NetCDF file as a raster. ``nodata``
    declares the raster's no-data value when the file does not. ``read``
    passes options to ``geopandas.read_file`` for a vector file — ``where``
    (an SQL filter on the attributes), ``encoding``, ``layer``, ….
    """

    type: Literal["file"]
    path: str
    crs: str | None = None
    format: Literal["raster", "vector"] | None = None
    time: int | None = None
    variable: str | None = None
    nodata: float | None = None
    archive: Literal["zip"] | None = None
    """
    Declares that ``url`` serves a zip archive to download-once and extract,
    regardless of what the URL string itself looks like. Without this, a zip
    is only detected when ``url`` happens to end in ``.zip`` — which fails
    for share links from Dropbox, Google Drive, institutional data portals,
    etc. that serve a zip through a query-string or token URL. ``path`` is
    still the member to extract (or, if no member matches, the whole archive
    is extracted next to it).
    """
    read: dict[str, Any] = Field(default_factory=dict)


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


class OsmSource(_SourceBase):
    """Ways from OpenStreetMap (Overpass), clipped to the grid plus ``margin``.

    ``query`` holds Overpass statements without the bounding box (``way["highway"]["ref"~"BR-163"]``),
    separated by ``;`` or given as a list. ``margin`` is in degrees: a ``distance`` needs
    features beyond the grid, a ``count`` does not. ``date`` asks for the map as it was on
    that day; with ``years``, ``date = "{year}-07-01"`` gives one snapshot per year. The answer
    is cached, so a pipeline reads the same data until the cache is cleared. Set ``OSM_CONTACT``.
    """

    type: Literal["osm"]
    query: str | list[str]
    margin: float = Field(default=0.0, ge=0)
    date: str | None = None
    endpoints: list[str] | None = None
    cache: str | None = None
    time: int | None = None


class DemSource(_SourceBase):
    """Elevation or slope from a DEM (SRTM, Copernicus GLO-30, TOPODATA), over the grid plus ``margin``.

    Give ``dem`` to download one, or ``tiles`` (GeoTIFFs, paths relative to the pipeline file or URLs
    GDAL can read) to use your own. ``product`` is ``elevation`` (m), ``slope_deg`` or ``slope_pct``;
    a slope is computed on a metric (UTM) grid of ``resolution`` metres. Prefer SRTM or TOPODATA to
    Copernicus for slope: Copernicus includes the forest canopy.
    """

    type: Literal["dem"]
    dem: Literal["srtm", "copernicus", "topodata"] | None = None
    tiles: list[str] | None = None
    product: Literal["elevation", "slope_deg", "slope_pct"] = "elevation"
    margin: float = Field(default=0.02, ge=0)
    resolution: float = Field(default=30.0, gt=0)
    cache: str | None = None

    @model_validator(mode="after")
    def _one_input(self):
        if (self.dem is None) == (self.tiles is None):
            raise ValueError(f"source {self.id!r}: give exactly one of 'dem' or 'tiles'")
        return self


class UnionSource(_SourceBase):
    """The features of several vector sources of this file as one source."""

    type: Literal["union"]
    of: list[str] = Field(min_length=2)


Source = Annotated[
    FileSource | BdcSource | MapbiomasSource | ProdesSource | ClassifiedSource | OsmSource | DemSource | UnionSource,
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
    params: dict[str, Any] = Field(default_factory=dict)
    fill: Literal["nearest"] | None = None


class ExportConfig(_Strict):
    """Optional export configuration declared in pipeline TOML."""

    output: str
    format: Literal["geotiff", "netcdf"] = "geotiff"
    variables: list[str] | None = None


class PipelineConfig(_Strict):
    """A whole pipeline file."""

    schema_version: int = Field(alias="schema")
    name: str | None = None
    workspace: str | None = None
    grid: GridConfig | None = None
    extent: list[float] | None = Field(default=None, min_length=4, max_length=4)
    """``[min_lon, min_lat, max_lon, max_lat]`` (WGS84) a sources-only file reads
    windowed sources over (classified maps, BDC, MapBiomas, PRODES, OpenStreetMap, DEM)."""
    sources_from_catalog: bool = False
    """Let [[derive]] blocks use sources this file does not declare, registered
    in the workspace's catalog by another pipeline file. Off by default, so a
    misspelt source id fails when the file is planned."""
    source: list[Source] = Field(default_factory=list)
    derive: list[DeriveConfig] = Field(default_factory=list)
    export: ExportConfig | str | None = None

    @model_validator(mode="after")
    def _grid_or_extent(self):
        if self.derive and self.grid is None:
            raise ValueError("a file with [[derive]] blocks needs a [grid]")

        # Apenas fontes dinâmicas em janela na nuvem precisam de extent.
        # Fontes 'file' e 'union' já têm extensão definida por seus arquivos locais.
        windowed_types = {"bdc", "mapbiomas", "prodes", "classified", "osm", "dem"}
        needs_extent = any(
            getattr(s, "type", None) in windowed_types for s in self.source
        )

        if self.grid is None and self.extent is None and needs_extent:
            raise ValueError(
                "a sources-only file with windowed sources (BDC, MapBiomas, PRODES, OpenStreetMap, DEM)"
                " needs `extent` (or a [grid])"
            )
        return self

    @field_validator("schema_version")
    @classmethod
    def _known_schema(cls, v: int) -> int:
        if v != 1:
            raise ValueError(
                f"unsupported pipeline schema {v}; this DisSCube reads schema 1"
            )
        return v