# World Bank Indicators Pipeline

A local, configuration-driven pipeline that pulls yearly country indicators from the public World Bank API, models them in DuckDB with dbt, and writes four analytics reports as CSV files. Every reported value can be traced back to the exact API response it came from, and missing data is reported as missing, never as zero.

| Scope | Value |
| --- | --- |
| Countries | BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF |
| Indicators | `SP.POP.TOTL`, `NY.GDP.MKTP.CD`, `NY.GDP.PCAP.CD`, `SP.DYN.LE00.IN` |
| Years | 2018–2024 |
| Stack | dlt (open source) for ingestion, DuckDB, dbt-core + dbt-duckdb, Docker Compose |

---

## Quick start

**Prerequisite:** Docker with Compose v2. Nothing else is needed on the host.

```bash
git clone https://github.com/al85gp/lateralwordbank.git && cd lateralwordbank
docker compose run --rm --build pipeline     # extract, dbt build, export CSVs to output/
docker compose run --rm test                 # every test, no network access
```

What you get:

| Output | Contents |
| --- | --- |
| `output/rpt_missing_or_incomplete.csv` | Country-indicator-year combinations with no value |
| `output/rpt_yoy_change.csv` | Year-over-year change per country and indicator |
| `output/rpt_latest_comparison.csv` | Each country's latest available year and value, per indicator |
| `output/rpt_income_group_by_year.csv` | Indicators aggregated by income group and year |
| `data/warehouse.duckdb` | The full warehouse, for ad-hoc queries |

Replay an earlier load without calling the API:

```bash
docker compose run --rm pipeline rebuild --load-id <load_id>
```

> **TODO (scaffold):** confirm both commands work from a fresh clone; note how long a full run takes.

---

## Requirement coverage

| ID | Requirement | How it is handled | Where | Proven by |
| --- | --- | --- | --- | --- |
| R-ING-01, R-ING-02 | Retrieve country metadata and indicator observations | One country call, one call per indicator, all nine countries per request | `source.py` | `test_end_to_end` |
| R-ING-03 | Handle paging | `per_page: 50`, so every indicator has two pages; a page loop reads `pages` from page 1 and fetches the rest; rows received are checked against `total` | `source.py` | `test_paging` |
| R-ING-04 | Handle API failures | dlt retries 429, 5xx and timeouts with backoff (`.dlt/config.toml`); other 4xx fail fast; every HTTP 200 body is validated against its request, and a rejected body is refetched with a fresh cache-buster; a failed extract loads nothing | `source.py`, `.dlt/config.toml` | `test_retries`, `test_error_payload`, `test_response_validation`, `test_wrong_body_retry`, `test_failed_run_not_exported` |
| R-ING-05, D-07 | Every output value traceable to its API response | `load_id`, `request_url`, `body_sha256` and `record_index` on every row; verbatim body, the URL actually sent and the attempt number in raw; lineage query below | `raw.api_responses`, every model | Lineage test, `test_end_to_end` |
| R-ING-06 | One documented command from a clean checkout | `docker compose run --rm --build pipeline` | `docker-compose.yml` | Fresh-clone run |
| R-ING-07, D-08 | A rerun creates no duplicate analytical records | Raw is append-only; marts rebuild from the latest completed load (`_dlt_loads.status = 0`); `unique` on every grain | dbt marts | `test_idempotent_rerun` |
| R-ING-08 | No cloud resources or accounts | Public API, open-source dlt (no paid tier), a local DuckDB file, local Docker | Whole repo | Nothing to sign up for |
| R-MOD-01 | Raw separate from cleaned and analytics-ready | `raw` written only by dlt; `staging`, `marts`, `reporting` only by dbt | DuckDB schemas | `rebuild` restores everything from raw alone |
| R-MOD-02 | Grain, keys and relationships documented | Data model section; a description on every dbt model and column | `dbt/models/*.yml` | `unique`, `not_null`, `relationships` tests |
| R-MOD-03 | Standardized for analysts | ISO3 key, integer year, flattened objects, snake_case, typed values | Staging models | `accepted_values` and range tests |
| R-MOD-04, D-09 | Add a country or indicator by configuration | One list entry in `config/pipeline.yml` | `config.py` | `test_config_validation`, spine-completeness test |
| D-01, D-02 | Missing data is an outcome, not zero | Spine-based fact with `value_status` | `fct_indicator_year` | `accepted_values`, spine-completeness test |
| R-ANL-01 – R-ANL-04 | The four analytics questions | One dbt view each, exported to CSV | `dbt/models/reporting/`, `output/` | `test_end_to_end` |
| R-ANL-05 | *Optional* extra view or dashboard | Not built; CSV-only output (see Trade-offs) | — | — |
| R-DQ-01 | Correctness and safe reprocessing | dbt tests on every run, pytest suite | `dbt/tests/`, `tests/` | Data quality and tests section |
| R-DQ-02, D-06 | Related indicators consistent | GDP per capita within 1% of GDP ÷ population | Singular dbt test (warn) | Warning rows in the run output |
| R-DQ-03, D-10 | Tests never call the live API | `requests-mock` with recorded fixtures; the `test` service has no network | `tests/`, `docker-compose.yml` | Any live call fails the suite |
| R-PRD-01 – R-PRD-05 | Production design note | Scheduling, late data, monitoring, backfills, cloud mapping | Production design note section | — |
| R-DEL-01 | Runs locally by following the README | Docker Compose, nothing else on the host | Quick start | Fresh-clone run |
| R-DEL-02 | Setup, assumptions, trade-offs, decisions, AI usage | One section each in this README | `README.md` | — |
| R-DEL-03 | Sample output of the reporting queries | Committed CSVs | `output/*.csv` | — |
| R-DEL-04 | Git repository with commit history | One commit per build step | This repository | `git log` |

---

## Architecture

```text
World Bank API v2              config/pipeline.yml
        │                              │
        └───────────────┬──────────────┘
                        ▼
   dlt source  (source.py)      page loop, dlt retries, response validation, cache-buster retries
                        ▼
   raw  (DuckDB dataset)        raw.api_responses, raw.run_scope, raw._dlt_loads; append-only
                        ▼
   staging  (dbt views)         latest completed load only; parse, flatten, cast
                        ▼
   marts  (dbt tables)          dim_country, dim_indicator, fct_indicator_year
                        ▼
   reporting  (dbt views)       one view per analytics question
                        ▼
   output/*.csv                 swapped in only after dbt build passes
```

One Compose service, `pipeline`, runs the dlt extract and load, then `dbt build`, then the CSV export, and stops at the first failure with a non-zero exit code. `data/` and `output/` are mounted from the host, so the warehouse and the CSVs outlive the container.

The raw layer is the only state that matters: drop every schema below it and `rebuild` restores them exactly.

---

## Repository layout

```text
lateralwordbank/
├── README.md
├── Dockerfile                # python:3.12-slim, pinned requirements
├── docker-compose.yml        # services: pipeline, test (network_mode: none); mounts data/ and output/
├── requirements.txt          # dlt[duckdb], dbt-core, dbt-duckdb, pyyaml (pinned)
├── requirements-dev.txt      # pytest, requests-mock
├── config/
│   └── pipeline.yml          # countries, indicators, years, aggregation
├── .dlt/
│   └── config.toml           # HTTP timeout, retry attempts, backoff
├── wb_pipeline/
│   ├── __main__.py           # run | rebuild
│   ├── config.py             # load + validate pipeline.yml
│   ├── source.py             # dlt source: page loop, resources, response validation, cache-buster retries
│   └── export.py             # reporting views to a temp folder, swapped into output/ on green
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml          # duckdb at /app/data/warehouse.duckdb
│   ├── macros/generate_schema_name.sql
│   ├── models/
│   │   ├── staging/          # stg_wb__scope, stg_wb__countries, stg_wb__observations
│   │   ├── marts/            # dim_country, dim_indicator, fct_indicator_year
│   │   └── reporting/        # the four rpt_* views
│   └── tests/                # singular SQL tests
├── tests/
│   ├── fixtures/             # recorded API pages (JSON)
│   └── test_*.py
├── output/                   # committed sample CSVs
└── data/                     # gitignored: warehouse.duckdb
```

---

## Configuration: adding a country or indicator

`config/pipeline.yml` sets scope and is the only file to edit:

```yaml
api:
  base_url: https://api.worldbank.org/v2
  per_page: 50            # 63 rows per indicator -> 2 pages, so paging runs on every real run
  validation_attempts: 4  # tries per page when an HTTP 200 body fails validation
years: {start: 2018, end: 2024}
countries: [BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF]
indicators:
  - {code: SP.POP.TOTL,    unit: people,      aggregation: sum}
  - {code: NY.GDP.MKTP.CD, unit: current USD, aggregation: sum}
  - {code: NY.GDP.PCAP.CD, unit: current USD, aggregation: population_weighted_mean}
  - {code: SP.DYN.LE00.IN, unit: years,       aggregation: population_weighted_mean}
```

- **Add a country:** append its ISO3 code to `countries`.
- **Add an indicator:** append its code, unit and income-group aggregation (`sum` or `population_weighted_mean`).
- **Change years:** edit `years`.

Then rerun the pipeline. The expected spine, the completeness test and every report widen automatically.

`config.py` rejects invalid ISO3 codes, duplicates, `start > end`, unknown aggregations, `validation_attempts` below 1, and `population_weighted_mean` when `SP.POP.TOTL` is not in scope.

HTTP behavior lives in `.dlt/config.toml`:

```toml
[runtime]
request_timeout = 90          # an uncached response took 45 s (api_calls/01)
request_max_attempts = 5
request_backoff_factor = 2
request_max_retry_delay = 60
```

---

## Ingestion

### Calls

| Call | Path and params | Pages |
| --- | --- | --- |
| Country metadata | `/country/BRA;CAN;…;ZAF?format=json&per_page=50` | 1 |
| One per indicator | `/country/BRA;CAN;…;ZAF/indicator/<code>?format=json&date=2018:2024&per_page=50&page=<n>` | 2 |

That is nine requests per run. These are the canonical URLs: a first attempt always sends exactly this URL, and only a retry of a rejected HTTP 200 body adds `&cb=<load_id>-<attempt>` (see Failure handling).

### dlt source

`wb_pipeline/source.py` defines one `@dlt.source` with two resources, both `write_disposition: append`, loaded into dataset `raw`:

- `run_scope` — one row per load: the config snapshot of countries, indicators with unit and aggregation, and years.
- `api_responses` — one row per accepted page: `endpoint`, `indicator_code`, `request_url` (canonical), `fetched_url` (the URL actually sent), `attempt`, `page`, `pages`, `total`, `source_last_updated`, `http_status`, `fetched_at`, `body_sha256`, and `body` declared as a `json` column so dlt stores it verbatim instead of unnesting it into child tables.

HTTP calls go through dlt's `requests` helper, so the timeout and retry settings in `.dlt/config.toml` apply to every request.

### Paging

`per_page` is 50, so each indicator's 63 rows arrive on two pages and paging runs on every real run and every test.

dlt's paginators move on to the next page once a response arrives. They have no built-in way to reject an HTTP 200 page and request the same page again from a different URL, and that is what the wrong-body problem below needs. So `source.py` runs the page loop itself:

1. Fetch and validate page 1, then read `pages` from its metadata.
2. Fetch and validate pages 2 to `pages`. The loop is driven by `pages`, never by an empty page: a page past the end returns HTTP 200 with `[]` (api_calls/05).
3. Every page of one indicator must agree on `pages`, `total` and `lastupdated`. A disagreement means the source changed mid-run or a stale copy was served, so the indicator restarts from page 1, within the same attempt budget.
4. After the last page, the rows received must equal `total`, with no duplicate (country, year). A mismatch fails the run.

### Failure handling

What dlt handles, at the HTTP level:

- Timeouts (90 s), connection errors, 429 and 5xx are retried with exponential backoff, honoring `Retry-After`, within the limits in `.dlt/config.toml`.
- Any other 4xx fails fast.

What our code adds: **HTTP 200 does not mean success.** The API returns its errors with HTTP 200. It can also return HTTP 200 with a well-formed body that answers a *different* query; this was reproduced several times while mapping the API ([api_calls/02](api_calls/02_indicator_observations.md), [api_calls/05](api_calls/05_errors_and_resilience.md)). So every response is validated against the request that produced it before anything is kept:

1. **Content type** is JSON. The API's 400 pages are HTML, and its default format is XML.
2. **Not an error body.** `[{"message": [...]}]` is the API's error format. Ids 105 (service unavailable) and 199 (unexpected error) are transient and retried as below. Any other id, such as 120 (invalid value) or 175 (archived indicator), is a configuration or code problem and fails the run at once. An error body is never loaded as empty data.
3. **Shape.** A two-element list: metadata with integer `page`, `pages` and `total`, then a list of records, or `null` for zero matches.
4. **Metadata matches the request.** `page` and `per_page` equal what was sent, and `sourceid == "2"` (World Development Indicators).
5. **Every record matches the request.** `indicator.id` equals the requested code, `countryiso3code` is in the requested countries, and `int(date)` is within the configured years. On the country call, every `id` is a requested country.
6. **Row count.** The page holds `min(per_page, total - (page - 1) * per_page)` records.

Retrying a rejected HTTP 200 body:

- A plain retry is useless. Cloudflare caches the exact URL for up to 24 hours, so the same URL returns the same wrong bytes.
- Each retry therefore appends a fresh token, `&cb=<load_id>-<attempt>`. The API ignores unknown parameters, so the token changes the cache key but not the query.
- The token usually gets a fresh response but not always, because an inner cache sometimes ignores it (api_calls/02, finding C7). Every attempt is validated again.
- After `api.validation_attempts` attempts (default 4), with the same backoff as HTTP retries, the run fails.
- A rejected attempt is never loaded. It is logged with its URL, the check that failed and the body's sha256. The accepted page records its `attempt`, so retries are visible in raw.

How a failure plays out:

- An exception during extract stops `pipeline.run()` before load, so nothing is written.
- A failure inside dlt's load step can leave rows behind, but without a completed entry in `_dlt_loads`; staging reads completed loads only (`status = 0`).
- Each run first drops any pending load packages a crashed run left behind.
- The process exits non-zero with the error and dlt's trace, and `output/` keeps the last good CSVs.

> **TODO (extract and load):** record any API behavior the live run revealed (error format, empty pages, null values, response time) and how the code handles it.

### Raw tables

| Table | Grain and key | Notes |
| --- | --- | --- |
| `raw.api_responses` | One accepted page per load; (`_dlt_load_id`, `request_url`) | Verbatim `body`, `body_sha256`, request metadata, `fetched_url` and `attempt`; rejected attempts are not stored |
| `raw.run_scope` | One row per load; `_dlt_load_id` | Config snapshot; defines what was expected |
| `raw._dlt_loads` | One row per load; `load_id` | Written by dlt; `status = 0` marks a completed load |

### Traceability

1. Every staging, mart and reporting row carries `load_id`, `request_url`, `body_sha256` and `record_index`, the record's position in that page's data array.
2. `raw.api_responses` holds the verbatim body for that load and URL, so element `record_index` of the body's data array is the exact JSON object behind the value.
3. `request_url` is the canonical URL from the Calls table. It is the same for every load and every attempt, which makes it the join key and keeps lineage stable across runs.
4. `fetched_url` is the URL actually sent for the accepted body. It equals `request_url` when `attempt = 1`. On a retry it is `request_url` plus `&cb=<load_id>-<attempt>`, so raw shows exactly which request produced the stored bytes.
5. Both URLs are complete. Pasting either into a browser runs the same query against the live API, because the API ignores the `cb` token.
6. A `not_returned` row has no lineage by definition; `raw.run_scope` shows it was requested.

Lineage query for any value:

```sql
select f.country_iso3, f.indicator_code, f.year, f.value,
       r.request_url, r.fetched_url, r.attempt, r.fetched_at,
       json_extract(r.body, '$[1][' || f.record_index || ']') as source_record
from marts.fct_indicator_year f
join raw.api_responses r
  on r._dlt_load_id = f.load_id and r.request_url = f.request_url
where f.country_iso3 = 'BRA' and f.indicator_code = 'SP.POP.TOTL' and f.year = 2023;
```

### Idempotency

Raw is append-only: a rerun adds one load and about nine page rows. dbt rebuilds the marts as tables from the latest completed load, with `unique` tests on every grain, so unchanged source data produces identical facts. An unchanged `body_sha256` shows the source did not move. A run that needed cache-buster retries produces the same facts too: marts and CSVs carry the canonical `request_url`, never `fetched_url`. `test_idempotent_rerun` runs the pipeline twice and diffs the fact table.

---

## Data model

The fact table's grain is the full requested spine, country × indicator × year, so a missing value is a row with a status rather than an absent row. With the default config that is 9 × 4 × 7 = 252 rows.

| Model | Layer | Materialized | Grain | Business key | Relationships |
| --- | --- | --- | --- | --- | --- |
| `stg_wb__scope` | staging | view | Requested country × indicator × year | (`country_iso3`, `indicator_code`, `year`) | Built from `raw.run_scope` of the selected load |
| `stg_wb__countries` | staging | view | Country in the selected load | `country_iso3` | Parsed from the country responses |
| `stg_wb__observations` | staging | view | API record | (`country_iso3`, `indicator_code`, `year`) | Parsed from indicator responses with `record_index` |
| `dim_country` | marts | table | Country | `country_iso3` | One to many with the fact |
| `dim_indicator` | marts | table | Indicator | `indicator_code` | One to many with the fact |
| `fct_indicator_year` | marts | table | Country × indicator × year in scope | (`country_iso3`, `indicator_code`, `year`) | Many to one with both dims |

`fct_indicator_year` columns:

- `country_iso3`, `indicator_code`, `year` — the business key.
- `value` — DOUBLE, nullable. Never coerced to 0.
- `value_status` — `reported`, `null_in_source` (the API returned the row with a null value) or `not_returned` (requested, but no row came back).
- `obs_status`, `decimals` — passed through as the API sends them.
- `source_last_updated` — the API's `lastupdated` for that indicator's source.
- `load_id`, `request_url`, `body_sha256`, `record_index` — lineage to the exact raw record; null only when `not_returned`.

`dim_country`: `country_iso3`, `iso2_code`, `country_name`, `region_name`, `income_group_code`, `income_group_name`.
`dim_indicator`: `indicator_code`, `indicator_name`, `unit`, `aggregation`, `source_id`.

**Standardization:** one country key (ISO3) everywhere, because the API mixes ISO2 `country.id` and `countryiso3code`; `year` as an integer instead of the string `date`; nested `{id, value}` objects flattened; snake_case names throughout.

> **TODO (staging and marts):** confirm the column list against the real payload and note anything the API returned that the model drops.

---

## Reporting outputs

| Question | View | Grain | Logic |
| --- | --- | --- | --- |
| Which combinations are missing or incomplete? | `rpt_missing_or_incomplete` | Country × indicator × year not `reported` | Includes `value_status` plus the series' `years_reported` and `years_expected` |
| Year-over-year change | `rpt_yoy_change` | Country × indicator × year | Prior is exactly `year - 1`. When the current or prior value is missing, `abs_change` and `pct_change` are both null. When the prior value is 0, `abs_change` is still computed and only `pct_change` is null (D-03). `change_status` says which case applies: `ok`, `first_year_in_scope`, `no_current_value`, `no_prior_value`, `prior_is_zero` |
| Countries at the latest year | `rpt_latest_comparison` | Indicator × country | Per D-04, `latest_year` is each country's own latest year with a reported (non-null) value, so it can differ between countries. Shows that year's `value` and its `rank` among countries for the indicator. `years_behind` is the most recent `latest_year` of any country minus this country's, so a comparison across different years is visible. A country with no reported value in scope has null `latest_year`, `value` and `rank` |
| Income groups by year | `rpt_income_group_by_year` | Income group × indicator × year | `sum` for population and GDP; `population_weighted_mean` for GDP per capita and life expectancy; `countries_in_group`, `countries_reporting` and `is_complete` show coverage |

CSVs are ordered by their keys and carry `request_url`, `body_sha256` and `record_index` as lineage (not `load_id`), so a rerun on unchanged data produces a zero-line git diff.

- 2018 has no prior year in scope, so its YoY status is `first_year_in_scope`. Set `years.start: 2017` to change that.
- India is the only lower-middle-income country in scope, so that group is a single country.

> **TODO (reporting):** add a two-line summary of what each CSV shows on the real data.

---

## Data quality and tests

### dbt tests (every run)

A failing `error` test stops the run before export, so `output/` keeps the last good CSVs. A `warn` test prints its rows and the run continues.

| Check | Implemented as | Severity |
| --- | --- | --- |
| Keys unique and not null on every model | `unique`, `not_null` | error |
| Fact rows point at existing dims | `relationships` | error |
| `value_status` is one of three values | `accepted_values` | error |
| Spine complete: fact rows = countries × indicators × years in the load's scope | singular SQL | error |
| No duplicate API records per key within a load | singular SQL | error |
| Every non-null value has `request_url` and `body_sha256` | singular SQL | error |
| Every country has an income group | `not_null` | error |
| Population and GDP above 0; life expectancy 20–100 years | singular SQL | error |
| GDP per capita within 1% of GDP ÷ population | singular SQL | warn |
| GDP and population present but GDP per capita missing | singular SQL | warn |
| Population changes more than 10% year over year | singular SQL | warn |

The consistency checks warn rather than fail: the source revises indicators on different dates, so a mismatch is usually revision timing, not a pipeline bug.

### pytest (no network)

`requests-mock` intercepts every HTTP call dlt makes and serves JSON from `tests/fixtures/`, recorded once from the live API. The fixtures are the exact canonical URLs a real run sends: the country call plus pages 1 and 2 at `per_page=50` for all four indicators, copied from `api_calls/samples/pagination/`. Joined, the two pages of each indicator match the single-page golden recordings in `api_calls/samples/observations/` value for value. Integration tests run the dlt pipeline into a temporary DuckDB file and call `dbt build` through dbt's Python runner, with dlt's retry delays set to zero. The `test` service runs with `network_mode: none`, so any accidental live call fails the suite.

| Test | Proves |
| --- | --- |
| `test_paging` | The recorded two-page responses yield all 63 rows per indicator; a count that disagrees with `total` fails; a page 2 whose `lastupdated` differs from page 1 restarts the indicator |
| `test_retries` | 503, 503, 200 succeeds on the third attempt; five 503s fail the run; a timeout is retried |
| `test_error_payload` | HTTP 200 with a `message` body is never loaded as empty data; ids 105 and 199 are retried with a cache-buster; id 120 fails at once |
| `test_response_validation` | Each check rejects a body: wrong `indicator.id`, a country or year outside the request, wrong `page` or `per_page`, `sourceid` other than `"2"`, a row count that doesn't fit `total` |
| `test_wrong_body_retry` | Page 1 of `NY.GDP.MKTP.CD` is first served the recorded `SP.POP.TOTL` page 1, a real response to a different query, as the cache does. The retry carries `&cb=<load_id>-2`, gets the right body, and loads it with `attempt = 2` and the canonical `request_url`. Wrong bodies on every attempt fail the run with nothing loaded |
| `test_config_validation` | Bad ISO3 codes, duplicates, `validation_attempts` below 1 and a weighted mean without population are rejected |
| `test_idempotent_rerun` | Two runs on the same fixtures: identical fact table, still 252 rows |
| `test_correction` | A changed value in run two shows in the fact; raw keeps both bodies |
| `test_failed_run_not_exported` | A run that fails partway adds no completed load and leaves `output/` untouched |
| `test_bad_data_blocks_export` | A seeded duplicate fails `dbt build`; `output/` stays untouched |
| `test_end_to_end` | Fixture run, `dbt build` passes, four CSVs written with the expected columns |

> **TODO (pytest):** update this table if tests are added, renamed or dropped.

---

## Key decisions walkthrough

| Decision | Alternative considered | Why |
| --- | --- | --- |
| Open-source dlt for ingestion | Plain `requests` | Retries, atomic load packages and a load log come built in; paging, error-body and `total` checks stay in our code, where a reviewer can read them |
| DuckDB | PostgreSQL, Snowflake | No server, no account; SQL close to Snowflake's |
| Spine-based fact table | Fact of returned rows only | Missing data becomes an explicit, testable row |
| Raw append-only with full response bodies | Overwrite raw each run | Exact traceability and replay of any load |
| Analytics read the latest completed load | Merge latest value per key | Failed loads are never visible; values removed at the source disappear correctly |
| CSVs swapped in after a green build | Write CSVs directly | A failed run never leaves half-written reports |
| Consistency checks as warnings | Errors | Indicators are revised on different dates |
| Income group from current classification | Historical classification per year | The country endpoint returns only the current classification |
| Validate every HTTP 200 body against its request; retry with a fresh cache-buster | Trust HTTP 200; retry the same URL | The API can return another query's body with HTTP 200, and the same URL returns the same cached bytes (api_calls/02, 05) |
| Page loop in `source.py` | A dlt paginator | A rejected page must be requested again from a different URL, which dlt's paginators don't do |
| `per_page: 50`, two pages per indicator | `per_page: 1000`, one page | Paging runs on every real run and in the end-to-end test, not only in a unit test |
| Latest year per country (D-04) | The latest year every country reports | The requirements rule; `years_behind` shows when countries are compared across different years |
| YoY on a zero prior keeps `abs_change` (D-03) | Null both changes | The requirements rule; the absolute change is well defined, only the percentage is not |
| 90 s HTTP timeout | 30 s | An uncached response from the API took 45 s |

> **TODO (every step):** add a row each time you choose between two approaches.

---

## Assumptions

- Missing comes in two kinds, `null_in_source` and `not_returned`. Neither is ever 0.
- Income group is the current World Bank classification at extraction time, applied to every year.
- Year-over-year compares with exactly the previous year. A prior value of 0 gives an absolute change but no percentage change (D-03).
- "Latest available year" is per country and indicator: the latest year with a reported value (D-04).
- A `cb` cache-buster parameter doesn't change the query. The API ignores unknown parameters; a probe with one returned a byte-identical body (api_calls/02).
- "Unchanged source data" means byte-identical response bodies, by `body_sha256`.
- Analytics reflect the latest completed dlt load; raw holds the history.
- Values stay in current US dollars as published: no deflation, no currency conversion.
- Income-group aggregates use reporting countries only; coverage columns show the gaps.

> **TODO (every step):** add any assumption made while building.

---

## Trade-offs

| Decision | Gained | Given up |
| --- | --- | --- |
| Open-source dlt for ingestion | Retries with backoff, atomic load packages, a load log in `_dlt_loads`, a verbatim `json` column | One more abstraction to explain; paging and response validation are our own code rather than a dlt paginator; a failed load step can leave rows that staging must filter out |
| Validation with cache-buster retries | Wrong cached bodies are caught and usually recovered | Not guaranteed, because an inner cache sometimes ignores the token, so a run can still fail after `validation_attempts`; raw needs both `request_url` and `fetched_url` |
| `per_page: 50` | Paging is exercised on every run | Nine requests instead of five |
| dbt-core, no packages | `ref()` ordering, built-in tests, generated docs | `dbt build` is not transactional; a failing test leaves rebuilt tables, so CSVs are swapped in only on green |
| DuckDB | No server, one file | One writer at a time |
| Docker Compose | Identical environment, one command | The reviewer needs Docker |
| CSV reports only | Open anywhere, diff in git, double as sample output | No interactive view |
| Raw stores full bodies | Exact traceability and replay | Raw grows each run; production needs a retention rule |
| Full re-pull every run | Simple and always consistent | Redundant requests, harmless at this volume |
| Consistency checks warn | Reports ship despite revision timing | A real error needs someone to read the warning |

---

## Production design note

**Scheduling and incremental refreshes**

- Run on a schedule, daily or weekly. Each run first compares every indicator's `lastupdated` and `body_sha256` with the last completed load.
- At about nine requests a full re-pull is cheaper than incremental logic. At hundreds of indicators, re-pull only indicators whose `lastupdated` moved, plus a full pull monthly.

**Late, corrected or changed source data**

- The API serves current values only and overwrites history at the source. Raw keeps every response body per load, so every revision survives locally.
- Facts reflect the latest completed load. Next step: a dbt snapshot on `fct_indicator_year` for value history, and a `rpt_revisions` view listing values that changed between loads and by how much.
- A value that goes from reported to null is surfaced as `null_in_source`, never silently kept.

**Monitoring, alerting, data-quality failures**

- Alert on a failed run, any `error` test, a drop in `reported` rows versus the previous load, revisions above a threshold, and no completed load inside the schedule window.
- Track `warn` test counts per run as a trend.
- Track pages with `attempt > 1` and the logged rejections per run. A rise means the API's caching problem got worse; a run that fails on `validation_attempts` alerts like any other failure.
- Locally, CSVs are swapped in only after a green `dbt build`, though `warehouse.duckdb` itself is already rebuilt. In Snowflake, build into a staging schema and run `ALTER SCHEMA ... SWAP WITH` on green.

**Backfills and replay**

- Raw is per load and never modified, so any past state rebuilds with `rebuild --load-id <id>`.
- A backfill of more years or countries is a config change and a run.

**Mapping to cloud services**

| Local | Snowflake + AWS | Azure |
| --- | --- | --- |
| Docker image | ECR image on ECS Fargate, or the dlt source as a Glue Python shell job; EMR is overkill at this volume | ACR image on Azure Container Apps jobs |
| dlt into DuckDB `raw` | dlt's Snowflake destination writing the same raw tables, or dlt to S3 loaded by Snowpipe into a VARIANT table | dlt into Snowflake on Azure, or to ADLS Gen2 behind an external stage |
| dbt-duckdb | dbt-snowflake, same models; JSON paths move to `:` syntax and `FLATTEN` | Same |
| CSV swap after a green build | Staging schema plus `ALTER SCHEMA ... SWAP WITH` | Same |
| `docker compose run` | Step Functions or MWAA: extract, `dbt build`, swap | Data Factory pipeline |
| Test failures and alerts | dbt results and `_dlt_loads` status to SNS or Slack | Azure Monitor alerts |

---

## AI usage

**I chose the tech stack myself, not an AI tool:** dlt (open source) for ingestion, DuckDB, dbt-core with dbt-duckdb, and Docker Compose. AI (Claude) designed and drafted within that stack, as the table below shows.

| Step | What AI drafted | What I changed | How I verified |
| --- | --- | --- | --- |
| Planning | Architecture, data model, requirement mapping and README outline (Claude), within the stack I chose | Cut the orchestrator and the report UI; chose CSV-only output | Checked every requirement in the brief against the coverage table |
| Scaffold | TODO | | |
| Extract and load | TODO | | |
| Staging and marts | TODO | | |
| pytest | TODO | | |
| Reporting | TODO | | |
| README | TODO | | |

> **TODO (every step):** fill the row before committing that step. Be specific: which file, what you rewrote, which test or live check confirmed it.

---

## Unfinished work and next steps

> **TODO (end of build):** list anything not finished, with why and the next step.

Known next steps beyond the brief:

- dbt snapshot on `fct_indicator_year` and a `rpt_revisions` view for value history.
- Incremental refresh driven by `lastupdated`.
- A retention rule for `raw.api_responses`.
- A CI workflow that runs `docker compose run --rm test` on every push.
