"""
Command line: run DisSCube pipeline files.

    disscube validate pipeline.toml [--json]
    disscube fetch pipeline.toml
    disscube run pipeline.toml [--workspace DIR] [--output OUT.tif] [--dry-run] [--json] [-v]
    disscube export pipeline.toml --output OUT.tif [--workspace DIR] [--variables ...] [--json] [-v]
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="disscube", description="Run DisSCube pipeline files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="check a pipeline file without fetching anything")
    p_val.add_argument("file")
    p_val.add_argument("--json", action="store_true", help="output validation result as JSON")

    p_fetch = sub.add_parser("fetch", help="download and verify remote file sources declared in the pipeline")
    p_fetch.add_argument("file")

    p_run = sub.add_parser("run", help="fetch the sources and derive the variables")
    p_run.add_argument("file")
    p_run.add_argument("--workspace", help="output folder (default: the file's 'workspace', "
                                           "else a folder named after the file)")
    p_run.add_argument("--output", "-o", help="export derived variables to a multi-band GeoTIFF")
    p_run.add_argument("--dry-run", action="store_true", help="simulate plan execution without downloading or computing")
    p_run.add_argument("--json", action="store_true", help="output execution report as JSON")
    p_run.add_argument("-v", "--verbose", action="store_true", help="log each step")

    p_exp = sub.add_parser("export", help="export derived variables from an existing data cube to GeoTIFF")
    p_exp.add_argument("file", help="pipeline TOML file")
    p_exp.add_argument("--output", "-o", required=True, help="output GeoTIFF file path (e.g. data/cellspace.tif)")
    p_exp.add_argument("--workspace", help="workspace folder (default: data/cube or from pipeline)")
    p_exp.add_argument("--variables", nargs="*", help="specific variables to export (default: all derived)")
    p_exp.add_argument("--json", action="store_true", help="output export result as JSON")
    p_exp.add_argument("-v", "--verbose", action="store_true", help="log each step")

    args = parser.parse_args(argv)
    from disscube.config import plan, run
    from disscube.config.runner import PipelineError

    logging.basicConfig(level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    is_json = getattr(args, "json", False)
    try:
        p = plan(args.file)
        if not is_json:
            print(p.summary())
        if args.command == "fetch":
            from disscube.config.runner import _fetch_file_source, _local_file, _raw_cache_dir, _resolve
            from disscube.config.schema import FileSource
            raw = _raw_cache_dir()
            raw.mkdir(parents=True, exist_ok=True)
            fetched = 0
            for s in p.sources:
                if isinstance(s.config, FileSource) and getattr(s.config, 'url', None):
                    target = _local_file(_resolve(raw, s.config.path))
                    if target is not None:
                        print(f"Fetching {s.id} -> {target.name}...")
                        _fetch_file_source(s.config, target)
                        fetched += 1
            print(f"Fetch completed: {fetched} remote sources verified.")
            return 0
        if args.command == "validate":
            if is_json:
                import json
                print(json.dumps({
                    "status": "ok",
                    "file": str(p.file.path),
                    "name": p.file.config.name,
                    "grid": p.grid.name if p.grid else None,
                    "sources": [s.id for s in p.sources],
                    "derives": [d.target for d in p.derives],
                }, indent=2))
            else:
                print("OK")
            return 0
        if getattr(args, "dry_run", False):
            if is_json:
                import json
                print(json.dumps({
                    "status": "ok",
                    "dry_run": True,
                    "file": str(p.file.path),
                    "grid": p.grid.name if p.grid else None,
                    "sources": [{"id": s.id, "type": s.config.type} for s in p.sources],
                    "derives": [{"target": d.target, "source": d.source, "operator": d.operator} for d in p.derives],
                }, indent=2))
            else:
                print(f"[dry-run] Plan is valid. Would process {len(p.sources)} sources and derive {len(p.derives)} variables.")
            return 0
        if args.command == "export":
            from disscube.config.runner import export_cube
            exp = export_cube(p, output=args.output, workspace=args.workspace, variables=args.variables)
            if is_json:
                import json
                print(json.dumps({
                    "status": "ok",
                    "workspace": str(exp.workspace),
                    "grid_id": exp.grid_id,
                    "variables": exp.variables,
                    "output": str(exp.output),
                }, indent=2))
            else:
                print(f"workspace : {exp.workspace}")
                print(f"exported  : {len(exp.variables)} variables on {exp.grid_id}")
                print(f"output    : {exp.output}")
            return 0
        report = run(p, workspace=args.workspace, export_geotiff=getattr(args, "output", None))
        if is_json:
            import json
            print(json.dumps({
                "status": "ok",
                "workspace": str(report.workspace),
                "grid_id": report.grid_id,
                "sources": report.sources,
                "derived": report.derived,
                "exported": str(report.exported) if report.exported else None,
                "record": str(report.record),
            }, indent=2, default=str))
            return 0
    except PipelineError as exc:
        if is_json:
            import json
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"workspace : {report.workspace}")
    print(f"derived   : {len(report.derived)} products on {report.grid_id}")
    if report.exported:
        print(f"exported  : {report.exported}")
    print(f"record    : {report.record}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())