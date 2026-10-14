"""Shared test setup: a fake World Bank API that serves recorded responses, and temp workspaces.

requests-mock intercepts every HTTP call dlt's requests client makes; nothing reaches the network.
A request for a URL that has no recorded or queued response fails the test.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests_mock as requests_mock_lib

from wb_pipeline.config import load_config
from wb_pipeline.pipeline import REPO_ROOT, Paths

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://api.worldbank.org/v2"
COUNTRIES = "BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF"
INDICATORS = ("SP.POP.TOTL", "NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD", "SP.DYN.LE00.IN")
JSON = "application/json;charset=utf-8"


def country_url(page: int = 1) -> str:
    return f"{BASE}/country/{COUNTRIES}?format=json&per_page=50&page={page}"


def indicator_url(code: str, page: int) -> str:
    return f"{BASE}/country/{COUNTRIES}/indicator/{code}?format=json&date=2018:2024&per_page=50&page={page}"


def canonical_urls() -> list[str]:
    """Every URL a run sends, in order, when all responses are valid."""
    return [country_url()] + [indicator_url(code, page) for code in INDICATORS for page in (1, 2)]


def recorded() -> dict[str, bytes]:
    """URL -> body bytes, recorded from the live API (see each .meta.json sidecar)."""
    bodies = {}
    for meta_file in FIXTURES.glob("*.meta.json"):
        meta = json.loads(meta_file.read_text())
        bodies[meta["url"]] = meta_file.with_name(meta_file.name.replace(".meta.json", ".json")).read_bytes()
    return bodies


def recorded_json(url: str):
    return json.loads(recorded()[url])


@dataclass(frozen=True)
class Reply:
    body: bytes = b""
    status: int = 200
    content_type: str = JSON

    @classmethod
    def json(cls, obj) -> "Reply":
        return cls(json.dumps(obj).encode())


class FakeWorldBank:
    """Serves recorded responses by URL. A `cb` cache-buster is ignored when matching, as the real
    API ignores unknown parameters. `queue()` overrides a URL with replies served in order; the
    last one repeats. A reply may be an exception instance, which is raised."""

    def __init__(self, mocker: requests_mock_lib.Mocker) -> None:
        self.routes: dict[str, list] = {url: [Reply(body)] for url, body in recorded().items()}
        self.calls: list[str] = []
        mocker.get(requests_mock_lib.ANY, content=self._respond)

    def queue(self, url: str, *replies) -> None:
        self.routes[url] = list(replies)

    def calls_to(self, url: str) -> list[str]:
        return [c for c in self.calls if _strip_cache_buster(c) == url]

    def _respond(self, request, context):
        self.calls.append(request.url)
        replies = self.routes.get(_strip_cache_buster(request.url))
        if replies is None:
            raise AssertionError(f"unexpected request: {request.url}")
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, BaseException):
            raise reply
        context.status_code = reply.status
        context.headers["Content-Type"] = reply.content_type
        return reply.body


def _strip_cache_buster(url: str) -> str:
    return re.sub(r"&cb=[^&]*", "", url)


@pytest.fixture
def api(requests_mock) -> FakeWorldBank:
    return FakeWorldBank(requests_mock)


@pytest.fixture
def config():
    return load_config(REPO_ROOT / "config" / "pipeline.yml")


@pytest.fixture
def paths(tmp_path, monkeypatch) -> Paths:
    """A fresh warehouse, output folder and dlt working folder per test; no retry delays."""
    monkeypatch.setenv("RUNTIME__REQUEST_BACKOFF_FACTOR", "0")
    monkeypatch.setenv("RUNTIME__REQUEST_MAX_RETRY_DELAY", "0")
    warehouse = tmp_path / "warehouse.duckdb"
    monkeypatch.setenv("WAREHOUSE_PATH", str(warehouse))
    return Paths(
        warehouse=warehouse,
        output_dir=tmp_path / "output",
        pipelines_dir=tmp_path / "dlt_pipelines",
        dbt_project_dir=REPO_ROOT / "dbt",
        dbt_target_dir=tmp_path / "dbt_target",
        dbt_log_dir=tmp_path / "dbt_logs",
    )
