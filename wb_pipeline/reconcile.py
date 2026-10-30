"""Check that the exported CSVs match the tables they came from: python -m wb_pipeline reconcile.

The dbt tests check the models inside the warehouse. This checks the files people actually read,
output/*.csv, against the warehouse and the raw API responses, from the outside:

1. Each CSV equals its gold report view, row for row and value for value.
2. Row counts follow from the scopes of the loads merged into gold (from raw.run_scope).
3. Every value in rpt_yoy_change.csv (one row per fact) equals gold.fct_indicator_year.
4. Every value in rpt_latest_comparison.csv equals the fact at that country's latest reported year.
5. Every reported fact equals the raw API record its lineage points at.
6. For `sum` indicators, the income-group totals in the CSV equal the sum of the country values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from wb_pipeline.export import REPORTS


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


def reconcile(warehouse: Path, output_dir: Path) -> list[Check]:
    with duckdb.connect(str(warehouse)) as con:
        for name in REPORTS:
            _register_csv(con, name, output_dir / f"{name}.csv")
        return _csv_matches_views(con) + _row_counts(con) + _values(con)


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"{'check'.ljust(width)}  {'expected':>10}  {'actual':>10}  result"]
    for c in checks:
        lines.append(f"{c.name.ljust(width)}  {c.expected:>10}  {c.actual:>10}  {'OK' if c.ok else 'MISMATCH'}")
    return "\n".join(lines)


def _register_csv(con: duckdb.DuckDBPyConnection, name: str, path: Path) -> None:
    """Read the CSV with its view's column types, so values compare exactly, not as text."""
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run the pipeline first")
    columns = con.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = 'gold' and table_name = ? order by ordinal_position",
        [name],
    ).fetchall()
    types = ", ".join(f"'{column}': '{data_type}'" for column, data_type in columns)
    csv = str(path).replace("'", "''")
    con.execute(
        f"create or replace temp view csv_{name} as "
        f"select * from read_csv('{csv}', header = true, auto_detect = false, columns = {{{types}}})"
    )


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


def _csv_matches_views(con) -> list[Check]:
    checks = []
    for name in REPORTS:
        differing = _count(con, f"""
            select count(*) from (
                (select * from csv_{name} except all select * from gold.{name})
                union all
                (select * from gold.{name} except all select * from csv_{name})
            )""")  # fmt: skip
        checks.append(Check(f"{name}.csv equals gold.{name} (differing rows)", "0", str(differing)))
    return checks


def _row_counts(con) -> list[Check]:
    # Every key any merged load requested, from raw.run_scope: the latest load wins it, but none drops out.
    keys = """
        select distinct c.country_iso3, i.indicator_code, y.year
        from raw.run_scope s
        join stage.stg_wb__loads l on s._dlt_load_id = l.load_id
        cross join unnest(from_json(s.countries, '["VARCHAR"]')) as c(country_iso3)
        cross join unnest(list_transform(from_json(s.indicators, '[{"code": "VARCHAR"}]'), x -> x.code))
            as i(indicator_code)
        cross join unnest(range(s.year_start, s.year_end + 1)) as y(year)
    """
    spine = _count(con, f"select count(*) from ({keys})")
    countries_x_indicators = _count(con, f"select count(distinct (country_iso3, indicator_code)) from ({keys})")
    groups = _count(con, f"""
        select count(distinct (d.income_group_code, k.indicator_code, k.year))
        from ({keys}) k join gold.dim_country d on d.country_iso3 = k.country_iso3""")  # fmt: skip
    not_reported = _count(con, "select count(*) from gold.fct_indicator_year where value_status <> 'reported'")
    return [
        Check("fct_indicator_year rows = keys requested by merged loads", str(spine),
              str(_count(con, "select count(*) from gold.fct_indicator_year"))),
        Check("rpt_yoy_change.csv rows = one per fact", str(spine), str(_count(con, "select count(*) from csv_rpt_yoy_change"))),
        Check("rpt_latest_comparison.csv rows = countries x indicators", str(countries_x_indicators),
              str(_count(con, "select count(*) from csv_rpt_latest_comparison"))),
        Check("rpt_income_group_by_year.csv rows = group x indicator x year combinations", str(groups),
              str(_count(con, "select count(*) from csv_rpt_income_group_by_year"))),
        Check("rpt_missing_or_incomplete.csv rows = facts not reported", str(not_reported),
              str(_count(con, "select count(*) from csv_rpt_missing_or_incomplete"))),
    ]  # fmt: skip


def _values(con) -> list[Check]:
    yoy_vs_fact = _count(con, """
        select count(*)
        from csv_rpt_yoy_change c
        full join gold.fct_indicator_year f
            on c.country_iso3 = f.country_iso3 and c.indicator_code = f.indicator_code and c.year = f.year
        where c.country_iso3 is null or f.country_iso3 is null
           or c.value is distinct from f.value
           or c.request_url is distinct from f.request_url
           or c.record_index is distinct from f.record_index""")  # fmt: skip
    latest_vs_fact = _count(con, """
        with latest as (
            select country_iso3, indicator_code, max(year) filter (where value_status = 'reported') as year
            from gold.fct_indicator_year group by all
        )
        select count(*)
        from csv_rpt_latest_comparison c
        join latest l on c.country_iso3 = l.country_iso3 and c.indicator_code = l.indicator_code
        left join gold.fct_indicator_year f
            on f.country_iso3 = l.country_iso3 and f.indicator_code = l.indicator_code and f.year = l.year
        where c.latest_year is distinct from l.year or c.value is distinct from f.value""")  # fmt: skip
    fact_vs_raw = _count(con, """
        select count(*)
        from gold.fct_indicator_year f
        left join raw.api_responses r on r._dlt_load_id = f.load_id and r.request_url = f.request_url
        where f.value_status = 'reported' and (
            r.body is null
            or sha256(r.body) <> f.body_sha256
            or json_extract_string(r.body, '$[1][' || f.record_index || '].countryiso3code') <> f.country_iso3
            or json_extract_string(r.body, '$[1][' || f.record_index || '].indicator.id') <> f.indicator_code
            or cast(json_extract_string(r.body, '$[1][' || f.record_index || '].date') as integer) <> f.year
            or cast(json_extract_string(r.body, '$[1][' || f.record_index || '].value') as double) <> f.value
        )""")  # fmt: skip
    group_totals = _count(con, """
        with csv_totals as (
            select indicator_code, year, sum(value) as total
            from csv_rpt_income_group_by_year where aggregation = 'sum' group by all
        ),
        country_totals as (
            select indicator_code, year, sum(value) filter (where value_status = 'reported') as total
            from gold.fct_indicator_year group by all
        )
        select count(*)
        from csv_totals c
        join country_totals f on c.indicator_code = f.indicator_code and c.year = f.year
        where abs(c.total - f.total) > 1e-9 * abs(f.total)""")  # fmt: skip
    return [
        Check("rpt_yoy_change.csv values = fct_indicator_year (differing rows)", "0", str(yoy_vs_fact)),
        Check("rpt_latest_comparison.csv values = fact at latest year (differing rows)", "0", str(latest_vs_fact)),
        Check("fct_indicator_year values = raw API record (differing rows)", "0", str(fact_vs_raw)),
        Check("income-group sums = country totals (differing indicator-years)", "0", str(group_totals)),
    ]
