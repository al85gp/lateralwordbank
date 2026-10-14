"""Command line: python -m wb_pipeline run | rebuild [--load-id ID] | loads"""

from __future__ import annotations

import argparse
import logging
import sys

from wb_pipeline.config import ConfigError, load_config
from wb_pipeline.pipeline import REPO_ROOT, Paths, PipelineError, list_loads, rebuild, run

log = logging.getLogger("wb_pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wb_pipeline", description="World Bank indicators pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="extract and load from the API, dbt build, export CSVs")
    run_cmd.add_argument("--config", default=str(REPO_ROOT / "config" / "pipeline.yml"), help="pipeline config file")
    rebuild_cmd = commands.add_parser("rebuild", help="dbt build and export from a stored load, no API calls")
    rebuild_cmd.add_argument("--load-id", help="load to rebuild from (default: the latest completed load)")
    commands.add_parser("loads", help="list completed loads")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths = Paths.from_env()
    try:
        if args.command == "run":
            load_id = run(load_config(args.config), paths)
            log.info("done: load %s, reports in %s", load_id, paths.output_dir)
        elif args.command == "rebuild":
            load_id = rebuild(paths, args.load_id)
            log.info("done: rebuilt from load %s, reports in %s", load_id, paths.output_dir)
        else:
            loads = list_loads(paths)
            print("load_id\tinserted_at\tpages")
            for load_id, inserted_at, pages in loads:
                print(f"{load_id}\t{inserted_at}\t{pages}")
    except (ConfigError, PipelineError) as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
