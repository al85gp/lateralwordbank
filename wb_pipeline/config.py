"""Load and validate config/pipeline.yml, the single source of pipeline scope."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

POPULATION = "SP.POP.TOTL"
AGGREGATIONS = ("sum", "population_weighted_mean")
MAX_PER_PAGE = 32767  # the API answers 32768 and above with an HTTP 400 page (api_calls/04)

ISO3 = re.compile(r"^[A-Z]{3}$")
INDICATOR_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._]*$")


class ConfigError(ValueError):
    """The configuration is invalid; the message says what to fix."""


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    per_page: int
    validation_attempts: int


@dataclass(frozen=True)
class Indicator:
    code: str
    unit: str
    aggregation: str


@dataclass(frozen=True)
class PipelineConfig:
    api: ApiConfig
    year_start: int
    year_end: int
    countries: tuple[str, ...]
    indicators: tuple[Indicator, ...]

    @property
    def years(self) -> range:
        return range(self.year_start, self.year_end + 1)

    def snapshot(self) -> dict[str, Any]:
        """The scope of one load, stored in raw.run_scope."""
        return {
            "countries": list(self.countries),
            "indicators": [asdict(i) for i in self.indicators],
            "year_start": self.year_start,
            "year_end": self.year_end,
            "per_page": self.api.per_page,
        }


def load_config(path: str | Path) -> PipelineConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return parse_config(raw)


def parse_config(raw: Any) -> PipelineConfig:
    if not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")
    _only_keys(raw, {"api", "years", "countries", "indicators"}, "config")

    api_raw = _mapping(raw.get("api"), "api")
    _only_keys(api_raw, {"base_url", "per_page", "validation_attempts"}, "api")
    base_url = api_raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise ConfigError("api.base_url must be an https:// URL")
    per_page = _int(api_raw.get("per_page"), "api.per_page")
    if not 1 <= per_page <= MAX_PER_PAGE:
        raise ConfigError(f"api.per_page must be between 1 and {MAX_PER_PAGE}, got {per_page}")
    attempts = _int(api_raw.get("validation_attempts"), "api.validation_attempts")
    if attempts < 1:
        raise ConfigError(f"api.validation_attempts must be at least 1, got {attempts}")
    api = ApiConfig(base_url=base_url.rstrip("/"), per_page=per_page, validation_attempts=attempts)

    years = _mapping(raw.get("years"), "years")
    _only_keys(years, {"start", "end"}, "years")
    start, end = _int(years.get("start"), "years.start"), _int(years.get("end"), "years.end")
    if start > end:
        raise ConfigError(f"years.start ({start}) is after years.end ({end})")

    countries = raw.get("countries")
    if not isinstance(countries, list) or not countries:
        raise ConfigError("countries must be a non-empty list of ISO3 codes")
    for code in countries:
        if not isinstance(code, str) or not ISO3.match(code):
            raise ConfigError(f"countries: {code!r} is not an upper-case ISO3 code")
    _no_duplicates(countries, "countries")

    indicators_raw = raw.get("indicators")
    if not isinstance(indicators_raw, list) or not indicators_raw:
        raise ConfigError("indicators must be a non-empty list")
    indicators = []
    for i, item in enumerate(indicators_raw):
        where = f"indicators[{i}]"
        item = _mapping(item, where)
        _only_keys(item, {"code", "unit", "aggregation"}, where)
        code, unit, aggregation = item.get("code"), item.get("unit"), item.get("aggregation")
        if not isinstance(code, str) or not INDICATOR_CODE.match(code):
            raise ConfigError(f"{where}.code: {code!r} is not an upper-case World Bank indicator code")
        if not isinstance(unit, str) or not unit.strip():
            raise ConfigError(f"{where}.unit is required")
        if aggregation not in AGGREGATIONS:
            raise ConfigError(f"{where}.aggregation must be one of {', '.join(AGGREGATIONS)}, got {aggregation!r}")
        indicators.append(Indicator(code=code, unit=unit, aggregation=aggregation))
    codes = [i.code for i in indicators]
    _no_duplicates(codes, "indicators")
    if POPULATION not in codes and any(i.aggregation == "population_weighted_mean" for i in indicators):
        raise ConfigError(f"population_weighted_mean needs {POPULATION} in indicators as the weight")

    return PipelineConfig(
        api=api,
        year_start=start,
        year_end=end,
        countries=tuple(countries),
        indicators=tuple(indicators),
    )


def _mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping")
    return value


def _only_keys(mapping: dict, allowed: set[str], where: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(map(str, unknown)))}")


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer, got {value!r}")
    return value


def _no_duplicates(values: list[str], where: str) -> None:
    seen, dupes = set(), set()
    for v in values:
        (dupes if v in seen else seen).add(v)
    if dupes:
        raise ConfigError(f"{where}: duplicate(s) {', '.join(sorted(dupes))}")
