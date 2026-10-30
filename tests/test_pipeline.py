"""End-to-end runs on recorded responses: dlt extract and load, dbt build, CSV export."""

import csv
import dataclasses
import hashlib
import json
import logging

import duckdb
import pytest

from conftest import Reply, canonical_urls, indicator_url, recorded_json
from wb_pipeline.export import REPORTS
from wb_pipeline.pipeline import PipelineError, list_loads, rebuild, run
from wb_pipeline.reconcile import format_checks, reconcile

POP_P1 = indicator_url("SP.POP.TOTL", 1)
LE_P2 = indicator_url("SP.DYN.LE00.IN", 2)
PCAP_P1 = indicator_url("NY.GDP.PCAP.CD", 1)

EXPECTED_HEADERS = {
    "rpt_missing_or_incomplete": "indicator_code,indicator_name,country_iso3,country_name,year,value_status,"
    "years_reported,years_expected,request_url,body_sha256,record_index",
    "rpt_yoy_change": "indicator_code,indicator_name,country_iso3,country_name,year,value,prior_year,prior_value,"
    "abs_change,pct_change,change_status,request_url,body_sha256,record_index",
    "rpt_latest_comparison": "indicator_code,indicator_name,unit,country_iso3,country_name,latest_year,value,"
    "value_rank,years_behind,request_url,body_sha256,record_index",
    "rpt_income_group_by_year": "indicator_code,income_group_code,income_group_name,year,aggregation,value,"
    "countries_in_group,countries_reporting,is_complete",
}


def query(paths, sql, params=None):
    with duckdb.connect(str(paths.warehouse)) as con:
        return con.execute(sql, params or []).fetchall()


def fact(paths):
    return query(paths, "select * exclude (load_id, scope_load_id) from gold.fct_indicator_year order by all")


def outputs(paths):
    return {name: (paths.output_dir / f"{name}.csv").read_bytes() for name in REPORTS}


def with_value(url, iso3, year, new_value):
    """The recorded body of `url` with one value changed, as a corrected release would send it."""
    meta, records = recorded_json(url)
    for record in records:
        if record["countryiso3code"] == iso3 and record["date"] == str(year):
            record["value"] = new_value
    return Reply.json([meta, records])


def test_end_to_end(api, config, paths):
    load_id = run(config, paths)

    assert api.calls == canonical_urls()
    assert [row[0] for row in list_loads(paths)] == [load_id]
    statuses = query(paths, "select value_status, count(*) from gold.fct_indicator_year group by all")
    assert statuses == [("reported", 252)]
    for name, header in EXPECTED_HEADERS.items():
        lines = (paths.output_dir / f"{name}.csv").read_text().splitlines()
        assert lines[0] == header
    assert len((paths.output_dir / "rpt_yoy_change.csv").read_text().splitlines()) == 1 + 252
    assert len((paths.output_dir / "rpt_latest_comparison.csv").read_text().splitlines()) == 1 + 36
    assert len((paths.output_dir / "rpt_income_group_by_year.csv").read_text().splitlines()) == 1 + 4 * 3 * 7
    assert not (paths.output_dir / ".staging").exists()


def test_lineage_every_value_resolves_to_its_raw_record(api, config, paths):
    run(config, paths)

    rows = query(paths, """
        select f.country_iso3, f.indicator_code, f.year, f.value, f.record_index, r.body, r.body_sha256
        from gold.fct_indicator_year f
        join raw.api_responses r on r._dlt_load_id = f.load_id and r.request_url = f.request_url
    """)  # fmt: skip
    assert len(rows) == 252
    for iso3, code, year, value, record_index, body, body_sha256 in rows:
        assert hashlib.sha256(body.encode()).hexdigest() == body_sha256
        record = json.loads(body)[1][record_index]
        assert (record["countryiso3code"], record["indicator"]["id"], int(record["date"])) == (iso3, code, year)
        assert float(record["value"]) == value


def test_idempotent_rerun(api, config, paths):
    run(config, paths)
    first_fact, first_outputs = fact(paths), outputs(paths)

    run(config, paths)

    assert len(list_loads(paths)) == 2
    assert query(paths, "select count(*) from raw.api_responses") == [(18,)]  # raw appends
    assert len(fact(paths)) == 252
    assert fact(paths) == first_fact  # no duplicate or changed analytical records
    assert outputs(paths) == first_outputs  # byte-identical CSVs, so a zero-line git diff


def test_correction_shows_in_the_fact_and_raw_keeps_both_bodies(api, config, paths):
    first_load = run(config, paths)
    api.queue(POP_P1, with_value(POP_P1, "BRA", 2023, 211_000_000))

    second_load = run(config, paths)

    assert query(paths, "select value from gold.fct_indicator_year where country_iso3 = 'BRA' "
                        "and indicator_code = 'SP.POP.TOTL' and year = 2023") == [(211_000_000.0,)]
    bodies = query(paths, "select _dlt_load_id, body_sha256 from raw.api_responses where request_url = ? order by 1", [POP_P1])
    assert [b[0] for b in bodies] == [first_load, second_load]
    assert bodies[0][1] != bodies[1][1]


def test_correction_replay_rebuilds_an_earlier_load_without_the_api(api, config, paths):
    first_load = run(config, paths)
    first_fact, first_outputs = fact(paths), outputs(paths)
    api.queue(POP_P1, with_value(POP_P1, "BRA", 2023, 211_000_000))
    run(config, paths)
    calls_before_rebuild = len(api.calls)

    rebuild(paths, first_load)

    assert len(api.calls) == calls_before_rebuild  # no API calls
    assert fact(paths) == first_fact
    assert outputs(paths) == first_outputs


def without_life_expectancy(config):
    """The same scope minus SP.DYN.LE00.IN; the other indicators keep their recorded URLs."""
    return dataclasses.replace(config, indicators=config.indicators[:3])


def merged_from(paths):
    return dict(query(paths, "select indicator_code, any_value(scope_load_id) from gold.fct_indicator_year "
                             "group by all having count(distinct scope_load_id) = 1"))  # fmt: skip


def test_merge_adds_the_rows_of_a_wider_scope(api, config, paths):
    first_load = run(without_life_expectancy(config), paths)
    assert len(fact(paths)) == 189  # 9 countries x 3 indicators x 7 years

    second_load = run(config, paths)

    assert len(fact(paths)) == 252
    assert set(merged_from(paths).values()) == {second_load}  # every key requested again was updated
    assert first_load != second_load


def test_merge_keeps_the_keys_a_narrower_run_did_not_request(api, config, paths):
    first_load = run(config, paths)
    api.queue(POP_P1, with_value(POP_P1, "BRA", 2023, 211_000_000))

    second_load = run(without_life_expectancy(config), paths)

    assert len(fact(paths)) == 252  # nothing dropped
    assert merged_from(paths) == {
        "SP.POP.TOTL": second_load, "NY.GDP.MKTP.CD": second_load, "NY.GDP.PCAP.CD": second_load,
        "SP.DYN.LE00.IN": first_load,  # not requested, so it keeps the first load's values and lineage
    }  # fmt: skip
    assert query(paths, "select value from gold.fct_indicator_year where country_iso3 = 'BRA' "
                        "and indicator_code = 'SP.POP.TOTL' and year = 2023") == [(211_000_000.0,)]
    checks = reconcile(paths.warehouse, paths.output_dir)
    assert all(c.ok for c in checks), format_checks(checks)


def test_full_refresh_replays_the_merges(api, config, paths):
    run(without_life_expectancy(config), paths)
    api.queue(POP_P1, with_value(POP_P1, "BRA", 2023, 211_000_000))
    run(config, paths)
    run(without_life_expectancy(config), paths)
    merged = query(paths, "select * from gold.fct_indicator_year order by all")
    merged_outputs = outputs(paths)

    rebuild(paths)  # --full-refresh: every load again, oldest first, the latest winning each key

    assert query(paths, "select * from gold.fct_indicator_year order by all") == merged  # load ids included
    assert outputs(paths) == merged_outputs


def test_failed_run_not_exported(api, config, paths):
    run(config, paths)
    before = outputs(paths)
    api.queue(LE_P2, Reply.json([{"message": [{"id": "120", "key": "Invalid value", "value": "x"}]}]))

    with pytest.raises(PipelineError, match="nothing was loaded.*id 120"):
        run(config, paths)

    assert len(list_loads(paths)) == 1  # no completed load added
    assert outputs(paths) == before  # output/ untouched


def test_bad_data_blocks_export(api, config, paths):
    run(config, paths)
    before = outputs(paths)
    # Seed a duplicate: the same raw page twice in the completed load.
    with duckdb.connect(str(paths.warehouse)) as con:
        con.execute("insert into raw.api_responses select * replace (_dlt_id || '-copy' as _dlt_id) "
                    "from raw.api_responses where request_url = ?", [POP_P1])

    with pytest.raises(PipelineError, match="dbt build failed.*assert_no_duplicate_api_records"):
        rebuild(paths)

    assert outputs(paths) == before


def test_consistency_warning_does_not_block_export(api, config, paths, caplog):
    meta, records = recorded_json(PCAP_P1)
    bra_2023 = next(r for r in records if r["countryiso3code"] == "BRA" and r["date"] == "2023")
    api.queue(PCAP_P1, with_value(PCAP_P1, "BRA", 2023, bra_2023["value"] * 1.05))

    with caplog.at_level(logging.WARNING, logger="wb_pipeline"):
        run(config, paths)

    assert "dbt warn: assert_gdp_per_capita_consistent, 1 row(s)" in caplog.text
    assert "BRA" in caplog.text
    assert set(outputs(paths)) == set(REPORTS)


def test_rebuild_rejects_an_unknown_load(api, config, paths):
    run(config, paths)
    with pytest.raises(PipelineError, match="not a completed load"):
        rebuild(paths, "1234.5")


def test_reconcile_csvs_match_their_source_tables(api, config, paths):
    run(config, paths)

    checks = reconcile(paths.warehouse, paths.output_dir)

    assert len(checks) == 13
    assert all(c.ok for c in checks), format_checks(checks)


def test_reconcile_catches_a_csv_that_no_longer_matches(api, config, paths):
    run(config, paths)
    report = paths.output_dir / "rpt_yoy_change.csv"
    with report.open(newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["value"] = str(float(rows[0]["value"]) * 1.01)  # one value edited by hand
    with report.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    failed = {c.name: c.actual for c in reconcile(paths.warehouse, paths.output_dir) if not c.ok}

    assert failed == {
        "rpt_yoy_change.csv equals gold.rpt_yoy_change (differing rows)": "2",  # the edited row, both ways
        "rpt_yoy_change.csv values = fct_indicator_year (differing rows)": "1",
    }
