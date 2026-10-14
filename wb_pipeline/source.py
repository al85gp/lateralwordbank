"""dlt source for the World Bank API v2.

The API returns its errors with HTTP 200, and it can return HTTP 200 with a well-formed body
that answers a *different* query (a caching problem, see api_calls/02 and 05). So every page is
validated against the request that produced it before anything is kept, and a rejected page is
requested again with a fresh cache-buster, because the same URL returns the same cached bytes.

HTTP-level failures (timeouts, connection errors, 429, 5xx) are retried by dlt's requests client
with the settings in .dlt/config.toml; any other 4xx fails fast.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterator

import dlt
from dlt.common.configuration import resolve_configuration
from dlt.common.configuration.specs import RuntimeConfiguration
from dlt.sources.helpers.requests import Client

from wb_pipeline.config import PipelineConfig

log = logging.getLogger(__name__)

WDI_SOURCE_ID = "2"  # World Development Indicators
TRANSIENT_ERROR_IDS = frozenset({"105", "199"})  # service unavailable, unexpected error


class WorldBankError(RuntimeError):
    """The API did not deliver valid data; the run must fail."""


class ApiErrorResponse(WorldBankError):
    """The API answered with a permanent error body, such as id 120 (invalid value)."""


class ResponseRejected(Exception):
    """A body failed validation; the page is requested again with a cache-buster."""


class SourceChanged(Exception):
    """A later page disagrees with page 1 on pages, total or lastupdated."""


@dataclass(frozen=True)
class ApiRequest:
    """One logical call. Each page of it has one canonical URL."""

    endpoint: str  # "country" or "indicator"
    path: str
    params: tuple[tuple[str, str], ...]
    indicator_code: str | None = None

    def url(self, base_url: str, page: int) -> str:
        query = "&".join(f"{k}={v}" for k, v in self.params)
        return f"{base_url}{self.path}?{query}&page={page}"

    def describe(self) -> str:
        return self.indicator_code or self.endpoint


def plan_requests(config: PipelineConfig) -> list[ApiRequest]:
    """The country call plus one call per indicator, all countries in each request."""
    countries = ";".join(config.countries)
    per_page = str(config.api.per_page)
    years = f"{config.year_start}:{config.year_end}"
    requests = [ApiRequest("country", f"/country/{countries}", (("format", "json"), ("per_page", per_page)))]
    for indicator in config.indicators:
        requests.append(
            ApiRequest(
                "indicator",
                f"/country/{countries}/indicator/{indicator.code}",
                (("format", "json"), ("date", years), ("per_page", per_page)),
                indicator.code,
            )
        )
    return requests


@dataclass(frozen=True)
class Page:
    """One accepted response, as stored in raw.api_responses."""

    request: ApiRequest
    request_url: str
    fetched_url: str
    attempt: int
    page: int
    pages: int
    total: int
    source_last_updated: str | None
    http_status: int
    fetched_at: datetime
    body_sha256: str
    body: str
    records: list[dict[str, Any]]

    @property
    def signature(self) -> tuple[int, int, str | None]:
        return self.pages, self.total, self.source_last_updated

    def as_row(self) -> dict[str, Any]:
        return {
            "endpoint": self.request.endpoint,
            "indicator_code": self.request.indicator_code,
            "request_url": self.request_url,
            "fetched_url": self.fetched_url,
            "attempt": self.attempt,
            "page": self.page,
            "pages": self.pages,
            "total": self.total,
            "source_last_updated": date.fromisoformat(self.source_last_updated) if self.source_last_updated else None,
            "http_status": self.http_status,
            "fetched_at": self.fetched_at,
            "body_sha256": self.body_sha256,
            "body": self.body,
        }


def validate_page(
    request: ApiRequest,
    page_no: int,
    per_page: int,
    body: Any,
    countries: frozenset[str],
    years: range,
    expected: tuple[int, int, str | None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Check a parsed body against the request that produced it.

    Raises ResponseRejected (retry with a cache-buster), ApiErrorResponse (fail now) or
    SourceChanged (restart the request from page 1). Returns (metadata, records).
    """
    # 1. The API's error format: [{"message": [{"id": "120", "key": ..., "value": ...}]}]
    if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict) and "message" in body[0]:
        messages = [m for m in body[0].get("message") or [] if isinstance(m, dict)]
        ids = {str(m.get("id")) for m in messages}
        text = "; ".join(f"id {m.get('id')} {m.get('key')}: {m.get('value')}" for m in messages) or "no detail"
        if ids and ids <= TRANSIENT_ERROR_IDS:
            raise ResponseRejected(f"transient API error ({text})")
        raise ApiErrorResponse(f"{request.describe()}: the API returned an error ({text})")

    # 2. Shape: [metadata, records or null]
    if not (isinstance(body, list) and len(body) == 2 and isinstance(body[0], dict)):
        raise ResponseRejected("unexpected shape, expected [metadata, records]")
    meta, records = body
    try:
        page, pages, total, meta_per_page = (int(meta[k]) for k in ("page", "pages", "total", "per_page"))
    except (KeyError, TypeError, ValueError):
        raise ResponseRejected("metadata lacks integer page, pages, total and per_page") from None
    records = [] if records is None else records  # null means zero matches
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ResponseRejected("records is not a list of objects")
    if total == 0:
        if records:
            raise ResponseRejected("total is 0 but records were returned")
        return meta, records

    # 3. Metadata matches the request
    if page != page_no:
        raise ResponseRejected(f"page is {page}, requested {page_no}")
    if meta_per_page != per_page:
        raise ResponseRejected(f"per_page is {meta_per_page}, requested {per_page}")
    if request.endpoint == "indicator" and meta.get("sourceid") != WDI_SOURCE_ID:
        raise ResponseRejected(f"sourceid is {meta.get('sourceid')!r}, expected {WDI_SOURCE_ID!r}")

    # 4. Every record matches the request
    for r in records:
        if request.endpoint == "indicator":
            indicator_id = (r.get("indicator") or {}).get("id")
            if indicator_id != request.indicator_code:
                raise ResponseRejected(f"record for indicator {indicator_id!r}, requested {request.indicator_code!r}")
            if r.get("countryiso3code") not in countries:
                raise ResponseRejected(f"record for country {r.get('countryiso3code')!r}, not requested")
            try:
                year = int(r.get("date"))
            except (TypeError, ValueError):
                raise ResponseRejected(f"record has a non-integer date {r.get('date')!r}") from None
            if year not in years:
                raise ResponseRejected(f"record for year {year}, outside {years.start}-{years.stop - 1}")
        elif r.get("id") not in countries:
            raise ResponseRejected(f"country record {r.get('id')!r}, not requested")

    # 5. Row count fits total
    expected_rows = max(0, min(per_page, total - (page_no - 1) * per_page))
    if len(records) != expected_rows:
        raise ResponseRejected(f"{len(records)} records on page {page_no}, expected {expected_rows} for total {total}")

    # 6. Consistent with page 1 of the same request
    if expected is not None and (pages, total, meta.get("lastupdated")) != expected:
        raise SourceChanged(f"page {page_no} has pages/total/lastupdated {(pages, total, meta.get('lastupdated'))}, page 1 had {expected}")
    return meta, records


class WorldBankClient:
    """Fetches and validates pages; retries a rejected HTTP 200 body with a fresh cache-buster."""

    def __init__(
        self,
        config: PipelineConfig,
        load_id: str,
        runtime: RuntimeConfiguration | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        runtime = runtime or resolve_configuration(RuntimeConfiguration())
        self.http = Client(
            request_timeout=runtime.request_timeout,
            request_max_attempts=runtime.request_max_attempts,
            request_backoff_factor=runtime.request_backoff_factor,
            request_max_retry_delay=runtime.request_max_retry_delay,
        )
        self.backoff_factor = runtime.request_backoff_factor
        self.max_delay = runtime.request_max_retry_delay
        self.base_url = config.api.base_url
        self.per_page = config.api.per_page
        self.max_attempts = config.api.validation_attempts
        self.countries = frozenset(config.countries)
        self.years = config.years
        self.load_id = load_id
        self.sleep = sleep
        self.attempts: Counter[str] = Counter()  # requests sent per canonical URL in this run

    def fetch_all(self, request: ApiRequest) -> list[Page]:
        """Every page of one request, mutually consistent and complete."""
        while True:
            try:
                first = self.fetch_page(request, 1)
                pages = [first]
                for page_no in range(2, first.pages + 1):
                    pages.append(self.fetch_page(request, page_no, expected=first.signature))
                break
            except SourceChanged as e:
                # The source changed mid-run or a stale copy was served. Page 1 is requested
                # again with a cache-buster, within its attempt budget.
                log.warning("%s: %s; restarting from page 1", request.describe(), e)
        self._check_complete(request, pages)
        return pages

    def fetch_page(self, request: ApiRequest, page_no: int, expected: tuple | None = None) -> Page:
        canonical = request.url(self.base_url, page_no)
        last_rejection = None
        while True:
            attempt = self.attempts[canonical] + 1
            if attempt > self.max_attempts:
                raise WorldBankError(
                    f"no valid response for {canonical} after {self.max_attempts} attempts; "
                    f"last rejection: {last_rejection}"
                )
            self.attempts[canonical] = attempt
            if attempt > 1:
                self.sleep(min(self.backoff_factor * 2 ** (attempt - 2), self.max_delay))
            # Only a retry carries a token: a first attempt always sends the canonical URL.
            fetched_url = canonical if attempt == 1 else f"{canonical}&cb={self.load_id}-{attempt}"
            response = self.http.get(fetched_url)
            fetched_at = datetime.now(timezone.utc)
            content = response.content
            body_sha256 = hashlib.sha256(content).hexdigest()
            try:
                meta, records = self._parse_and_validate(request, page_no, response, content, expected)
            except ResponseRejected as e:
                last_rejection = str(e)
                log.warning("rejected %s (attempt %d): %s; body sha256 %s", fetched_url, attempt, e, body_sha256)
                continue
            if attempt > 1:
                log.warning("accepted %s on attempt %d", canonical, attempt)
            return Page(
                request=request,
                request_url=canonical,
                fetched_url=fetched_url,
                attempt=attempt,
                page=page_no,
                pages=int(meta["pages"]),
                total=int(meta["total"]),
                source_last_updated=meta.get("lastupdated"),
                http_status=response.status_code,
                fetched_at=fetched_at,
                body_sha256=body_sha256,
                body=content.decode("utf-8"),
                records=records,
            )

    def _parse_and_validate(self, request, page_no, response, content, expected):
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise ResponseRejected(f"content type is {content_type!r}, expected JSON")
        try:
            body = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ResponseRejected(f"body is not valid JSON ({e})") from None
        return validate_page(request, page_no, self.per_page, body, self.countries, self.years, expected)

    @staticmethod
    def _check_complete(request: ApiRequest, pages: list[Page]) -> None:
        records = [r for p in pages for r in p.records]
        total = pages[0].total
        if len(records) != total:
            raise WorldBankError(f"{request.describe()}: received {len(records)} records, the API reported total {total}")
        if request.endpoint == "indicator":
            keys = Counter((r["countryiso3code"], r["date"]) for r in records)
        else:
            keys = Counter(r["id"] for r in records)
        dupes = sorted(str(k) for k, n in keys.items() if n > 1)
        if dupes:
            raise WorldBankError(f"{request.describe()}: duplicate records for {', '.join(dupes)}")


API_RESPONSE_COLUMNS = {
    "endpoint": {"data_type": "text", "nullable": False},
    "indicator_code": {"data_type": "text"},
    "request_url": {"data_type": "text", "nullable": False},
    "fetched_url": {"data_type": "text", "nullable": False},
    "attempt": {"data_type": "bigint", "nullable": False},
    "page": {"data_type": "bigint", "nullable": False},
    "pages": {"data_type": "bigint", "nullable": False},
    "total": {"data_type": "bigint", "nullable": False},
    "source_last_updated": {"data_type": "date"},
    "http_status": {"data_type": "bigint", "nullable": False},
    "fetched_at": {"data_type": "timestamp", "nullable": False},
    "body_sha256": {"data_type": "text", "nullable": False},
    # Stored as text, exactly as received, so sha256(body) == body_sha256 and JSON paths still work.
    "body": {"data_type": "text", "nullable": False},
}

RUN_SCOPE_COLUMNS = {
    "countries": {"data_type": "json", "nullable": False},
    "indicators": {"data_type": "json", "nullable": False},
    "year_start": {"data_type": "bigint", "nullable": False},
    "year_end": {"data_type": "bigint", "nullable": False},
    "per_page": {"data_type": "bigint", "nullable": False},
}


@dlt.resource(name="run_scope", write_disposition="append", columns=RUN_SCOPE_COLUMNS)
def run_scope(pipeline_config: PipelineConfig) -> Iterator[dict[str, Any]]:
    """One row per load: what this load was asked to fetch."""
    yield pipeline_config.snapshot()


@dlt.resource(name="api_responses", write_disposition="append", columns=API_RESPONSE_COLUMNS)
def api_responses(pipeline_config: PipelineConfig) -> Iterator[dict[str, Any]]:
    """One row per accepted page. A request's pages are yielded only once all are valid."""
    load_id = dlt.current.load_package_state()["load_id"]
    client = WorldBankClient(pipeline_config, load_id)
    for request in plan_requests(pipeline_config):
        pages = client.fetch_all(request)
        log.info("%s: %d page(s), %d records", request.describe(), len(pages), pages[0].total)
        for page in pages:
            yield page.as_row()


@dlt.source(name="world_bank")
def world_bank_source(pipeline_config: PipelineConfig):
    return run_scope(pipeline_config), api_responses(pipeline_config)
