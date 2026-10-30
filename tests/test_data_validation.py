"""End-to-end data validation: a null value and a missing record, from the API to every CSV.

The recorded responses are served with three gaps the live data does not have: USA's GDP for
2020 and Brazil's population for 2019 are sent as JSON null (null_in_source), and South Africa's
life expectancy for 2024 is left out of the response (not_returned). A null population also
takes Brazil out of its group's population-weighted means for 2019. The run goes through dlt, raw, stage, the gold fact and every
report. An independent oracle, written in Python from the JSON the fake API served, computes
what every fact and every report row must be; the warehouse and the four CSVs are compared
against it row by row. A second run then serves the complete data, as a late release would,
and the merge must turn both gaps into reported values.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math

import duckdb

from conftest import COUNTRIES, INDICATORS, Reply, country_url, indicator_url, recorded_json
from wb_pipeline.pipeline import run
from wb_pipeline.reconcile import format_checks, reconcile

YEARS = range(2018, 2025)
POPULATION = "SP.POP.TOTL"
NULL_IN_SOURCE = [
    ("NY.GDP.MKTP.CD", "USA", 2020),  # a `sum` indicator
    ("SP.POP.TOTL", "BRA", 2019),  # the weight of every population-weighted mean
]
NOT_RETURNED = ("SP.DYN.LE00.IN", "ZAF", 2024)  # a `population_weighted_mean` indicator, at the latest year


# --- What the fake API serves ------------------------------------------------------------------


def complete_bodies() -> dict[str, list]:
    """Indicator URL -> parsed body, exactly as recorded."""
    return {indicator_url(code, page): recorded_json(indicator_url(code, page)) for code in INDICATORS for page in (1, 2)}


def with_gaps(bodies: dict[str, list]) -> dict[str, list]:
    """The same bodies with values nulled and one record removed, with metadata to match."""
    bodies = json.loads(json.dumps(bodies))
    for code, iso3, year in NULL_IN_SOURCE:
        for url in (indicator_url(code, 1), indicator_url(code, 2)):
            for record in bodies[url][1]:
                if (record["countryiso3code"], record["date"]) == (iso3, str(year)):
                    record["value"] = None

    code, iso3, year = NOT_RETURNED
    last_page = bodies[indicator_url(code, 2)]
    kept = [r for r in last_page[1] if (r["countryiso3code"], r["date"]) != (iso3, str(year))]
    assert len(kept) == len(last_page[1]) - 1, "the removed record must be on the last page"
    last_page[1] = kept
    for page in (1, 2):  # the API's total counts the records it returns, on every page
        bodies[indicator_url(code, page)][0]["total"] -= 1
    return bodies


def serve(api, bodies: dict[str, list]) -> dict[str, bytes]:
    """Queue the bodies on the fake API; returns the exact bytes each URL will answer with."""
    served = {}
    for url, body in bodies.items():
        reply = Reply.json(body)
        api.queue(url, reply)
        served[url] = reply.body
    return served


# --- The oracle: expected rows computed from the served JSON alone ----------------------------


def expected_facts(bodies: dict[str, list]) -> dict[tuple, tuple]:
    """(indicator, country, year) -> (value, value_status), for the full requested spine."""
    returned = {}
    for body in bodies.values():
        for record in body[1]:
            key = (record["indicator"]["id"], record["countryiso3code"], int(record["date"]))
            returned[key] = record["value"]
    facts = {}
    for code in INDICATORS:
        for iso3 in COUNTRIES.split(";"):
            for year in YEARS:
                key = (code, iso3, year)
                if key not in returned:
                    facts[key] = (None, "not_returned")
                elif returned[key] is None:
                    facts[key] = (None, "null_in_source")
                else:
                    facts[key] = (float(returned[key]), "reported")
    return facts


def expected_yoy(facts) -> dict[tuple, tuple]:
    """(indicator, country, year) -> (value, prior_value, abs_change, pct_change, change_status)."""
    rows = {}
    for (code, iso3, year), (value, _) in facts.items():
        prior = facts.get((code, iso3, year - 1), (None, None))[0]
        if year == YEARS[0]:
            status = "first_year_in_scope"
        elif value is None:
            status = "no_current_value"
        elif prior is None:
            status = "no_prior_value"
        elif prior == 0:
            status = "prior_is_zero"
        else:
            status = "ok"
        both = value is not None and prior is not None
        abs_change = value - prior if both else None
        pct_change = (value - prior) / abs(prior) * 100 if both and prior != 0 else None
        rows[(code, iso3, year)] = (value, prior, abs_change, pct_change, status)
    return rows


def expected_latest(facts) -> dict[tuple, tuple]:
    """(indicator, country) -> (latest_year, value, value_rank, years_behind)."""
    latest = {}
    for code in INDICATORS:
        for iso3 in COUNTRIES.split(";"):
            years = [y for y in YEARS if facts[(code, iso3, y)][1] == "reported"]
            year = max(years) if years else None
            latest[(code, iso3)] = (year, facts[(code, iso3, year)][0] if year else None)
    rows = {}
    for (code, iso3), (year, value) in latest.items():
        peers = [v for (c, _), (_, v) in latest.items() if c == code and v is not None]
        newest = max(y for (c, _), (y, _) in latest.items() if c == code and y is not None)
        rank = 1 + sum(v > value for v in peers) if value is not None else None
        rows[(code, iso3)] = (year, value, rank, newest - year if year else None)
    return rows


def expected_income_groups(facts, groups: dict[str, str], aggregation: dict[str, str]) -> dict[tuple, tuple]:
    """(indicator, income_group, year) -> (value, countries_in_group, countries_reporting, is_complete)."""
    rows = {}
    for code in INDICATORS:
        for group in sorted(set(groups.values())):
            members = [iso3 for iso3, g in groups.items() if g == group]
            for year in YEARS:
                values = {iso3: facts[(code, iso3, year)][0] for iso3 in members}
                weights = {iso3: facts[(POPULATION, iso3, year)][0] for iso3 in members}
                if aggregation[code] == "sum":
                    reporting = [i for i in members if values[i] is not None]
                    value = sum(values[i] for i in reporting) if reporting else None
                else:
                    reporting = [i for i in members if values[i] is not None and weights[i] is not None]
                    value = (sum(values[i] * weights[i] for i in reporting) / sum(weights[i] for i in reporting)
                             if reporting else None)  # fmt: skip
                rows[(code, group, year)] = (value, len(members), len(reporting), len(reporting) == len(members))
    return rows


# --- Reading what the pipeline produced -------------------------------------------------------


def read_csv(paths, name: str) -> list[dict]:
    with (paths.output_dir / f"{name}.csv").open(newline="") as f:
        return list(csv.DictReader(f))


def num(text: str):
    return None if text == "" else float(text)


def same(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if isinstance(expected, (bool, str)):
        return actual == expected
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def assert_rows_match(name: str, actual: dict, expected: dict) -> None:
    assert actual.keys() == expected.keys(), f"{name}: keys differ"
    wrong = {k: (actual[k], expected[k]) for k in expected
             if not all(same(a, e) for a, e in zip(actual[k], expected[k], strict=True))}  # fmt: skip
    assert not wrong, f"{name}: {len(wrong)} row(s) differ from the oracle, e.g. {next(iter(wrong.items()))}"


def assert_pipeline_matches(paths, bodies, served: dict[str, bytes], config) -> None:
    """Every layer, from raw to the CSVs, against the oracle computed from `bodies`."""
    facts = expected_facts(bodies)
    load_id = run(config, paths)
    con = duckdb.connect(str(paths.warehouse))
    try:
        # raw: each body is stored byte for byte as served.
        stored = dict(con.execute("select request_url, body from raw.api_responses where _dlt_load_id = ? "
                                  "and endpoint = 'indicator'", [load_id]).fetchall())  # fmt: skip
        assert {url: body.encode() for url, body in stored.items()} == served
        for url, body in con.execute("select request_url, body_sha256 from raw.api_responses "
                                     "where _dlt_load_id = ? and endpoint = 'indicator'", [load_id]).fetchall():  # fmt: skip
            assert body == hashlib.sha256(served[url]).hexdigest()

        # stage: one row per returned record of this load, the null kept as null.
        stage = con.execute("select indicator_code, country_iso3, year, value from stage.stg_wb__observations "
                            "where load_id = ?", [load_id]).fetchall()  # fmt: skip
        returned = {k: v for k, v in facts.items() if v[1] != "not_returned"}
        assert {(c, i, y): v for c, i, y, v in stage} == {k: v[0] for k, v in returned.items()}

        # gold: the full spine, each row merged from this load, lineage only where a record exists.
        gold = con.execute("""
            select indicator_code, country_iso3, year, value, value_status, scope_load_id, load_id, request_url,
                   record_index
            from gold.fct_indicator_year""").fetchall()  # fmt: skip
        assert_rows_match("fct_indicator_year", {(c, i, y): (v, s) for c, i, y, v, s, *_ in gold}, facts)
        for code, iso3, year, _, status, scope_load, value_load, url, index in gold:
            assert scope_load == load_id
            has_lineage = (value_load, url, index) != (None, None, None)
            assert has_lineage == (status != "not_returned"), (code, iso3, year, status)
            if has_lineage:  # the lineage points at the very record, null or not
                record = json.loads(served[url])[1][index]
                assert (record["indicator"]["id"], record["countryiso3code"], int(record["date"])) == (code, iso3, year)

        groups = dict(con.execute("select country_iso3, income_group_code from gold.dim_country").fetchall())
        aggregation = dict(con.execute("select indicator_code, aggregation from gold.dim_indicator").fetchall())
    finally:
        con.close()
    assert groups == {r["id"]: r["incomeLevel"]["id"] for r in recorded_json(country_url())[1]}

    # The four CSVs.
    missing = {(r["indicator_code"], r["country_iso3"], int(r["year"])): (r["value_status"], int(r["years_reported"]),
               int(r["years_expected"]), r["request_url"] != "") for r in read_csv(paths, "rpt_missing_or_incomplete")}  # fmt: skip
    expected_missing = {}
    for (code, iso3, year), (_, status) in facts.items():
        if status != "reported":
            reported = sum(facts[(code, iso3, y)][1] == "reported" for y in YEARS)
            expected_missing[(code, iso3, year)] = (status, reported, len(YEARS), status == "null_in_source")
    assert missing == expected_missing

    yoy = {(r["indicator_code"], r["country_iso3"], int(r["year"])): (num(r["value"]), num(r["prior_value"]),
           num(r["abs_change"]), num(r["pct_change"]), r["change_status"]) for r in read_csv(paths, "rpt_yoy_change")}  # fmt: skip
    assert_rows_match("rpt_yoy_change", yoy, expected_yoy(facts))

    latest = {(r["indicator_code"], r["country_iso3"]): (int(r["latest_year"]) if r["latest_year"] else None,
              num(r["value"]), num(r["value_rank"]), num(r["years_behind"]))
              for r in read_csv(paths, "rpt_latest_comparison")}  # fmt: skip
    assert_rows_match("rpt_latest_comparison", latest, expected_latest(facts))

    by_group = {(r["indicator_code"], r["income_group_code"], int(r["year"])): (num(r["value"]),
                int(r["countries_in_group"]), int(r["countries_reporting"]), r["is_complete"] == "true")
                for r in read_csv(paths, "rpt_income_group_by_year")}  # fmt: skip
    assert_rows_match("rpt_income_group_by_year", by_group, expected_income_groups(facts, groups, aggregation))

    checks = reconcile(paths.warehouse, paths.output_dir)
    assert all(c.ok for c in checks), format_checks(checks)


# --- The tests --------------------------------------------------------------------------------


def test_null_and_missing_values_flow_correctly_to_every_report(api, config, paths):
    bodies = with_gaps(complete_bodies())

    assert_pipeline_matches(paths, bodies, serve(api, bodies), config)

    # The cases the oracle covers, spelled out, so a broken oracle cannot hide them.
    yoy = {(r["indicator_code"], r["country_iso3"], r["year"]): r for r in read_csv(paths, "rpt_yoy_change")}
    assert yoy[("NY.GDP.MKTP.CD", "USA", "2020")]["change_status"] == "no_current_value"
    assert yoy[("NY.GDP.MKTP.CD", "USA", "2021")]["change_status"] == "no_prior_value"
    assert yoy[("NY.GDP.MKTP.CD", "USA", "2021")]["pct_change"] == ""  # missing, never a 0 or a number
    assert yoy[("SP.DYN.LE00.IN", "ZAF", "2024")]["change_status"] == "no_current_value"
    latest = {(r["indicator_code"], r["country_iso3"]): r for r in read_csv(paths, "rpt_latest_comparison")}
    assert (latest[("SP.DYN.LE00.IN", "ZAF")]["latest_year"], latest[("SP.DYN.LE00.IN", "ZAF")]["years_behind"]) == ("2023", "1")
    groups = {(r["indicator_code"], r["income_group_code"], r["year"]): r for r in read_csv(paths, "rpt_income_group_by_year")}
    assert (groups[("NY.GDP.MKTP.CD", "HIC", "2020")]["countries_reporting"], groups[("NY.GDP.MKTP.CD", "HIC", "2020")]["is_complete"]) == ("4", "false")
    assert (groups[("SP.DYN.LE00.IN", "UMC", "2024")]["countries_reporting"], groups[("SP.DYN.LE00.IN", "UMC", "2024")]["is_complete"]) == ("2", "false")
    pcap = groups[("NY.GDP.PCAP.CD", "UMC", "2019")]  # Brazil reports GDP per capita but has no weight
    assert (pcap["countries_in_group"], pcap["countries_reporting"], pcap["is_complete"]) == ("3", "2", "false")
    assert len(read_csv(paths, "rpt_missing_or_incomplete")) == 3


def test_late_values_are_merged_in_on_the_next_run(api, config, paths):
    gaps = with_gaps(complete_bodies())
    assert_pipeline_matches(paths, gaps, serve(api, gaps), config)

    complete = complete_bodies()
    assert_pipeline_matches(paths, complete, serve(api, complete), config)
    assert read_csv(paths, "rpt_missing_or_incomplete") == []
