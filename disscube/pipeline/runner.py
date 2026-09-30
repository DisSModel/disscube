"""Load, check and run DisSCube pipeline files."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import ValidationError

from disscube.pipeline.schema import (
    BdcSource,
    ClassifiedSource,
    DeriveConfig,
    ExportConfig,
    FileSource,
    GridConfig,
    MapbiomasSource,
    PipelineConfig,
    ProdesSource,
    UnionSource,
)
from disscube.utils import sha256_file

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
    config: FileSource | BdcSource | MapbiomasSource | ProdesSource | ClassifiedSource | UnionSource
    year: int | None = None


@dataclass
class PlannedDerive:
    target: str
    source: str
    operator: str
    class_code: int | None
    role: str
    params: dict = field(default_factory=dict)
    fill: str | None = None

    def derivation(self):
        from disscube.models import Derivation

        return Derivation(target=self.target, source_id=self.source, operator=self.operator,
                          class_code=self.class_code, role=self.role, params=self.params, fill=self.fill)


@dataclass
class Plan:
    """A pipeline file with every ``years`` expansion resolved and every reference checked."""

    file: PipelineFile
    grid: GridConfig | None
    sources: list[PlannedSource] = field(default_factory=list)
    derives: list[PlannedDerive] = field(default_factory=list)
    catalog_sources: list[str] = field(default_factory=list)
    """Sources the derivations use that this file does not declare: they must
    already be in the workspace's catalog (registered by another pipeline file)."""

    def summary(self) -> str:
        g = self.grid
        lines = [
            f"pipeline  : {self.file.path.name}  ({self.file.checksum[:19]}…)",
            f"grid      : {g.name}  {g.resolution:g} {'m, BDC Albers' if g.crs is None else g.crs}"
            if g is not None else f"extent    : {self.file.config.extent} (sources only)",
            f"sources   : {len(self.sources)}",
        ]
        lines += [f"  - {s.id:<24} {s.config.type}" for s in self.sources]
        if self.catalog_sources:
            lines.append(f"from the workspace catalog: {', '.join(self.catalog_sources)}")
        lines.append(f"variables : {len(self.derives)}")
        lines += [f"  - {d.target:<24} {d.operator}"
                  f"{'(' + str(d.class_code) + ')' if d.class_code is not None else ''}"
                  f"{' ' + str(d.params) if d.params else ''}"
                  f"{' fill ' + d.fill if d.fill else ''} <- {d.source}"
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


def _year_of(c: MapbiomasSource | ProdesSource) -> int:
    """The concrete year of a MapBiomas/PRODES source (``years`` expansion already applied)."""
    if not isinstance(c.year, int):
        raise PipelineError(f"source {c.id!r}: needs 'year' (or 'years' with '{{year}}')")
    return c.year


def plan(pipeline: PipelineFile | str | Path) -> Plan:
    """Expand ``years``, resolve references and check operators, years and legends."""
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
                year = _year_of(c)
            if isinstance(c, MapbiomasSource):
                try:
                    mapbiomas.dataset(c.collection, c.resolution).url(_year_of(c))
                except ValueError as exc:
                    raise PipelineError(f"source {c.id!r}: {exc}") from None
            if isinstance(c, UnionSource):
                _check_union(c, result.sources)
            result.sources.append(PlannedSource(id=c.id, config=c, year=year))

    ids = [s.id for s in result.sources]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PipelineError(f"duplicate source ids: {', '.join(duplicates)}")

    for d in cfg.derive:
        for source_id in _derive_sources(d, templates):
            if source_id not in ids:
                if cfg.sources_from_catalog:
                    if source_id not in result.catalog_sources:
                        result.catalog_sources.append(source_id)
                else:
                    raise PipelineError(f"variable {d.target!r}: unknown source {source_id!r}")
            planned = PlannedDerive(d.target, source_id, d.operator, d.class_code, d.role,
                                    params=d.params, fill=d.fill)
            try:
                planned.derivation()
            except ValidationError as exc:
                raise PipelineError(f"variable {d.target!r}: {exc.errors()[0]['msg']}") from None
            result.derives.append(planned)
    return result


def _check_union(c: UnionSource, earlier: list[PlannedSource]) -> None:
    """A union joins vector sources declared before it."""
    by_id = {s.id: s.config for s in earlier}
    for part in c.of:
        if part not in by_id:
            raise PipelineError(f"source {c.id!r}: {part!r} must be a source declared before it")
        cfg = by_id[part]
        vector = isinstance(cfg, UnionSource) or (
            isinstance(cfg, FileSource) and _file_format(cfg.path, cfg.format, cfg.variable) == "vector")
        if not vector:
            raise PipelineError(f"source {c.id!r}: {part!r} is not a vector file")


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
# Run & Export
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    workspace: Path
    grid_id: str | None
    sources: list[dict] = field(default_factory=list)
    derived: list[dict] = field(default_factory=list)
    record: Path | None = None
    exported: Path | None = None


@dataclass
class ExportReport:
    workspace: Path
    output: Path
    variables: list[str]
    grid_id: str


def _export(cube, variables: list[str], out_path: Path, grid_id: str, fmt: str | None = None) -> None:
    """Write ``variables`` to ``out_path``.

    ``fmt`` (``"geotiff"`` or ``"netcdf"``) wins when the caller gives one; otherwise
    a ``.nc`` suffix means netCDF and anything else GeoTIFF.
    """
    if fmt == "netcdf" or (fmt is None and out_path.suffix.lower() in (".nc", ".nc4", ".cdf")):
        cube.export_netcdf(variables, out_path, grid_id=grid_id)
        log.info("exported netCDF to %s (%d variables)", out_path, len(variables))
    else:
        cube.export_geotiff(variables, out_path, grid_id=grid_id)
        log.info("exported GeoTIFF to %s (%d variables)", out_path, len(variables))


def resolve_workspace(p: Plan, workspace: str | Path | None = None) -> Path:
    """
    The workspace folder for a plan: the explicit ``workspace`` argument, else the
    pipeline's own ``workspace =`` setting (relative to the pipeline file), else a
    folder named after the pipeline file itself. Shared by ``run()``, ``export_cube()``
    and the ``fetch`` CLI command so they always agree on where ``raw/`` lives.
    """
    pf, cfg = p.file, p.file.config
    if workspace:
        return Path(workspace)
    return pf.base_dir / cfg.workspace if cfg.workspace else pf.base_dir / pf.path.stem


def run(pipeline: PipelineFile | Plan | str | Path, workspace: str | Path | None = None,
        export_geotiff: str | Path | None = None) -> RunReport:
    """
    Execute a pipeline file: register the grid, fetch every source, derive every variable.

    The workspace receives ``catalog.db``, ``store/``, ``raw/`` and ``run.json``.
    """
    from disscube import CubeClient

    p = pipeline if isinstance(pipeline, Plan) else plan(pipeline)
    pf, cfg = p.file, p.file.config
    ws = resolve_workspace(p, workspace)
    raw = ws / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat(timespec="seconds")

    cube = CubeClient(catalog=str(ws / "catalog.db"), store=str(ws / "store"))
    if p.grid is not None:
        grid_id, bbox_geo = _register_grid(cube, p.grid)
    elif cfg.extent is not None:
        grid_id, bbox_geo = None, list(cfg.extent)
    else:
        grid_id, bbox_geo = None, None
    missing = [sid for sid in p.catalog_sources if cube.catalog.get_spatial_source(sid) is None]
    if missing:
        raise PipelineError(
            f"sources not declared in {pf.path.name} nor in the catalog of {ws}: {', '.join(missing)} "
            "— run the pipeline file that registers them first, with the same workspace")
    report = RunReport(workspace=ws, grid_id=grid_id)
    pipeline_info = {"file": str(pf.path), "checksum": pf.checksum, "name": cfg.name}

    for s in p.sources:
        log.info("source %s (%s)", s.id, s.config.type)
        src = _register_source(cube, s, raw, bbox_geo, pf.base_dir)
        _annotate_provenance(src, pipeline_info)
        report.sources.append({"id": s.id, "type": s.config.type, "file": src.asset_url,
                               "checksum": src.checksum, "time": src.time})

    for d in p.derives:
        if grid_id is None:  # unreachable: PipelineConfig requires a [grid] for [[derive]] blocks
            raise PipelineError("cannot derive: pipeline does not define a [grid]")
        derived = cube.derive_declarative(d.derivation(), grid_id=grid_id)
        for dv in derived:
            report.derived.append({"target": dv.name, "source": d.source, "spec_hash": dv.spec_hash,
                                   "times": dv.times, "file": dv.asset_url})

    # Resolve export output from parameter or TOML config
    export = cfg.export
    target_export = export_geotiff
    if target_export is None and export:
        target_export = export.output if isinstance(export, ExportConfig) else str(export)

    if target_export and p.derives and p.grid is not None and grid_id is not None:
        vars_to_export = [d.target for d in p.derives]
        if isinstance(export, ExportConfig) and export.variables:
            vars_to_export = export.variables

        out_path = Path(target_export)
        fmt = export.format if isinstance(export, ExportConfig) and export_geotiff is None else None
        _export(cube, vars_to_export, out_path, grid_id, fmt)
        report.exported = out_path

    record = {
        "pipeline": pipeline_info,
        "started": started,
        "finished": datetime.now(UTC).isoformat(timespec="seconds"),
        "grid": grid_id,
        "sources": report.sources,
        "derived": report.derived,
    }
    if report.exported:
        record["exported"] = str(report.exported)
    report.record = ws / "run.json"
    (ws / "runs").mkdir(exist_ok=True)
    (ws / "runs" / f"{pf.path.stem}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report.record.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


def export_cube(pipeline: PipelineFile | Plan | str | Path,
                output: str | Path,
                workspace: str | Path | None = None,
                variables: list[str] | None = None) -> ExportReport:
    """Export derived variables from an existing data cube workspace (GeoTIFF, or netCDF for ``.nc`` outputs)."""
    from disscube import CubeClient

    p = pipeline if isinstance(pipeline, Plan) else plan(pipeline)
    cfg = p.file.config
    ws = resolve_workspace(p, workspace)

    if p.grid is None:
        raise PipelineError("cannot export: pipeline does not define a [grid]")

    grid_id = p.grid.name
    cube = CubeClient(catalog=str(ws / "catalog.db"), store=str(ws / "store"))

    target_vars = variables
    if not target_vars and isinstance(cfg.export, ExportConfig) and cfg.export.variables:
        target_vars = cfg.export.variables
    if not target_vars:
        target_vars = [d.target for d in p.derives]

    if not target_vars:
        raise PipelineError("no variables found to export (pass --variables or declare [[derive]] in pipeline)")

    out_path = Path(output)
    _export(cube, target_vars, out_path, grid_id)
    return ExportReport(workspace=ws, output=out_path, variables=target_vars, grid_id=grid_id)


def _register_grid(cube, g: GridConfig) -> tuple[str, list[float]]:
    from pyproj import Transformer

    from disscube.models import GridSpec
    from disscube.utils import register_local_grid

    min_x, min_y, max_x, max_y = g.bbox
    if g.crs is None:
        grid = register_local_grid(cube, name=g.name, bbox_geo=(min_x, min_y, max_x, max_y),
                                   resolution=g.resolution, snap=g.snap)
        return grid.id, list(g.bbox)
    cube.register_grid(GridSpec(id=g.name, type="local", crs=g.crs, resolution=g.resolution, bbox=g.bbox))
    to_geo = Transformer.from_crs(g.crs, "EPSG:4326", always_xy=True)

    xs, ys = zip(*(to_geo.transform(x, y) for x in (min_x, max_x) for y in (min_y, max_y)))

    return g.name, [min(xs), min(ys), max(xs), max(ys)]


def _register_source(
    cube, s: PlannedSource, raw: Path, bbox_geo: list[float] | None, base: Path
):
    c = s.config
    # ── Validação defensiva: apenas fontes dinâmicas em janela na nuvem precisam de bbox_geo ──
    if isinstance(c, (BdcSource, MapbiomasSource, ProdesSource, ClassifiedSource)):
        if bbox_geo is None:
            raise PipelineError(
                f"source {c.id!r} ({c.type}): windowed cloud sources require an"
                " `extent` (or a [grid]) in the pipeline"
            )
        if isinstance(c, BdcSource):
            return _register_bdc(cube, c, raw, bbox_geo)
        if isinstance(c, MapbiomasSource):
            from disscube.sources.mapbiomas import register_mapbiomas_source

            return register_mapbiomas_source(
                cube,
                c.id,
                _year_of(c),
                bbox_geo,
                raw,
                collection=c.collection,
                resolution=c.resolution,
                url=c.url,
                name=c.name,
            )
        if isinstance(c, ProdesSource):
            from disscube.sources import prodes

            cache = (base / c.cache) if c.cache else None
            files = prodes.download(c.url or prodes.DEFAULT_URL, cache)
            return prodes.register_prodes_source(
                cube,
                c.id,
                _year_of(c),
                bbox_geo,
                raw,
                files=files,
                name=c.name,
            )
        if isinstance(c, ClassifiedSource):
            from disscube.sources.classified import register_classified_map

            legend = base / c.legend if isinstance(c.legend, str) else c.legend
            return register_classified_map(
                cube,
                c.id,
                _resolve(base, c.path),
                bbox_geo,
                raw,
                legend=legend,
                time=c.time,
                nodata=c.nodata,
                producer=c.producer,
                name=c.name,
            )
    if isinstance(c, UnionSource):
        return _register_union(cube, c, raw)
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
    if c.normalized_difference is None:  # unreachable: BdcSource validates asset xor normalized_difference
        raise PipelineError(f"source {c.id!r}: give exactly one of 'asset' or 'normalized_difference'")
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


def _file_format(path: str, declared: Literal["raster", "vector"] | None, variable: str | None) -> Literal["raster", "vector"]:
    if declared:
        return declared
    if variable:
        return "raster"
    local = _local_file(path)
    suffix = local.suffix.lower() if local is not None else ""
    return "vector" if path.startswith("zip://") or suffix in _VECTOR_SUFFIXES else "raster"


def _fetch_file_source(c: FileSource, target_path: Path) -> Path:
    import zipfile

    import pooch

    target_path.parent.mkdir(parents=True, exist_ok=True)

    
    token = os.environ.get("GITHUB_TOKEN")
    downloader = None
    if token and c.url and "api.github.com" in c.url:
        downloader = pooch.HTTPDownloader(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/octet-stream",
            }
        )
    else:
        downloader = pooch.HTTPDownloader(headers={"User-Agent": "disscube"})

    known_hash = f"sha256:{c.sha256}" if c.sha256 and not c.sha256.startswith("sha256:") else c.sha256

    is_zip = c.archive == "zip" or (c.url and c.url.endswith(".zip"))
    if c.url and is_zip and not target_path.name.endswith(".zip"):
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path

        zip_fname = c.url.split("/")[-1].split("?")[0]
        downloaded_zip = pooch.retrieve(
            url=c.url,
            known_hash=known_hash,
            path=target_path.parent,
            fname=zip_fname,
            downloader=downloader,
        )
        with zipfile.ZipFile(downloaded_zip, "r") as zf:
            target_stem = target_path.stem
            extracted = False
            for member in zf.infolist():
                if member.is_dir():
                    continue
                mem_path = Path(member.filename)
                if mem_path.name == target_path.name or len(zf.namelist()) == 1:
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        dst.write(src.read())
                    extracted = True
                elif target_path.suffix.lower() == ".shp" and mem_path.stem == target_stem:
                    with zf.open(member) as src, open(target_path.parent / mem_path.name, "wb") as dst:
                        dst.write(src.read())
            if not extracted:
                zf.extractall(target_path.parent)
        return target_path

    downloaded = pooch.retrieve(
        url=c.url,
        known_hash=known_hash,
        path=target_path.parent,
        fname=target_path.name,
        downloader=downloader,
    )
    return Path(downloaded)


def _raw_cache_dir() -> Path:
    """Onde as fontes remotas (com url) são baixadas e cacheadas via Pooch."""
    import pooch

    # Permite sobrescrever via variável de ambiente (útil para apontar para outro disco)
    env_cache = os.environ.get("DISSCUBE_CACHE_DIR") or os.environ.get(
        "DISSCUBE_CACHE"
    )
    if env_cache:
        return Path(env_cache) / "raw"

    # Padrão oficial: ~/.cache/disscube/raw
    return Path(pooch.os_cache("disscube")) / "raw"


def _register_file(cube, c: FileSource, base: Path, raw: Path):
    from disscube.models import SpatialSource

    # A remote source's `path` is just the logical filename inside disscube's
    # shared pooch cache — portable across machines/workspaces, and downloaded
    # only once regardless of how many workspaces read it. A purely local
    # source (no `url`) still resolves against the pipeline file's own folder,
    # since it's a hand-placed file, not a download.
    cache_root = _raw_cache_dir() if c.url else base
    path = _resolve(cache_root, c.path)
    local = _local_file(path)
    if (local is None or not local.exists()) and getattr(c, 'url', None):
        local = _fetch_file_source(c, local or (cache_root / c.path))
    if local is None or not local.exists():
        raise PipelineError(f"source {c.id!r}: file not found: {path}")
    fmt = _file_format(path, c.format, c.variable)
    if c.variable and fmt != "raster":
        raise PipelineError(f"source {c.id!r}: 'variable' reads a NetCDF variable as a raster")
    if c.read and fmt != "vector":
        raise PipelineError(f"source {c.id!r}: 'read' options apply to vector files")
    if c.nodata is not None and fmt != "raster":
        raise PipelineError(f"source {c.id!r}: 'nodata' applies to raster files")
    url = f'NETCDF:"{path}":{c.variable}' if c.variable else path
    crs = c.crs or _file_crs(url, fmt, c.read)
    checksum = sha256_file(local)
    prov_path = raw / f"{c.id}.provenance.json"
    provenance: dict[str, Any] = {"type": "file", "path": str(path), "format": fmt, "crs": crs, "checksum": checksum}
    provenance |= {k: v for k, v in (("variable", c.variable), ("nodata", c.nodata), ("read", c.read)) if v}
    prov_path.write_text(json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    src = SpatialSource(id=c.id, name=c.name or c.id, format=fmt, asset_url=url, crs=crs,
                        time=c.time, checksum=checksum, tags=["file", f"provenance:{prov_path}"],
                        read_options=c.read, nodata=c.nodata)
    cube.register_spatial_source(src)
    return src


def _register_union(cube, c: UnionSource, raw: Path):
    """Write the features of ``c.of`` to one GeoPackage (in the first part's CRS) and register it."""
    import geopandas as gpd
    import pandas as pd

    from disscube.models import SpatialSource

    parts = [cube.catalog.get_spatial_source(pid) for pid in c.of]
    frames = [gpd.read_file(p.asset_url, **p.read_options) for p in parts]
    crs = frames[0].crs
    geoms = pd.concat([f.geometry.to_crs(crs) for f in frames], ignore_index=True)
    out = raw / f"{c.id}.gpkg"
    gpd.GeoDataFrame({"part": np.repeat(c.of, [len(f) for f in frames])}, geometry=geoms, crs=crs
                     ).to_file(out, driver="GPKG")
    checksum = "sha256:" + hashlib.sha256(json.dumps([p.fingerprint() for p in parts]).encode()).hexdigest()
    prov_path = raw / f"{c.id}.provenance.json"
    prov_path.write_text(json.dumps({"type": "union", "of": c.of, "features": [len(f) for f in frames],
                                     "file": str(out), "checksum": checksum}, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    src = SpatialSource(id=c.id, name=c.name or c.id, format="vector", asset_url=str(out),
                        crs=crs.to_string(), checksum=checksum,
                        tags=["union", f"provenance:{prov_path}"])
    cube.register_spatial_source(src)
    return src


def _file_crs(path: str, fmt: str, read: dict | None = None) -> str:
    if fmt == "raster":
        import rasterio

        with rasterio.open(path) as ds:
            if ds.crs is None:
                raise PipelineError(f"{path}: no CRS in the file; set 'crs' in the source block")
            return ds.crs.to_string()
    import geopandas as gpd

    options = {k: v for k, v in (read or {}).items() if k in ("layer", "encoding")}
    crs = gpd.read_file(path, rows=1, **options).crs
    if crs is None:
        raise PipelineError(f"{path}: no CRS in the file; set 'crs' in the source block")
    return crs.to_string()


def _resolve(base: Path, path: str) -> str:
    if path.startswith("zip://"):
        return "zip://" + _resolve(base, path[len("zip://"):])
    if "://" in path:
        return path
    p = Path(path)
    return str(p if p.is_absolute() else (base / p).resolve())


def _local_file(path: str) -> Path | None:
    if path.startswith("zip://"):
        return Path(path[len("zip://"):].split("!")[0])
    return None if "://" in path else Path(path)


def _annotate_provenance(src, info: dict) -> None:
    for tag in src.tags:
        if tag.startswith("provenance:"):
            _, prov_path = tag.split(":", 1)
            path = Path(prov_path)
            record = json.loads(path.read_text(encoding="utf-8"))
            record["pipeline"] = info
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")