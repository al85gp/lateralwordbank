"""Run the steps in order: extract and load with dlt, dbt build, CSV export.

Each step runs only if the previous one succeeded, so a failed run never touches output/.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import dlt
import duckdb
from dbt.cli.main import dbtRunner

from wb_pipeline.config import POPULATION, PipelineConfig
from wb_pipeline.export import export_reports
from wb_pipeline.source import world_bank_source

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_NAME = "world_bank"
RAW_DATASET = "raw"
LOAD_ID = re.compile(r"^\d+(\.\d+)?$")


class PipelineError(RuntimeError):
    """A step failed; the message says which and why."""


@dataclass(frozen=True)
class Paths:
    warehouse: Path
    output_dir: Path
    pipelines_dir: Path
    dbt_project_dir: Path
    dbt_target_dir: Path
    dbt_log_dir: Path

    @classmethod
    def from_env(cls) -> "Paths":
        """Paths from the environment (set in the Docker image), else the repository's folders."""

        def env_path(name: str, default: Path) -> Path:
            return Path(os.environ.get(name) or default)

        dbt_dir = REPO_ROOT / "dbt"
        return cls(
            warehouse=env_path("WAREHOUSE_PATH", REPO_ROOT / "data" / "warehouse.duckdb"),
            output_dir=env_path("OUTPUT_DIR", REPO_ROOT / "output"),
            pipelines_dir=env_path("PIPELINES_DIR", REPO_ROOT / "data" / "dlt_pipelines"),
            dbt_project_dir=dbt_dir,
            dbt_target_dir=env_path("DBT_TARGET_PATH", dbt_dir / "target"),
            dbt_log_dir=env_path("DBT_LOG_PATH", dbt_dir / "logs"),
        )


def run(config: PipelineConfig, paths: Paths) -> str:
    """Extract and load, then merge that load into gold and export. Returns the load id."""
    load_id = extract_load(config, paths)
    build(paths, load_id)
    export(paths)
    return load_id


def rebuild(paths: Paths, load_id: str | None = None) -> str:
    """Rebuild every model and the CSVs from raw alone, no API calls: a full refresh of gold that
    replays every completed load up to `load_id`, the latest load winning each key."""
    load_id = load_id or latest_load_id(paths)
    if not LOAD_ID.match(load_id):
        raise PipelineError(f"{load_id!r} is not a load id; list them with: python -m wb_pipeline loads")
    if load_id not in {row[0] for row in list_loads(paths)}:
        raise PipelineError(f"load {load_id} is not a completed load; list them with: python -m wb_pipeline loads")
    build(paths, load_id, full_refresh=True)
    export(paths)
    return load_id


def extract_load(config: PipelineConfig, paths: Paths) -> str:
    paths.warehouse.parent.mkdir(parents=True, exist_ok=True)
    pipeline = dlt.pipeline(
        pipeline_name=PIPELINE_NAME,
        destination=dlt.destinations.duckdb(str(paths.warehouse)),
        dataset_name=RAW_DATASET,
        pipelines_dir=str(paths.pipelines_dir),
    )
    # Discard any load package a crashed run left behind, so it is never loaded later.
    pipeline.abort_packages()
    try:
        info = pipeline.run(world_bank_source(config))
        info.raise_on_failed_jobs()
    except Exception as e:
        raise PipelineError(f"extract and load failed, nothing was loaded: {root_cause(e)}") from e
    load_id = info.loads_ids[0]
    log.info("loaded %s", load_id)
    return load_id


def build(paths: Paths, load_id: str, full_refresh: bool = False) -> None:
    """dbt build up to one load. Incremental gold models merge only that load, or replay every
    load up to it with `full_refresh`. Warn-level test failures are logged with their rows."""
    os.environ["WAREHOUSE_PATH"] = str(paths.warehouse)  # read by dbt/profiles.yml
    args = [
        "build",
        "--project-dir", str(paths.dbt_project_dir),
        "--profiles-dir", str(paths.dbt_project_dir),
        "--target-path", str(paths.dbt_target_dir),
        "--log-path", str(paths.dbt_log_dir),
        "--vars", json.dumps({"load_id": load_id, "population_indicator": POPULATION}),
    ]  # fmt: skip
    if full_refresh:
        args.append("--full-refresh")
    result = dbtRunner().invoke(args)
    if result.exception is not None:
        raise PipelineError(f"dbt build could not run: {result.exception}") from result.exception
    node_results = result.result.results if result.result is not None else []
    _log_warnings(paths, [r for r in node_results if r.status == "warn"])
    if not result.success:
        failed = sorted(r.node.name for r in node_results if r.status in ("error", "fail"))
        raise PipelineError(f"dbt build failed, output/ was not changed: {', '.join(failed) or 'see the dbt log'}")


def export(paths: Paths) -> None:
    with duckdb.connect(str(paths.warehouse)) as con:
        written = export_reports(con, paths.output_dir)
    log.info("wrote %s", ", ".join(p.name for p in written))


def latest_load_id(paths: Paths) -> str:
    loads = list_loads(paths)
    if not loads:
        raise PipelineError("there is no completed load to rebuild from; run the pipeline first")
    return loads[-1][0]


def list_loads(paths: Paths) -> list[tuple[str, object, int]]:
    """Completed loads, oldest first: (load_id, inserted_at, pages)."""
    if not paths.warehouse.exists():
        return []
    with duckdb.connect(str(paths.warehouse)) as con:
        tables = {r[0] for r in con.execute(
            "select table_name from information_schema.tables where table_schema = ?", [RAW_DATASET]
        ).fetchall()}  # fmt: skip
        if not {"_dlt_loads", "run_scope", "api_responses"} <= tables:
            return []
        return con.execute(
            """
            select l.load_id, l.inserted_at, count(r._dlt_id) as pages
            from raw._dlt_loads l
            join raw.run_scope s on s._dlt_load_id = l.load_id
            left join raw.api_responses r on r._dlt_load_id = l.load_id
            where l.status = 0
            group by all
            order by l.inserted_at, l.load_id
            """
        ).fetchall()


def root_cause(e: BaseException) -> str:
    """The innermost exception's message; dlt wraps resource errors in several layers."""
    seen = set()
    while id(e) not in seen:
        seen.add(id(e))
        inner = getattr(e, "exception", None) or e.__cause__ or e.__context__
        if inner is None:
            break
        e = inner
    return f"{type(e).__name__}: {e}"


def _log_warnings(paths: Paths, warnings: list) -> None:
    if not warnings:
        return
    with duckdb.connect(str(paths.warehouse)) as con:
        for r in warnings:
            log.warning("dbt warn: %s, %s row(s)", r.node.name, r.failures)
            relation = getattr(r.node, "relation_name", None)
            if relation:
                cursor = con.execute(f"select * from {relation} limit 20")
                lines = [" | ".join(d[0] for d in cursor.description)]
                lines += [" | ".join(map(str, row)) for row in cursor.fetchall()]
                log.warning("%s rows:\n  %s", r.node.name, "\n  ".join(lines))
