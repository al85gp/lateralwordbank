"""Write the gold report views to CSV.

The files are written to output/.staging first and moved into output/ only when all of them
are written, so a failure part-way never leaves a mix of old and new reports.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import duckdb

# Each report and the key it is ordered by, so a rerun on unchanged data gives identical bytes.
REPORTS = {
    "rpt_missing_or_incomplete": "indicator_code, country_iso3, year",
    "rpt_yoy_change": "indicator_code, country_iso3, year",
    "rpt_latest_comparison": "indicator_code, country_iso3",
    "rpt_income_group_by_year": "indicator_code, income_group_code, year",
}


def export_reports(con: duckdb.DuckDBPyConnection, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for name, order_by in REPORTS.items():
        target = str(staging / f"{name}.csv").replace("'", "''")
        con.execute(f"copy (select * from gold.{name} order by {order_by}) to '{target}' (header, delimiter ',')")
    written = []
    for name in REPORTS:
        final = output_dir / f"{name}.csv"
        os.replace(staging / f"{name}.csv", final)
        written.append(final)
    staging.rmdir()
    return written
