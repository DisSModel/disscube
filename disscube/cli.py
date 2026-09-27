"""
Command line: run DisSCube pipeline files.

    disscube validate pipeline.toml
    disscube run pipeline.toml [--workspace DIR] [-v]
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

    p_fetch = sub.add_parser("fetch", help="download and verify remote file sources declared in the pipeline")
    p_fetch.add_argument("file")

    p_run = sub.add_parser("run", help="fetch the sources and derive the variables")
    p_run.add_argument("file")
    p_run.add_argument("--workspace", help="output folder (default: the file's 'workspace', "
                                           "else a folder named after the file)")
    p_run.add_argument("-v", "--verbose", action="store_true", help="log each step")

    args = parser.parse_args(argv)
    from disscube.config import plan, run
    from disscube.config.runner import PipelineError

    logging.basicConfig(level=logging.INFO if getattr(args, "verbose", False) else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        p = plan(args.file)
        print(p.summary())
        if args.command == "fetch":
            from disscube.config.runner import _fetch_file_source, _local_file, _resolve
            from disscube.config.schema import FileSource
            fetched = 0
            for s in p.sources:
                if isinstance(s.config, FileSource) and getattr(s.config, 'url', None):
                    target = _local_file(_resolve(p.file.base_dir, s.config.path))
                    if target is not None:
                        print(f"Fetching {s.id} -> {target.name}...")
                        _fetch_file_source(s.config, target)
                        fetched += 1
            print(f"Fetch completed: {fetched} remote sources verified.")
            return 0
        if args.command == "validate":
            print("OK")
            return 0
        report = run(p, workspace=args.workspace)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"workspace : {report.workspace}")
    print(f"derived   : {len(report.derived)} products on {report.grid_id}")
    print(f"record    : {report.record}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
