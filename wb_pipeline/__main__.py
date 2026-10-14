"""Command line: python -m wb_pipeline run | rebuild [--load-id ID] | loads | reconcile"""

from __future__ import annotations

import argparse
import logging
import sys

from wb_pipeline.config import ConfigError, load_config
from wb_pipeline.pipeline import REPO_ROOT, Paths, PipelineError, list_loads, rebuild, run
from wb_pipeline.reconcile import format_checks, reconcile

log = logging.getLogger("wb_pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wb_pipeline", description="World Bank indicators pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="extract and load from the API, dbt build, export CSVs")
    run_cmd.add_argument("--config", default=str(REPO_ROOT / "config" / "pipeline.yml"), help="pipeline config file")
    rebuild_cmd = commands.add_parser("rebuild", help="dbt build and export from a stored load, no API calls")
    rebuild_cmd.add_argument("--load-id", help="load to rebuild from (default: the latest completed load)")
    commands.add_parser("loads", help="list completed loads")
    commands.add_parser("reconcile", help="check output/*.csv against the warehouse and raw responses")
    args = parser.parse_args(argv)

    # Our own handler: dlt and dbt configure the root logger when imported, so basicConfig would be ignored.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    paths = Paths.from_env()
    try:
        if args.command == "run":
            load_id = run(load_config(args.config), paths)
            log.info("done: load %s, reports in %s", load_id, paths.output_dir)
        elif args.command == "rebuild":
            load_id = rebuild(paths, args.load_id)
            log.info("done: rebuilt from load %s, reports in %s", load_id, paths.output_dir)
        elif args.command == "reconcile":
            checks = reconcile(paths.warehouse, paths.output_dir)
            print(format_checks(checks))
            failed = [c for c in checks if not c.ok]
            if failed:
                log.error("%d of %d checks failed", len(failed), len(checks))
                return 1
            log.info("all %d checks passed: the CSVs match the warehouse and the raw API responses", len(checks))
        else:
            loads = list_loads(paths)
            print("load_id\tinserted_at\tpages")
            for load_id, inserted_at, pages in loads:
                print(f"{load_id}\t{inserted_at}\t{pages}")
    except (ConfigError, PipelineError, FileNotFoundError) as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
