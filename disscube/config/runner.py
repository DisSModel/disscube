"""Load, check and run DisSCube pipeline files."""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from disscube.config.schema import (
    BdcSource,
    ClassifiedSource,
    DeriveConfig,
    FileSource,
    GridConfig,
    MapbiomasSource,
    PipelineConfig,
    ProdesSource,
)
from disscube.utils.files import sha256_file

log = logging.getLogger(__name__)

_VECTOR_SUFFIXES = {".shp", ".gpkg", ".geojson", ".json", ".fgb", ".kml"}


class PipelineError(ValueError):
    """A pipeline file that cannot be read or does not make sense."""


# ---------------------------------------------------------------------------
# Load and plan
# ---------------------------------------------------------------------------

@dataclass
class PipelineFile:
    path: Path
    checksum: str
    config: PipelineConfig

    @property
    def base_dir(self) -> Path:
        return self.path.parent


@dataclass
class PlannedSource:
    id: str
    config: FileSource | BdcSource | MapbiomasSource | ProdesSource | ClassifiedSource
    year: int | None = None


@dataclass
class PlannedDerive:
    target: str
    source: str
    operator: str
    class_code: int | None
    role: str


@dataclass
class Plan:
    """A pipeline file with every ``years`` expansion resolved and every reference checked."""

    file: PipelineFile
    grid: GridConfig
    sources: list[PlannedSource] = field(default_factory=list)
    derives: list[PlannedDerive] = field(default_factory=list)

    def summary(self) -> str:
        g = self.grid
        lines = [
            f"pipeline  : {self.file.path.name}  ({self.file.checksum[:19]}…)",
            f"grid      : {g.name}  {g.resolution:g} {'m, BDC Albers' if g.crs is None else g.crs}",
            f"sources   : {len(self.sources)}",
        ]
        lines += [f"  - {s.id:<24} {s.config.type}" for s in self.sources]
        lines.append(f"variables : {len(self.derives)}")
        lines += [f"  - {d.target:<24} {d.operator}"
                  f"{'(' + str(d.class_code) + ')' if d.class_code is not None else ''} <- {d.source}"
                  for d in self.derives]
        return "\n".join(lines)


def load(path: str | Path) -> PipelineFile:
    """Read and validate a pipeline file (no network, no files other than the TOML)."""
    path = Path(path).resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PipelineError(f"{path}: file not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise PipelineError(f"{path.name}: invalid TOML — {exc}") from None
    try:
        config = PipelineConfig.model_validate(data)
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise PipelineError(f"{path.name}: invalid pipeline\n{problems}") from None
    return PipelineFile(path=path, checksum=sha256_file(path), config=config)


def _expand(src, year: int):
    data = src.model_dump(exclude={"years"})
    data = {k: v.replace("{year}", str(year)) if isinstance(v, str) else v for k, v in data.items()}
    if "year" in type(src).model_fields and data.get("year") in (None, str(year)):
        data["year"] = year
    if "time" in type(src).model_fields and data.get("time") is None:
        data["time"] = year
    return type(src).model_validate(data)


def plan(pipeline: PipelineFile | str | Path) -> Plan:
    """Expand ``years``, resolve references and check operators, years and legends."""
    from disscube.derivation import Derivation
    from disscube.sources import mapbiomas

    pf = pipeline if isinstance(pipeline, PipelineFile) else load(pipeline)
    cfg = pf.config
    result = Plan(file=pf, grid=cfg.grid)
    templates: dict[str, list[int] | None] = {}

    for src in cfg.source:
        templates[src.id] = src.years
        concrete = [(_expand(src, y), y) for y in src.years] if src.years else [(src, None)]
        for c, year in concrete:
            if isinstance(c, MapbiomasSource | ProdesSource):
                if not isinstance(c.year, int):
                    raise PipelineError(f"source {c.id!r}: needs 'year' (or 'years' with '{{year}}')")
                year = c.year
            if isinstance(c, MapbiomasSource):
                try:
                    mapbiomas.dataset(c.collection, c.resolution).url(c.year)
                except ValueError as exc:
                    raise PipelineError(f"source {c.id!r}: {exc}") from None
            result.sources.append(PlannedSource(id=c.id, config=c, year=year))

    ids = [s.id for s in result.sources]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PipelineError(f"duplicate source ids: {', '.join(duplicates)}")

    for d in cfg.derive:
        for source_id in _derive_sources(d, templates):
            if source_id not in ids:
                raise PipelineError(f"variable {d.target!r}: unknown source {source_id!r}")
            try:
                Derivation(target=d.target, source_id=source_id, operator=d.operator,
                           class_code=d.class_code, role=d.role)
            except ValidationError as exc:
                raise PipelineError(f"variable {d.target!r}: {exc.errors()[0]['msg']}") from None
            result.derives.append(PlannedDerive(d.target, source_id, d.operator, d.class_code, d.role))
    return result


def _derive_sources(d: DeriveConfig, templates: dict[str, list[int] | None]) -> list[str]:
    if "{year}" not in d.source:
        if d.years:
            raise PipelineError(f"variable {d.target!r}: 'years' requires '{{year}}' in 'source'")
        return [d.source]
    years = d.years or templates.get(d.source)
    if not years:
        raise PipelineError(
            f"variable {d.target!r}: source {d.source!r} has '{{year}}' but no years "
            "(give 'years' here or in the matching source block)"
        )
    return [d.source.replace("{year}", str(y)) for y in years]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    workspace: Path
    grid_id: str
    sources: list[dict] = field(default_factory=list)
    derived: list[dict] = field(default_factory=list)
    record: Path | None = None


def run(pipeline: PipelineFile | Plan | str | Path, workspace: str | Path | None = None) -> RunReport:
    """
    Execute a pipeline file: register the grid, fetch every source, derive every variable.

    The workspace (``workspace`` argument, else the file's ``workspace`` key,
    else a folder named after the file next to it) receives ``catalog.db``,
    ``store/``, ``raw/`` (sources with their provenance) and ``run.json``, a
    record of this run with the pipeline file's checksum.
    """
    from disscube import CubeClient, Derivation

    p = pipeline if isinstance(pipeline, Plan) else plan(pipeline)
    pf, cfg = p.file, p.file.config
    ws = Path(workspace) if workspace else (
        pf.base_dir / cfg.workspace if cfg.workspace else pf.base_dir / pf.path.stem)
    raw = ws / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat(timespec="seconds")

    cube = CubeClient(catalog=str(ws / "catalog.db"), store=str(ws / "store"))
    grid_id, bbox_geo = _register_grid(cube, p.grid)
    report = RunReport(workspace=ws, grid_id=grid_id)
    pipeline_info = {"file": str(pf.path), "checksum": pf.checksum, "name": cfg.name}

    for s in p.sources:
        log.info("source %s (%s)", s.id, s.config.type)
        src = _register_source(cube, s, raw, bbox_geo, pf.base_dir)
        _annotate_provenance(src, pipeline_info)
        report.sources.append({"id": s.id, "type": s.config.type, "file": src.asset_url,
                               "checksum": src.checksum, "time": src.time})

    for d in p.derives:
        derived = cube.derive_declarative(
            Derivation(target=d.target, source_id=d.source, operator=d.operator,
                       class_code=d.class_code, role=d.role),
            grid_id=grid_id,
        )
        for dv in derived:
            report.derived.append({"target": dv.name, "source": d.source, "spec_hash": dv.spec_hash,
                                   "times": dv.times, "file": dv.asset_url})

    record = {
        "pipeline": pipeline_info,
        "started": started,
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "grid": grid_id,
        "sources": report.sources,
        "derived": report.derived,
    }
    report.record = ws / "run.json"
    report.record.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


def _register_grid(cube, g: GridConfig) -> tuple[str, list[float]]:
    from pyproj import Transformer

    from disscube.models import GridSpec
    from disscube.utils.grids import register_local_grid

    if g.crs is None:
        grid = register_local_grid(cube, name=g.name, bbox_geo=tuple(g.bbox), resolution=g.resolution,
                                   snap=g.snap)
        return grid.id, list(g.bbox)
    cube.register_grid(GridSpec(id=g.name, type="local", crs=g.crs, resolution=g.resolution, bbox=g.bbox))
    to_geo = Transformer.from_crs(g.crs, "EPSG:4326", always_xy=True)
    xs, ys = zip(*(to_geo.transform(x, y) for x in (g.bbox[0], g.bbox[2]) for y in (g.bbox[1], g.bbox[3])),
                 strict=True)
    return g.name, [min(xs), min(ys), max(xs), max(ys)]


def _register_source(cube, s: PlannedSource, raw: Path, bbox_geo: list[float], base: Path):
    c = s.config
    if isinstance(c, BdcSource):
        return _register_bdc(cube, c, raw, bbox_geo)
    if isinstance(c, MapbiomasSource):
        from disscube.sources.mapbiomas import register_mapbiomas_source

        return register_mapbiomas_source(cube, c.id, c.year, bbox_geo, raw, collection=c.collection,
                                         resolution=c.resolution, url=c.url, name=c.name)
    if isinstance(c, ProdesSource):
        from disscube.sources import prodes

        cache = (base / c.cache) if c.cache else None
        files = prodes.download(c.url or prodes.DEFAULT_URL, cache)
        return prodes.register_prodes_source(cube, c.id, c.year, bbox_geo, raw, files=files, name=c.name)
    if isinstance(c, ClassifiedSource):
        from disscube.sources.classified import register_classified_map

        legend = c.legend
        if isinstance(legend, str):
            legend = base / legend
        return register_classified_map(cube, c.id, _resolve(base, c.path), bbox_geo, raw, legend=legend,
                                       time=c.time, nodata=c.nodata, producer=c.producer, name=c.name)
    return _register_file(cube, c, base, raw)


def _register_bdc(cube, c: BdcSource, raw: Path, bbox_geo: list[float]):
    from disscube.sources import normalized_difference, register_raster
    from disscube.sources.bdc import (
        BDC_STAC_URL,
        items_provenance,
        period_year,
        read_composite,
        register_bdc_source,
        search_items,
    )

    url = c.url or BDC_STAC_URL
    time = c.time if c.time is not None else period_year(c.period)
    if c.asset:
        return register_bdc_source(cube, c.id, c.collection, c.asset, bbox_geo, c.period, raw,
                                   reducer=c.reducer, scale=c.scale, offset=c.offset, url=url,
                                   name=c.name, time=time)
    a, b = c.normalized_difference
    items = search_items(c.collection, bbox_geo, c.period, url=url)
    layers = [read_composite(c.collection, asset, bbox_geo, c.period, reducer=c.reducer, url=url,
                             items=items, scale=c.scale, offset=c.offset) for asset in (a, b)]
    provenance = {"stac_url": url, "collection": c.collection, "assets": [a, b],
                  "expression": f"({a} - {b}) / ({a} + {b})", "bbox_geo": bbox_geo,
                  "period": c.period, "reducer": c.reducer, "scale": c.scale, "offset": c.offset,
                  "items": items_provenance(items)}
    return register_raster(cube, c.id, normalized_difference(*layers), raw, provenance,
                           name=c.name or f"{c.collection} ({a} - {b}) / ({a} + {b}), {c.period}",
                           time=time, tags=["bdc", f"collection:{c.collection}", f"period:{c.period}"])


def _register_file(cube, c: FileSource, base: Path, raw: Path):
    from disscube.models import SpatialSource

    path = _resolve(base, c.path)
    local = _local_file(path)
    if local is None or not local.exists():
        raise PipelineError(f"source {c.id!r}: file not found: {path}")
    fmt = c.format or ("vector" if path.startswith("zip://")
                       or local.suffix.lower() in _VECTOR_SUFFIXES else "raster")
    crs = c.crs or _file_crs(path, fmt)
    checksum = sha256_file(local)
    prov_path = raw / f"{c.id}.provenance.json"
    prov_path.write_text(json.dumps({"type": "file", "path": str(path), "format": fmt, "crs": crs,
                                     "checksum": checksum}, indent=2, ensure_ascii=False), encoding="utf-8")
    src = SpatialSource(id=c.id, name=c.name or c.id, format=fmt, asset_url=str(path), crs=crs,
                        time=c.time, checksum=checksum, tags=["file", f"provenance:{prov_path}"])
    cube.register_spatial_source(src)
    return src


def _file_crs(path: str, fmt: str) -> str:
    if fmt == "raster":
        import rasterio

        with rasterio.open(path) as ds:
            if ds.crs is None:
                raise PipelineError(f"{path}: no CRS in the file; set 'crs' in the source block")
            return ds.crs.to_string()
    import geopandas as gpd

    crs = gpd.read_file(path, rows=1).crs
    if crs is None:
        raise PipelineError(f"{path}: no CRS in the file; set 'crs' in the source block")
    return crs.to_string()


def _resolve(base: Path, path: str) -> str:
    """Resolve ``path`` against the pipeline file's folder; ``zip://`` paths too, URLs untouched."""
    if path.startswith("zip://"):
        return "zip://" + _resolve(base, path[len("zip://"):])
    if "://" in path:
        return path
    p = Path(path)
    return str(p if p.is_absolute() else (base / p).resolve())


def _local_file(path: str) -> Path | None:
    """The file on disk behind ``path`` (the archive for ``zip://…``), or None for URLs."""
    if path.startswith("zip://"):
        return Path(path[len("zip://"):].split("!")[0])
    return None if "://" in path else Path(path)


def _annotate_provenance(src, info: dict) -> None:
    for tag in src.tags:
        if tag.startswith("provenance:"):
            path = Path(tag.split(":", 1)[1])
            record = json.loads(path.read_text(encoding="utf-8"))
            record["pipeline"] = info
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
