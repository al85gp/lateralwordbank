"""Paging, HTTP retries, API error bodies and response validation, against recorded responses."""

import copy
import json

import pytest
import requests

from conftest import INDICATORS, Reply, canonical_urls, country_url, indicator_url, recorded, recorded_json
from wb_pipeline.source import (
    ApiErrorResponse,
    ResponseRejected,
    SourceChanged,
    WorldBankClient,
    WorldBankError,
    plan_requests,
    validate_page,
)

LOAD_ID = "1700000000.123456"
POP_P1 = indicator_url("SP.POP.TOTL", 1)
POP_P2 = indicator_url("SP.POP.TOTL", 2)


@pytest.fixture
def client(config, paths):  # paths sets zero retry delays
    return WorldBankClient(config, LOAD_ID, sleep=lambda seconds: None)


def request_for(config, code):
    return next(r for r in plan_requests(config) if r.indicator_code == code)


def cache_busted(url, attempt):
    return f"{url}&cb={LOAD_ID}-{attempt}"


def error_body(error_id, key="Invalid value", value="The provided parameter value is not valid"):
    return Reply.json([{"message": [{"id": error_id, "key": key, "value": value}]}])


# --- paging -----------------------------------------------------------------------------------


def test_paging_reads_every_page_from_the_canonical_urls(api, client, config):
    pages = {r.describe(): client.fetch_all(r) for r in plan_requests(config)}

    assert [len(p) for p in pages.values()] == [1, 2, 2, 2, 2]
    assert sum(len(p.records) for p in pages["country"]) == 9
    for code in INDICATORS:
        assert sum(len(p.records) for p in pages[code]) == 63
        assert [p.attempt for p in pages[code]] == [1, 1]
    assert api.calls == canonical_urls()  # one request per page, no cache-busters


def test_paging_count_that_disagrees_with_total_fails(api, client, config):
    meta, records = recorded_json(POP_P2)
    api.queue(POP_P2, Reply.json([meta, records[:-1]]))  # 12 records, total still says 63

    with pytest.raises(WorldBankError, match="12 records on page 2, expected 13"):
        client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert api.calls_to(POP_P2) == [POP_P2] + [cache_busted(POP_P2, n) for n in (2, 3, 4)]


def test_paging_restarts_from_page_1_when_a_later_page_is_from_another_release(api, client, config):
    meta, records = recorded_json(POP_P2)
    stale = [dict(meta, lastupdated="2026-07-01"), records]
    api.queue(POP_P2, Reply.json(stale), Reply(recorded()[POP_P2]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert api.calls == [POP_P1, POP_P2, cache_busted(POP_P1, 2), cache_busted(POP_P2, 2)]
    assert [p.attempt for p in pages] == [2, 2]
    assert sum(len(p.records) for p in pages) == 63


def test_paging_zero_matches_is_an_empty_result_not_an_error(api, client, config):
    api.queue(POP_P1, Reply.json([{"page": 0, "pages": 0, "per_page": 0, "total": 0, "sourceid": None, "lastupdated": None}, None]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert [len(p.records) for p in pages] == [0]
    assert api.calls == [POP_P1]


# --- HTTP retries (dlt's requests client) -------------------------------------------------------


def test_retries_503_twice_then_success(api, client, config):
    api.queue(POP_P1, Reply(status=503), Reply(status=503), Reply(recorded()[POP_P1]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert api.calls_to(POP_P1) == [POP_P1] * 3  # HTTP retries resend the same URL
    assert pages[0].attempt == 1  # and are not validation attempts


def test_retries_five_503s_fail_the_run(api, client, config):
    api.queue(POP_P1, Reply(status=503))

    with pytest.raises(requests.HTTPError, match="503"):
        client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert len(api.calls_to(POP_P1)) == 5  # request_max_attempts in .dlt/config.toml


def test_retries_a_timeout(api, client, config):
    api.queue(POP_P1, requests.exceptions.ReadTimeout("read timed out"), Reply(recorded()[POP_P1]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert len(api.calls_to(POP_P1)) == 2
    assert len(pages) == 2


def test_retries_none_for_a_400(api, client, config):
    api.queue(POP_P1, Reply(b"<html>Request Error</html>", status=400, content_type="text/html"))

    with pytest.raises(requests.HTTPError, match="400"):
        client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert len(api.calls_to(POP_P1)) == 1


# --- API error bodies (HTTP 200) ----------------------------------------------------------------


def test_error_payload_120_fails_at_once(api, client, config):
    api.queue(POP_P1, error_body("120"))

    with pytest.raises(ApiErrorResponse, match="id 120"):
        client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert api.calls == [POP_P1]


def test_error_payload_105_is_retried_with_a_cache_buster(api, client, config):
    api.queue(POP_P1, error_body("105", "Service currently unavailable", ""), Reply(recorded()[POP_P1]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert api.calls_to(POP_P1) == [POP_P1, cache_busted(POP_P1, 2)]
    assert pages[0].attempt == 2
    assert pages[0].fetched_url == cache_busted(POP_P1, 2)
    assert pages[0].request_url == POP_P1


def test_error_payload_199_on_every_attempt_fails_after_the_budget(api, client, config):
    api.queue(POP_P1, error_body("199", "Unexpected error", ""))

    with pytest.raises(WorldBankError, match="after 4 attempts"):
        client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert len(api.calls_to(POP_P1)) == 4


# --- response validation ------------------------------------------------------------------------


def valid_page_1():
    return recorded_json(POP_P1)


def mutate(change):
    body = copy.deepcopy(valid_page_1())
    change(body)
    return body


@pytest.mark.parametrize(
    "body, reason",
    [
        (mutate(lambda b: b[1][0]["indicator"].update(id="NY.GDP.MKTP.CD")), "indicator"),
        (mutate(lambda b: b[1][0].update(countryiso3code="FRA")), "country 'FRA'"),
        (mutate(lambda b: b[1][0].update(date="2017")), "year 2017"),
        (mutate(lambda b: b[1][0].update(date="abc")), "non-integer date"),
        (mutate(lambda b: b[0].update(page=2)), "page is 2"),
        (mutate(lambda b: b[0].update(per_page=100)), "per_page is 100"),
        (mutate(lambda b: b[0].update(sourceid="11")), "sourceid"),
        (mutate(lambda b: b[0].pop("total")), "metadata lacks"),
        (mutate(lambda b: b[1].pop()), "49 records on page 1"),
        (mutate(lambda b: b.__setitem__(1, "not a list")), "not a list"),
        (mutate(lambda b: b.append({})), "unexpected shape"),
        ({"page": 1}, "unexpected shape"),
    ],
)
def test_response_validation_rejects(config, body, reason):
    request = request_for(config, "SP.POP.TOTL")
    with pytest.raises(ResponseRejected, match=reason):
        validate_page(request, 1, 50, body, frozenset(config.countries), config.years)


def test_response_validation_accepts_the_recorded_page(config):
    request = request_for(config, "SP.POP.TOTL")
    meta, records = validate_page(request, 1, 50, valid_page_1(), frozenset(config.countries), config.years)
    assert meta["total"] == 63 and len(records) == 50


def test_response_validation_checks_country_records_too(config):
    request = plan_requests(config)[0]
    body = recorded_json(country_url())
    body[1][0]["id"] = "FRA"
    with pytest.raises(ResponseRejected, match="country record 'FRA'"):
        validate_page(request, 1, 50, body, frozenset(config.countries), config.years)


def test_response_validation_flags_a_page_from_another_release(config):
    request = request_for(config, "SP.POP.TOTL")
    body = recorded_json(POP_P2)
    with pytest.raises(SourceChanged):
        validate_page(request, 2, 50, body, frozenset(config.countries), config.years, expected=(2, 63, "2026-07-01"))


def test_response_validation_rejects_a_non_json_content_type(api, client, config):
    api.queue(POP_P1, Reply(b"<xml/>", content_type="text/xml"), Reply(recorded()[POP_P1]))

    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))

    assert pages[0].attempt == 2


def test_response_validation_body_is_stored_exactly_as_received(api, client, config):
    pages = client.fetch_all(request_for(config, "SP.POP.TOTL"))
    assert pages[0].body.encode() == recorded()[POP_P1]
    assert json.loads(pages[0].body) == valid_page_1()
