"""The API can answer HTTP 200 with the body of a different query (api_calls/02, 05). The run must
reject it, request the page again with a fresh cache-buster, and record which attempt was kept."""

import hashlib

import duckdb
import pytest

from conftest import Reply, canonical_urls, indicator_url, recorded
from wb_pipeline.pipeline import PipelineError, extract_load, list_loads

GDP_P1 = indicator_url("NY.GDP.MKTP.CD", 1)
POP_P1 = indicator_url("SP.POP.TOTL", 1)


def raw_page(paths, request_url):
    with duckdb.connect(str(paths.warehouse)) as con:
        return con.execute(
            "select request_url, fetched_url, attempt, body_sha256, body from raw.api_responses where request_url = ?",
            [request_url],
        ).fetchall()


def test_wrong_body_retry_loads_the_cache_busted_attempt(api, config, paths):
    # Page 1 of GDP is first served page 1 of population: a real response to a different query.
    api.queue(GDP_P1, Reply(recorded()[POP_P1]), Reply(recorded()[GDP_P1]))

    load_id = extract_load(config, paths)

    assert api.calls_to(GDP_P1) == [GDP_P1, f"{GDP_P1}&cb={load_id}-2"]
    [(request_url, fetched_url, attempt, body_sha256, body)] = raw_page(paths, GDP_P1)
    assert request_url == GDP_P1  # lineage keeps the canonical URL
    assert fetched_url == f"{GDP_P1}&cb={load_id}-2"
    assert attempt == 2
    assert body_sha256 == hashlib.sha256(recorded()[GDP_P1]).hexdigest()
    assert body.encode() == recorded()[GDP_P1]


def test_wrong_body_retry_fails_the_run_when_every_attempt_is_wrong(api, config, paths):
    api.queue(GDP_P1, Reply(recorded()[POP_P1]))

    with pytest.raises(PipelineError, match="no valid response .* after 4 attempts"):
        extract_load(config, paths)

    assert len(api.calls_to(GDP_P1)) == 4
    assert list_loads(paths) == []  # nothing was loaded, not even the pages that were valid


def test_wrong_body_retry_is_not_needed_on_a_clean_run(api, config, paths):
    extract_load(config, paths)
    assert api.calls == canonical_urls()
