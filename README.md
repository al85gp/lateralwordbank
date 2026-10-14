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

The pipeline is a batch job: it runs for about 10 seconds and exits. `--rm` removes its container when it finishes. To keep the container in Docker Desktop with its logs, use `up` instead. It shows as `worldbank-pipeline` in the `lateralwordbank` project and reads "Exited (0)" after a successful run:

```bash
docker compose up --build
```

```bash
docker compose --profile test up test
```

The second command keeps the test run as the `worldbank-tests` container. The `test` service sits behind a profile, so a plain `docker compose up` starts only the pipeline.

**Dashboard.** A Streamlit page over the four reports runs as the `worldbank-dashboard` container, which stays running. Open http://localhost:8501 once it has started:

```bash
docker compose up -d --build dashboard
```

It reads the CSVs in `output/`, so it works on the committed sample output before any run. Refresh the page after a pipeline run to see new data. Stop it with `docker compose stop dashboard`.

What you get:

| Output | Contents |
| --- | --- |
| `output/rpt_missing_or_incomplete.csv` | Country-indicator-year combinations with no value |
| `output/rpt_yoy_change.csv` | Year-over-year change per country and indicator |
| `output/rpt_latest_comparison.csv` | Each country's latest available year and value, per indicator |
| `output/rpt_income_group_by_year.csv` | Indicators aggregated by income group and year |
| `data/warehouse.duckdb` | The full warehouse, for ad-hoc queries |

Each run logs its load id. List the completed loads, or replay one without calling the API:

```bash
docker compose run --rm pipeline loads
```

```bash
docker compose run --rm pipeline rebuild --load-id <load_id>
```

Check that the CSVs in `output/` match the warehouse and the raw API responses. This prints 13 checks and exits non-zero if any fails:

```bash
docker compose run --rm pipeline reconcile
```

Both commands were checked from a fresh clone. Building the image the first time takes about a minute. After that, a full run takes about 10 seconds: about 4 seconds for the nine API requests, then `dbt build` and the export. The test suite takes about 30 seconds.

Without Docker, with Python 3.12:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt -c constraints.txt
```

```bash
.venv/bin/python -m wb_pipeline run && .venv/bin/python -m pytest
```

---

## Requirement coverage

| ID | Requirement | How it is handled | Where | Proven by |
| --- | --- | --- | --- | --- |
| R-ING-01, R-ING-02 | Retrieve country metadata and indicator observations | One country call, one call per indicator, all nine countries per request | `source.py` | `test_end_to_end`, `test_paging_*` |
| R-ING-03 | Handle paging | `per_page: 50`, so every indicator has two pages; a page loop reads `pages` from page 1 and fetches the rest; rows received are checked against `total` | `source.py` | `test_paging_*` |
| R-ING-04 | Handle API failures | dlt retries 429, 5xx and timeouts with backoff (`.dlt/config.toml`); other 4xx fail fast; every HTTP 200 body is validated against its request, and a rejected body is refetched with a fresh cache-buster; a failed extract loads nothing | `source.py`, `.dlt/config.toml` | `test_retries_*`, `test_error_payload_*`, `test_response_validation_*`, `test_wrong_body_retry_*`, `test_failed_run_not_exported` |
| R-ING-05, D-07 | Every output value traceable to its API response | `load_id`, `request_url`, `body_sha256` and `record_index` on every row; the body exactly as received, the URL actually sent and the attempt number in raw; lineage query below | `raw.api_responses`, every model | `assert_lineage_resolves_to_raw`, `assert_raw_pages_intact`, `test_lineage_*`, `reconcile` |
| R-ING-06 | One documented command from a clean checkout | `docker compose run --rm --build pipeline` | `docker-compose.yml` | Fresh-clone run |
| R-ING-07, D-08 | A rerun creates no duplicate analytical records | Raw is append-only; marts rebuild from the latest completed load (`_dlt_loads.status = 0`); `unique` on every grain | dbt marts | `test_idempotent_rerun` |
| R-ING-08 | No cloud resources or accounts | Public API, open-source dlt (no paid tier), a local DuckDB file, local Docker | Whole repo | Nothing to sign up for |
| R-MOD-01 | Raw separate from cleaned and analytics-ready | `raw` written only by dlt; `staging`, `marts`, `reporting` only by dbt | DuckDB schemas | `test_correction_replay_*`: `rebuild` restores everything from raw alone |
| R-MOD-02 | Grain, keys and relationships documented | Data model section; a description on every dbt model and column | `dbt/models/*.yml` | `unique`, `not_null`, `relationships` tests |
| R-MOD-03 | Standardized for analysts | ISO3 key, integer year, flattened objects, snake_case, typed values | Staging models | `accepted_values` and range tests |
| R-MOD-04, D-09 | Add a country or indicator by configuration | One list entry in `config/pipeline.yml` | `config.py` | `test_config_validation`, spine-completeness test |
| D-01, D-02 | Missing data is an outcome, not zero | Spine-based fact with `value_status` | `fct_indicator_year` | `accepted_values`, `assert_spine_complete` |
| R-ANL-01 – R-ANL-04 | The four analytics questions | One dbt view each, exported to CSV | `dbt/models/reporting/`, `output/` | `test_end_to_end`, `assert_yoy_change_matches_status`, `assert_income_groups_reconcile`, `test_reconcile_*` |
| R-ANL-05 | *Optional* extra view or dashboard | Streamlit dashboard over the four reports: coverage, year-over-year, latest year, income groups | `dashboard/app.py`, `docker-compose.yml` | `test_dashboard_*` |
| R-DQ-01 | Correctness and safe reprocessing | dbt tests on every run; pytest suite, run by CI on every push | `dbt/tests/`, `tests/`, `.github/workflows/ci.yml` | Data quality and tests section |
| R-DQ-02, D-06 | Related indicators consistent | GDP per capita within 1% of GDP ÷ population | Singular dbt test (warn) | `test_consistency_warning_does_not_block_export`; warning rows in the run output |
| R-DQ-03, D-10 | Tests never call the live API | `requests-mock` with recorded fixtures; the `test` service has no network | `tests/`, `docker-compose.yml` | Any live call fails the suite |
| R-PRD-01 – R-PRD-05 | Production design note | Dagster (open source) for scheduling, monitoring, alerting and data-quality enforcement; late data, backfills and replay, cloud mapping | Production design note section | — |
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
   staging  (dbt views)         one completed load (latest, or --load-id); parse, flatten, cast
                        ▼
   marts  (dbt tables)          dim_country, dim_indicator, fct_indicator_year
                        ▼
   reporting  (dbt views)       one view per analytics question
                        ▼
   output/*.csv                 swapped in only after dbt build passes
                        ▼
   dashboard  (Streamlit)       reads output/*.csv read-only; http://localhost:8501
```

One Compose service, `pipeline`, runs the dlt extract and load, then `dbt build`, then the CSV export, and stops at the first failure with a non-zero exit code. `data/` and `output/` are mounted from the host, so the warehouse and the CSVs outlive the container.

The raw layer is the only state that matters: drop every schema below it and `rebuild` restores them exactly.

---

## Repository layout

```text
lateralwordbank/
├── README.md
├── .github/workflows/ci.yml  # CI: docker compose run --rm test on every push
├── requirements.md           # the brief's requirements with IDs
├── Dockerfile                # python:3.12-slim; stages runtime and test; runs as the host user
├── docker-compose.yml        # project lateralwordbank: pipeline, test, dashboard; mounts config/ (read-only), data/ and output/
├── requirements.txt          # dlt[duckdb], duckdb, dbt-core, dbt-duckdb, pyyaml (pinned)
├── requirements-dev.txt      # pytest, requests-mock (pinned)
├── requirements-dashboard.txt # streamlit (pinned)
├── constraints.txt           # every transitive version, pinned
├── pytest.ini
├── config/
│   └── pipeline.yml          # countries, indicators, years, aggregation
├── .dlt/
│   └── config.toml           # HTTP timeout, retry attempts, backoff; telemetry off
├── wb_pipeline/
│   ├── __main__.py           # run | rebuild [--load-id] | loads | reconcile
│   ├── config.py             # load + validate pipeline.yml
│   ├── source.py             # dlt source: page loop, resources, response validation, cache-buster retries
│   ├── pipeline.py           # extract and load, dbt build, export, in order; rebuild; list loads
│   ├── export.py             # reporting views to output/.staging, moved into output/ on green
│   └── reconcile.py          # output/*.csv checked against the views, the fact table and raw
├── dbt/
│   ├── dbt_project.yml       # schemas staging, marts, reporting; failing test rows kept in dq
│   ├── profiles.yml          # duckdb at $WAREHOUSE_PATH
│   ├── macros/generate_schema_name.sql
│   ├── models/
│   │   ├── staging/          # stg_wb__selected_load, stg_wb__scope, stg_wb__countries, stg_wb__observations
│   │   ├── marts/            # dim_country, dim_indicator, fct_indicator_year
│   │   └── reporting/        # the four rpt_* views
│   └── tests/                # singular SQL tests
├── dashboard/
│   └── app.py                # Streamlit page over output/*.csv
├── tests/
│   ├── fixtures/             # recorded API pages for every canonical URL, with .meta.json sidecars
│   ├── conftest.py           # fake API serving the fixtures; per-test workspace
│   └── test_*.py
├── api_calls/                # how the API behaves: probes, findings, recorded samples
├── output/                   # committed sample CSVs
└── data/                     # gitignored: warehouse.duckdb, dlt's working folder
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

Then rerun the pipeline. The expected spine, the completeness test and every report widen automatically. In Docker, `config/` is mounted into the container read-only, so an edit applies on the next `docker compose run --rm pipeline` without rebuilding the image.

`config.py` rejects invalid ISO3 codes, lower-case indicator codes, duplicates, `start > end`, unknown aggregations, `validation_attempts` below 1, `per_page` above 32767 (the API's limit), unknown keys (a typo fails loudly), and `population_weighted_mean` when `SP.POP.TOTL` is not in scope.

HTTP behavior lives in `.dlt/config.toml`:

```toml
[runtime]
request_timeout = 90          # an uncached response took 45 s (api_calls/01)
request_max_attempts = 5
request_backoff_factor = 2
request_max_retry_delay = 60
dlthub_telemetry = false      # no calls to anything but the World Bank API
```

dbt's anonymous usage stats are off too (`dbt_project.yml`), so the API is the only network destination.

---

## Ingestion

### Calls

| Call | Path and params | Pages |
| --- | --- | --- |
| Country metadata | `/country/BRA;CAN;…;ZAF?format=json&per_page=50&page=<n>` | 1 |
| One per indicator | `/country/BRA;CAN;…;ZAF/indicator/<code>?format=json&date=2018:2024&per_page=50&page=<n>` | 2 |

That is nine requests per run. Both calls go through the same page loop, so the country call would page too with more than 50 countries. These are the canonical URLs: a first attempt always sends exactly this URL, and only a retry of a rejected HTTP 200 body adds `&cb=<load_id>-<attempt>` (see Failure handling).

### dlt source

`wb_pipeline/source.py` defines one `@dlt.source` with two resources, both `write_disposition: append`, loaded into dataset `raw`:

- `run_scope` — one row per load: the config snapshot of countries, indicators with unit and aggregation, and years.
- `api_responses` — one row per accepted page: `endpoint`, `indicator_code`, `request_url` (canonical), `fetched_url` (the URL actually sent), `attempt`, `page`, `pages`, `total`, `source_last_updated`, `http_status`, `fetched_at`, `body_sha256`, and `body`. The body is a `text` column holding the response exactly as received. A `json` column would also stop dlt from unnesting it into child tables, but it re-serializes the JSON, so the stored bytes would no longer match `body_sha256`. As text, `sha256(body) = body_sha256` holds for every page, and dbt tests that it does. DuckDB's JSON functions read the text column directly.

Every column has an explicit type, so dlt never guesses one from the first value it sees. The resource reads its `load_id` from dlt's load package state and uses it in the cache-buster token.

HTTP calls go through dlt's `requests` client, built from the `[runtime]` settings in `.dlt/config.toml`, so the timeout and retry settings apply to every request.

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
- Each run first discards any load package a crashed run left behind (`pipeline.abort_packages()`), so it can never be loaded later.
- The process exits non-zero with the innermost error, and `output/` keeps the last good CSVs.

What the live runs showed:

- Every one of the nine requests was accepted on the first attempt. The extract took about 4 seconds, all pages arrived in under 0.2 seconds each, and no cache-buster was needed.
- All 252 requested values came back reported. There were no null values and no missing rows, so `rpt_missing_or_incomplete` has only a header. The `null_in_source` and `not_returned` paths are exercised by tests instead.
- The source's `lastupdated` was 2026-10-08 on every indicator page.
- The country response has no `sourceid` or `lastupdated`, and its `per_page` is a string (`"50"`). Validation casts with `int()` and checks `sourceid` on indicator calls only.
- Some region names end with a space (`"Latin America & Caribbean "`). Staging trims them.
- Values arrive as JSON ints for some countries and floats for others within one indicator (USA GDP is an int). Staging casts all of them to DOUBLE.

### Raw tables

| Table | Grain and key | Notes |
| --- | --- | --- |
| `raw.api_responses` | One accepted page per load; (`_dlt_load_id`, `request_url`) | `body` as text exactly as received, `body_sha256`, request metadata, `fetched_url` and `attempt`; rejected attempts are not stored |
| `raw.run_scope` | One row per load; `_dlt_load_id` | Config snapshot; defines what was expected |
| `raw._dlt_loads` | One row per load; `load_id` | Written by dlt; `status = 0` marks a completed load |

### Traceability

1. Every staging, mart and reporting row carries `load_id`, `request_url`, `body_sha256` and `record_index`, the record's position in that page's data array.
2. `raw.api_responses` holds the verbatim body for that load and URL, so element `record_index` of the body's data array is the exact JSON object behind the value.
3. `request_url` is the canonical URL from the Calls table. It is the same for every load and every attempt, which makes it the join key and keeps lineage stable across runs.
4. `fetched_url` is the URL actually sent for the accepted body. It equals `request_url` when `attempt = 1`. On a retry it is `request_url` plus `&cb=<load_id>-<attempt>`, so raw shows exactly which request produced the stored bytes.
5. Both URLs are complete. Pasting either into a browser runs the same query against the live API, because the API ignores the `cb` token.
6. A `not_returned` row has no lineage by definition; `raw.run_scope` shows it was requested.
7. Two dbt tests check this on every run. `assert_lineage_resolves_to_raw` re-reads each reported value from its raw record and compares country, indicator, year and value. `assert_raw_pages_intact` checks every page's SHA-256 and that its `fetched_url` matches its `attempt`.

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

Raw is append-only: a rerun adds one load and about nine page rows. dbt rebuilds the marts as tables from the latest completed load, with `unique` tests on every grain, so unchanged source data produces identical facts. An unchanged `body_sha256` shows the source did not move. A run that needed cache-buster retries produces the same facts too: marts and CSVs carry the canonical `request_url`, never `fetched_url`. `test_idempotent_rerun` runs the pipeline twice and compares the fact table and the CSV bytes.

---

## Data model

The fact table's grain is the full requested spine, country × indicator × year, so a missing value is a row with a status rather than an absent row. With the default config that is 9 × 4 × 7 = 252 rows.

| Model | Layer | Materialized | Grain | Business key | Relationships |
| --- | --- | --- | --- | --- | --- |
| `stg_wb__selected_load` | staging | view | One row: the load to build from | `load_id` | The `load_id` var, else the latest completed load; every staging model joins to it |
| `stg_wb__scope` | staging | view | Requested country × indicator × year | (`country_iso3`, `indicator_code`, `year`) | Built from `raw.run_scope` of the selected load; carries each indicator's `unit` and `aggregation` |
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

**Standardization:** one country key (ISO3) everywhere, because the API mixes ISO2 `country.id` and `countryiso3code`; `year` as an integer instead of the string `date`; every value as DOUBLE; nested `{id, value}` objects flattened; names trimmed; snake_case throughout.

The column lists above were checked against the real payload. The model drops these API fields; all of them stay in raw:

- Country call: `adminregion`, `lendingType`, `capitalCity`, `longitude`, `latitude`, and the region's and income level's `iso2code` aliases.
- Observations: `country.id` and `country.value`, which the ISO2 code and `dim_country` already cover, and `unit`, which is empty for all four indicators (the unit comes from config).

---

## Reporting outputs

| Question | View | Grain | Logic |
| --- | --- | --- | --- |
| Which combinations are missing or incomplete? | `rpt_missing_or_incomplete` | Country × indicator × year not `reported` | Includes `value_status` plus the series' `years_reported` and `years_expected` |
| Year-over-year change | `rpt_yoy_change` | Country × indicator × year | Prior is exactly `year - 1`. When the current or prior value is missing, `abs_change` and `pct_change` are both null. When the prior value is 0, `abs_change` is still computed and only `pct_change` is null (D-03). `change_status` says which case applies: `ok`, `first_year_in_scope`, `no_current_value`, `no_prior_value`, `prior_is_zero` |
| Countries at the latest year | `rpt_latest_comparison` | Indicator × country | Per D-04, `latest_year` is each country's own latest year with a reported (non-null) value, so it can differ between countries. Shows that year's `value` and its `value_rank` among countries for the indicator (1 = highest; ties share a rank). `years_behind` is the most recent `latest_year` of any country minus this country's, so a comparison across different years is visible. A country with no reported value in scope has null `latest_year`, `value` and `value_rank` |
| Income groups by year | `rpt_income_group_by_year` | Income group × indicator × year | `sum` for population and GDP; `population_weighted_mean` for GDP per capita and life expectancy, weighted by the same country-year's population; `countries_in_group`, `countries_reporting` and `is_complete` show coverage |

`pct_change` is in percent of the prior value. CSVs are ordered by their keys. The three row-level reports carry `request_url`, `body_sha256` and `record_index` as lineage; the income-group report is an aggregate and has none. None of them carry `load_id` or `fetched_url`, so a rerun on unchanged data produces a zero-line git diff.

- 2018 has no prior year in scope, so its YoY status is `first_year_in_scope`. Set `years.start: 2017` to change that.
- India is the only lower-middle-income country in scope, so that group is a single country.

**Checking the CSVs against their source tables.** The dbt tests check the models inside the warehouse. `python -m wb_pipeline reconcile` (in Docker: `docker compose run --rm pipeline reconcile`) checks the files people actually read, from the outside:

| Check | What it compares |
| --- | --- |
| Each CSV equals its reporting view | Every row and value, both directions (`EXCEPT ALL`), with the CSV read using the view's column types |
| Row counts follow from scope | Facts and `rpt_yoy_change` = countries × indicators × years from `raw.run_scope`; `rpt_latest_comparison` = countries × indicators; `rpt_income_group_by_year` = groups × indicators × years; `rpt_missing_or_incomplete` = facts not reported |
| CSV values = fact table | Every `rpt_yoy_change` value, `request_url` and `record_index` against `fct_indicator_year`; every `rpt_latest_comparison` value against the fact at that country's latest reported year |
| Fact table = raw API response | Every reported value against the record at `record_index` in its stored body, whose SHA-256 must match |
| Income groups = country totals | For `sum` indicators, the groups add up to the reporting countries, per indicator and year |

On the committed output all 13 checks pass. `test_reconcile_*` runs the command after a pipeline run, and also edits one CSV value by hand to show that exactly the two checks that should fail do fail.

What the committed CSVs show (live run, source updated 2026-10-08):

- **`rpt_missing_or_incomplete`:** a header only. All 252 requested values were reported.
- **`rpt_yoy_change`:** 216 `ok` rows and 36 `first_year_in_scope` rows (2018). The largest falls are Brazil's GDP and GDP per capita in 2020 (about −21%). The largest rise is South Africa's GDP in 2021 (+24%).
- **`rpt_latest_comparison`:** every country's latest year is 2024, so `years_behind` is 0 everywhere. India ranks first for population, the USA for GDP and GDP per capita, and Japan for life expectancy.
- **`rpt_income_group_by_year`:** 84 rows (4 indicators × 3 groups × 7 years), all complete. In 2024, GDP per capita, weighted by population, was about $67,000 for high income, $10,900 for upper middle income and $2,600 for lower middle income.

---

## Data quality and tests

### dbt tests (every run)

`dbt build` runs 77 tests alongside the 11 models. A failing `error` test stops the run before export, so `output/` keeps the last good CSVs. A `warn` test's failing rows are printed in the run log, and the run continues. Every test stores its failing rows in the `dq` schema, so a failure can be inspected afterwards.

| Check | Implemented as | Severity |
| --- | --- | --- |
| Keys unique and not null on every model, including the reporting views | `unique` (on the full grain), `not_null` | error |
| Exactly one load selected | `unique`, `not_null` on `stg_wb__selected_load` | error |
| Fact rows point at existing dims | `relationships` | error |
| `value_status`, `aggregation`, `change_status`, `source_id` hold only known values | `accepted_values` | error |
| Spine complete: fact rows = countries × indicators × years in the load's scope, counted from `raw.run_scope` | `assert_spine_complete` | error |
| No duplicate API records per key within a load | `assert_no_duplicate_api_records` | error |
| Every value from the API has full lineage; `value` is null unless `reported` | `assert_reported_values_have_lineage` | error |
| Lineage resolves: the raw record at `record_index` has the same country, indicator, year and value | `assert_lineage_resolves_to_raw` | error |
| Raw pages intact: `sha256(body) = body_sha256`; `fetched_url` matches `attempt` | `assert_raw_pages_intact` | error |
| Every country has an income group | `not_null` | error |
| Population and GDP above 0; life expectancy 20–100 years | `assert_values_in_plausible_range` | error |
| Year-over-year numbers agree with `change_status` (no number where a value is missing; no percentage on a zero prior) | `assert_yoy_change_matches_status` | error |
| Income groups add up to the country totals for `sum` indicators, and each country is in exactly one group | `assert_income_groups_reconcile` | error |
| GDP per capita within 1% of GDP ÷ population | `assert_gdp_per_capita_consistent` | warn |
| GDP and population present but GDP per capita missing | `assert_gdp_per_capita_present` | warn |
| Population changes more than 10% year over year | `assert_population_yoy_within_10pct` | warn |

The consistency checks warn rather than fail: the source revises indicators on different dates, so a mismatch is usually revision timing, not a pipeline bug. On the real data, GDP per capita matches GDP ÷ population to about 1e-15.

### pytest (no network)

`requests-mock` intercepts every HTTP call dlt makes and serves JSON from `tests/fixtures/`, recorded once from the live API. The fixtures are the exact canonical URLs a real run sends: the country call plus pages 1 and 2 at `per_page=50` for all four indicators, copied from `api_calls/samples/pagination/`. Joined, the two pages of each indicator match the single-page golden recordings in `api_calls/samples/observations/` value for value. Integration tests run the dlt pipeline into a temporary DuckDB file and call `dbt build` through dbt's Python runner, with dlt's retry delays set to zero. The `test` service runs with `network_mode: none`, so any accidental live call fails the suite.

66 tests in five files. Names below are prefixes; each covers several cases.

| Test | File | Proves |
| --- | --- | --- |
| `test_paging_*` | `test_source.py` | The recorded responses are fetched from exactly the nine canonical URLs and yield 9 countries and 63 rows per indicator. A page with fewer rows than `total` implies fails after its attempts. A page 2 from another release (different `lastupdated`) restarts the indicator from page 1 with cache-busters. A zero-match response is an empty result, not an error |
| `test_retries_*` | `test_source.py` | 503, 503, 200 succeeds and resends the same URL; five 503s fail the run; a timeout is retried; a 400 is not retried |
| `test_error_payload_*` | `test_source.py` | HTTP 200 with id 120 fails at once with one request; id 105 is retried with `&cb=<load_id>-2` and recorded as attempt 2; id 199 on every attempt fails after 4 attempts |
| `test_response_validation_*` | `test_source.py` | Each check rejects a body: wrong `indicator.id`, a country or year outside the request, a non-integer date, wrong `page` or `per_page`, `sourceid` other than `"2"`, missing metadata, a short page, a bad shape, a non-JSON content type, a country record not requested. The recorded page passes, and its body is kept byte for byte |
| `test_wrong_body_retry_*` | `test_wrong_body_retry.py` | Page 1 of `NY.GDP.MKTP.CD` is first served the recorded `SP.POP.TOTL` page 1, a real response to a different query, as the cache does. The retry carries `&cb=<load_id>-2`, gets the right body, and loads it with `attempt = 2` and the canonical `request_url`. Wrong bodies on every attempt fail the run with nothing loaded. A clean run sends no cache-busters |
| `test_config_validation_*` | `test_config_validation.py` | The shipped config parses; adding a country or indicator is one list entry; 15 kinds of bad config are rejected with a clear message |
| `test_end_to_end` | `test_pipeline.py` | Extract, `dbt build`, export: 252 reported facts, four CSVs with the expected headers and row counts, no leftover staging folder |
| `test_lineage_*` | `test_pipeline.py` | For all 252 facts, the raw body's SHA-256 matches and the record at `record_index` has the same country, indicator, year and value |
| `test_idempotent_rerun` | `test_pipeline.py` | Two runs: raw has two loads, the fact table is identical with 252 rows, and the CSVs are byte-identical |
| `test_correction_*` | `test_pipeline.py` | A corrected value in run two shows in the fact, and raw keeps both bodies. `rebuild --load-id` of run one restores its facts and CSVs exactly, with no API calls |
| `test_failed_run_not_exported` | `test_pipeline.py` | A run that fails on its last page adds no completed load and leaves `output/` byte-identical |
| `test_bad_data_blocks_export` | `test_pipeline.py` | A duplicate page seeded into raw fails `assert_no_duplicate_api_records`; `output/` stays byte-identical |
| `test_consistency_warning_does_not_block_export` | `test_pipeline.py` | GDP per capita 5% off for one country-year logs the `warn` test with its row, and the CSVs are still written |
| `test_rebuild_rejects_an_unknown_load` | `test_pipeline.py` | `rebuild` with a load id that is not a completed load fails with a clear message |
| `test_reconcile_*` | `test_pipeline.py` | After a run, all 13 reconcile checks pass. With one CSV value edited by hand, exactly two fail: the CSV no longer equals its view, and no longer equals the fact |
| `test_dashboard_*` | `test_dashboard.py` | Streamlit's `AppTest` renders the dashboard without a browser. All four tabs and every indicator render from the CSVs of a real pipeline run on the recorded responses. A missing value is counted as missing and listed with its status. With no reports, the page says how to create them |

The suite can fail: with the `indicator.id` check in `source.py` disabled on purpose, three tests fail, including both `test_wrong_body_retry` tests that expect a wrong body to be rejected.

### CI

`.github/workflows/ci.yml` runs on every push to every branch, and can also be started by hand from the Actions tab. It runs the same commands as locally:

```bash
docker compose build test
```

```bash
docker compose run --rm test
```

- **Same as local:** CI uses the same image, tests and no-network rule as a local run, and never calls the World Bank API.
- **Fails on any failing test:** a failing test fails the run, which shows as a red check on the commit and on its pull request.
- **One run per branch:** a newer push to the same branch cancels the run it replaces.
- **Pinned and read-only:** `actions/checkout` is pinned to a commit, and the job only has read access to the repository.

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
| Dashboard in its own Docker stage | Install Streamlit into the pipeline image | The pipeline image stays 713 MB and unchanged; the dashboard needs none of dlt or dbt |
| Raw `body` as text | dlt's `json` column type | `json` re-serializes the body, so its bytes would no longer match `body_sha256`; text keeps them exact, and DuckDB's JSON functions still read it |
| One `stg_wb__selected_load` model that every staging model joins to | Repeat the "latest completed load" filter in each model | One place decides which load is built; `rebuild --load-id` only changes a dbt var |
| A cache-busted retry counter per canonical URL for the whole run | A counter per page fetch | A restart from page 1 also gets fresh tokens, and the attempt budget still bounds the total requests |
| Run the container as the host user | Run as root | Files in `data/` and `output/` belong to the person who ran it, on Linux too |
| Pinned top-level versions plus `constraints.txt` | Top-level pins only | The same transitive versions in every build |
| Dagster for production orchestration, monitoring, alerting and data quality | Airflow, Prefect, cron with scripts | Assets match the pipeline's lineage from API page to CSV. `dagster-dlt` and `dagster-dbt` wrap the existing code, and dbt tests become asset checks with no extra code. Sensors, freshness checks, partitions, backfills and the UI are all open source |

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
- Income-group aggregates use reporting countries only; coverage columns show the gaps. A weighted mean counts a country only when both its value and its population are reported.
- `pct_change` is a percentage of the prior value's magnitude, `(value - prior) / |prior| × 100`.
- Indicator codes are upper case, as the API returns them for World Development Indicators. The validation compares `indicator.id` exactly.
- Leading and trailing spaces in names are not meaningful, so staging trims them.

---

## Trade-offs

| Decision | Gained | Given up |
| --- | --- | --- |
| Open-source dlt for ingestion | Retries with backoff, atomic load packages, a load log in `_dlt_loads`, typed raw tables, a load id to put in cache-buster tokens | One more abstraction to explain; paging and response validation are our own code rather than a dlt paginator; a failed load step can leave rows that staging must filter out |
| Validation with cache-buster retries | Wrong cached bodies are caught and usually recovered | Not guaranteed, because an inner cache sometimes ignores the token, so a run can still fail after `validation_attempts`; raw needs both `request_url` and `fetched_url` |
| `per_page: 50` | Paging is exercised on every run | Nine requests instead of five |
| dbt-core, no packages | `ref()` ordering, built-in tests, generated docs | `dbt build` is not transactional; a failing test leaves rebuilt tables, so CSVs are swapped in only on green |
| DuckDB | No server, one file | One writer at a time |
| Docker Compose | Identical environment, one command | The reviewer needs Docker |
| CSV reports plus a Streamlit page over them | CSVs open anywhere, diff in git and double as sample output; the page adds charts and filters | The dashboard image is 779 MB (Streamlit brings pandas, pyarrow and altair), and the test image grows from 718 MB to 1.28 GB to include Streamlit for the dashboard tests |
| Dashboard reads `output/*.csv`, not DuckDB | Never shows a half-built state; never holds the warehouse lock, since DuckDB allows one writer and a dashboard holding the file open would block the next pipeline run | Shows only what the reports contain; refresh the page after a run |
| Raw stores full bodies | Exact traceability and replay | Raw grows each run; production needs a retention rule |
| Full re-pull every run | Simple and always consistent | Redundant requests, harmless at this volume |
| Consistency checks warn | Reports ship despite revision timing | A real error needs someone to read the warning |
| Failing test rows stored in `dq` | A failure can be inspected after the run; warnings print their rows | 77 small tables in the `dq` schema, rebuilt every run |
| dbt runs in-process through `dbtRunner` | One process and one exit code for the whole run; tests call it directly | dlt and dbt share a Python environment, so their dependency versions must agree (pinned in `constraints.txt`) |
| Dagster in production only | Locally, one command and nothing else to run; in production, a UI, alerts, freshness checks and backfills | Two ways to start the same steps; Dagster's webserver, daemon and PostgreSQL are infrastructure to operate; the Dagster definitions still have to be written |

---

## Production design note

In production the same code runs under **[Dagster](https://dagster.io)**. It is open source (Apache 2.0) and self-hosted, so it needs no account. Dagster owns scheduling, monitoring, alerting and the enforcement of data quality.

**Why Dagster:**
- Its model is assets, which matches how this pipeline already thinks: API pages, then raw tables, then models, then CSVs.
- It has first-party integrations for both tools here: `dagster-dlt` for the source and `dagster-dbt` for the models.
- `dagster-dbt` turns every dbt test into a Dagster asset check with no extra code.
- Sensors, freshness checks, partitions, backfills and the web UI are all in the open-source version. Dagster+, the paid hosted product, is not needed for anything below.

Locally, nothing changes: the pipeline stays one `docker compose run`. Dagster only wraps the same steps.

**How the pipeline maps onto Dagster**

| Today | In Dagster |
| --- | --- |
| `world_bank_source` (dlt) | Assets `raw/run_scope` and `raw/api_responses`, via `dagster-dlt` (`@dlt_assets`) |
| The dbt project | One asset per model, via `dagster-dbt` (`@dbt_assets`); every dbt test becomes an asset check on its model |
| `export.py` | An asset, `reports_csv`, downstream of the four reporting views |
| `reconcile.py` | An asset check on `reports_csv` |
| `python -m wb_pipeline run` | A job over all assets, started by a schedule or a sensor |
| `rebuild --load-id <id>` | The same job with `load_id` in its run config: rebuilds from raw, no API call |
| `config/pipeline.yml` | Unchanged; read when Dagster loads the definitions |

**Scheduling and incremental refreshes (R-PRD-01)**

- **Triggering.** A Dagster schedule runs the job daily. A sensor checks the source's `lastupdated` first, with one cache-busted request to `/v2/source/2?format=json` (about 0.2 seconds), and requests a run only when it has moved. Most days therefore cost one request, not nine. The cache-buster matters because the cached copy of that URL can lag a new release by up to about 24 hours (api_calls/03).
- **Full re-pull for now.** At about nine requests, a full re-pull is cheaper than incremental logic.
- **Incremental later.** At hundreds of indicators, partition the dlt asset by indicator, with the partition list read from config. The sensor then materializes only the partitions whose `lastupdated` moved, and a monthly scheduled run still pulls everything.
- **One writer at a time.** Only one run writes the warehouse. Dagster's queued run coordinator enforces this with a concurrency limit of 1 on a `warehouse` tag. That limit also respects DuckDB's single-writer rule.

**Late, corrected or changed source data (R-PRD-02)**

- **Revisions survive.** The API serves current values only and overwrites history at the source. Raw keeps every response body per load, so every revision survives.
- **Change history.** Each materialization of `raw/api_responses` records each indicator's `source_last_updated` and body hashes as Dagster metadata. A body that changed while `lastupdated` did not is visible in the asset's metadata history.
- **Revisions view (next step).** Add a dbt snapshot on `fct_indicator_year` for value history, and a `rpt_revisions` view listing values that changed between loads and by how much. An asset check on it warns when revisions pass a threshold.
- **Withdrawn values.** A value that goes from reported to null shows as `null_in_source`; it is never silently kept.

**Monitoring, alerting, data-quality failures (R-PRD-03)**

Monitoring, in the Dagster UI:

- **Runs and lineage.** Every run, its logs and the lineage from API pages to CSVs. Each asset shows when it was last materialized and by which run.
- **Metadata over time.** Each materialization attaches metadata, which the UI plots over time:
  - pages and records per indicator;
  - pages with `attempt > 1` and rejected bodies;
  - `reported` rows;
  - `warn` test failures;
  - run duration.

  A rise in `attempt > 1` means the API's caching problem got worse.

Data quality, as asset checks:

- **dbt tests.** Every dbt test becomes an asset check through `dagster-dbt`.
  - An `error` test that fails stops `dbt build` before the downstream models. `reports_csv` is then not materialized, and the last good CSVs stay in place, the same guarantee as the local swap-on-green.
  - `warn` tests become non-blocking checks with WARN severity.
- **`reconcile`** runs as a blocking asset check on `reports_csv`. All 13 of its checks must pass.
- **Extra checks:**
  - `reported` rows not lower than in the previous load;
  - revisions under a threshold;
  - pages with `attempt > 1` under a threshold;
  - a run that failed after its `validation_attempts` is already a failed run.
- **Freshness.** A freshness check on `fct_indicator_year` fails when there is no completed load inside the schedule window, for example 36 hours for a daily schedule.

Alerting. These are sensors run by the Dagster daemon, all open source:

- **Run failures.** A run-failure sensor posts to Slack (`dagster-slack`), Microsoft Teams (`dagster-msteams`) or email. The message carries the run link and the failing step: a validation that ran out of attempts, an API error id, or a failing dbt test.
- **Failed checks.** A sensor reads failed check evaluations from Dagster's event log. It alerts on any failed `error` check, and on a `warn` check that fails several runs in a row.
- **Freshness.** A freshness failure alerts the same way, so a run that silently never started is noticed too.

**Backfills and replay (R-PRD-04)**

- **Replay.** Raw is per load and never modified, so any past state can be rebuilt. In Dagster, that is the job launched from the UI with `load_id` in its run config, with no API call.
- **Backfills.** Adding years or countries is a config change and a run. With indicator partitions, the Dagster UI launches a backfill over any set of partitions and tracks the status of each one.
- **History.** Every replay and backfill run is recorded with its config, so the run history shows which load produced the current CSVs.

**Mapping to cloud services (R-PRD-05)**

| Local | Snowflake + AWS | Azure |
| --- | --- | --- |
| Docker image | ECR image; Dagster launches each run as an ECS Fargate task (`dagster-aws` ECS run launcher). If Glue is the platform standard, Dagster can instead start the dlt step as a Glue Python shell job through `dagster-aws` Pipes. EMR is overkill at this volume | ACR image; runs as Kubernetes jobs on AKS (`dagster-k8s`) |
| — (Dagster in production only) | Dagster webserver and daemon on ECS Fargate; run and event storage in RDS PostgreSQL | Dagster on AKS with the official Helm chart; storage in Azure Database for PostgreSQL |
| `docker compose run` | A Dagster schedule plus the `lastupdated` sensor | Same |
| dlt into DuckDB `raw` | dlt's Snowflake destination writing the same raw tables, or dlt to S3 loaded by Snowpipe into a VARIANT table | dlt into Snowflake on Azure, or to ADLS Gen2 behind an external stage |
| dbt-duckdb | dbt-snowflake, same models; JSON paths move to `:` syntax and `FLATTEN`; tests surface as Dagster asset checks | Same |
| CSV swap after a green build | Build into a staging schema, then `ALTER SCHEMA ... SWAP WITH` as the final asset, behind the blocking checks | Same |
| Test failures and alerts | Dagster asset checks, freshness checks and sensors to Slack, email or SNS | Dagster asset checks and sensors to Teams or email |
| Streamlit dashboard container | Streamlit in Snowflake over the reporting views, or the same image as an ECS service | The same image on Azure Container Apps |


---

## AI usage

**I chose the tech stack myself, not an AI tool:** dlt (open source) for ingestion, DuckDB, dbt-core with dbt-duckdb, and Docker Compose. AI (Claude, in Claude Code) designed and drafted within that stack.

- **Who did what:** I made the decisions and set the rules. Claude wrote the code, tests and documentation within them and ran the checks I asked for.
- **"What I changed"** records my instructions and decisions during the build.
- **"How I verified"** lists the evidence for each step.
- **"To check yourself"** marks a check to repeat by hand. The checklist below the table gives the exact commands.

| Step | What AI drafted | What I changed | How I verified |
| --- | --- | --- | --- |
| Planning | Architecture, data model, requirement mapping and README outline (Claude), within the stack I chose | Selected the tech stack after analyzing the API with live calls (`api_calls/`). Created `requirements.md`, which translates the brief into numbered requirements, then helped build the plan in `README.md`. Reviewed the README against `requirements.md` and set the rule that `requirements.md` wins on any conflict | Checked every requirement in the brief against the coverage table, which has a requirement ID on every row and a row for every requirement. Every relative link in `README.md` and `requirements.md` resolves; the broken ones were removed or fixed |
| Scaffold | Dockerfile (runtime and test stages, runs as the host user), `docker-compose.yml`, pinned requirements and `constraints.txt`, `config/pipeline.yml`, `.dlt/config.toml`, the dbt project skeleton (Claude) | Required the whole build to run in Docker. When the `--rm` containers disappeared from Docker Desktop, asked for named containers that stay visible: `worldbank-pipeline`, `worldbank-tests` and `worldbank-dashboard` in the `lateralwordbank` project | The documented commands were run from a fresh clone: the first image build takes about a minute and a run about 10 seconds. `worldbank-pipeline` and `worldbank-tests` ended "Exited (0)". **To check yourself:** items 1 and 2 |
| Extract and load | `config.py`, `source.py` (page loop, response validation, cache-buster retries), `pipeline.py`, `__main__.py`; recorded the canonical country URL and the 50-per-page pages as fixtures (Claude) | Decided how the API's failure modes are handled: every HTTP 200 body is validated against its request, and a rejected one is retried with a fresh cache-buster (`&cb=<load_id>-<attempt>`). Raised the HTTP timeout from 30 to 90 seconds after a 45-second uncached response. Set `per_page: 50` so paging runs on every real run, and had 50-per-page responses recorded for the other three indicators | Each new fixture page passed the validation rules, and its two pages joined match the single-page recording value for value. In the live runs, all nine requests were accepted on the first attempt, with `sha256(body) = body_sha256` on every raw page. Tests: `test_paging_*`, `test_retries_*`, `test_error_payload_*`, `test_response_validation_*`, `test_wrong_body_retry_*`. **To check yourself:** items 4 and 6 |
| Staging and marts | The staging and marts models, a description on every model and column, schema tests and singular tests (Claude) | Asked for dbt tests to be part of the build, so they run on every `dbt build`, not as a separate step. Set the model's core rule: missing is a status, never 0 (D-01, D-02) | `dbt build` runs 11 models and 77 tests with PASS=88. A deliberately broken view stopped the export and left `output/` untouched. The fact table has 252 rows, all `reported`, and GDP per capita matches GDP ÷ population to about 1e-15. **To check yourself:** items 7 and 8 |
| Reporting | The four `rpt_*` views, the year-over-year and income-group reconciliation tests, the CSV export, `reconcile.py` (Claude) | Ruled that `requirements.md` decides the report logic: each country's own latest reported year (D-04), and a zero prior that keeps `abs_change` with no `pct_change` (D-03). Asked for proof that the CSVs match their source tables, which added the `reconcile` command | Three layers. (1) `reconcile` checks each CSV against its view row for row, the row counts against the load's scope, every CSV value against `fct_indicator_year`, every fact against its raw API record, and the income-group sums against the country totals: 13 of 13 OK on the committed output. (2) The dbt tests `assert_lineage_resolves_to_raw`, `assert_yoy_change_matches_status` and `assert_income_groups_reconcile` run on every build. (3) `test_reconcile_*` shows the command catches a CSV edited by hand. **To check yourself:** items 3 and 5 |
| pytest | `conftest.py` with the fake API, and the test suite: 66 tests in five files (Claude) | Required that tests never call the live API (R-DQ-03) and that they run in Docker exactly as documented | 66 tests pass locally and in `docker compose run --rm test` with no network. A mutation check, with the `indicator.id` check disabled, made three tests fail. **To check yourself:** item 11 |
| README | The sections filled in from the live run and test results: quick start timing, observed API behavior, dropped fields, report summaries, test tables, decisions (Claude) | Reviewed the README against `requirements.md` and chose the fixes: requirement rules first, wrong-body handling, the 90-second timeout, `per_page: 50`, broken links removed. Asked for this section to state that I chose the stack, and to record my guidance here. Chose Dagster (open source) for production monitoring, alerting and data quality, and had the production design note written around it | Every TODO was replaced with what the build did. The numbers (timings, row counts, test counts) come from the runs above. **To check yourself:** read Assumptions and Trade-offs and confirm they match your intent |
| Dashboard | `dashboard/app.py`, the `dashboard` Docker stage and Compose service, `test_dashboard.py` (Claude) | Asked what a Streamlit page would add before deciding: about 285 MB of packages, for a 779 MB image. Then had it built in its own Docker stage and PR, so the pipeline image stays unchanged | The `worldbank-dashboard` container is healthy, and all four tabs were checked in a browser. `test_dashboard_*` (7 tests) render the page from a real pipeline run on the recorded responses. **To check yourself:** item 9 |
| CI | `.github/workflows/ci.yml` (Claude) | Asked for `docker compose run --rm test` on every push | The first run, on `ci/docker-tests`, passed in about 2.5 minutes. **To check yourself:** item 10 |

### Verify it yourself

Run these from the repository folder. Items 6 to 8 change `config/pipeline.yml` or `output/`. Revert with `git checkout config output` afterwards.

1. **Fresh clone, one command.** Clone the repo, then run:

   ```bash
   docker compose run --rm --build pipeline
   ```

   Expect `PASS=88` from dbt and the log line `done: load <id>`. If the source hasn't changed since 2026-10-08, `git status` shows no change in `output/`.
2. **In Docker Desktop.** Run:

   ```bash
   docker compose up --build
   ```

   Then go to Containers → `lateralwordbank` → `worldbank-pipeline`. It should read "Exited (0)", and its Logs tab shows the nine pages and `done`.
3. **The source tables match the output.** Run:

   ```bash
   docker compose run --rm pipeline reconcile
   ```

   Expect 13 checks, all `OK`, and exit code 0. Then change one number in `output/rpt_yoy_change.csv` and run it again. Expect `MISMATCH` on the two `rpt_yoy_change` checks and exit code 1. Restore the file with `git checkout output`.
4. **Raw pages are intact.** List the latest load's pages, with their attempt number and whether each stored body still matches its hash:

   ```bash
   docker compose run --rm --entrypoint python pipeline -c "import duckdb; print(duckdb.connect('/app/data/warehouse.duckdb').sql('select request_url, attempt, sha256(body) = body_sha256 as intact from raw.api_responses where _dlt_load_id = (select max(_dlt_load_id) from raw.api_responses) order by all'))"
   ```

   Expect nine rows with `intact` true. `attempt` is 1 unless the API sent a wrong body.
5. **One value against the live API.** In `output/rpt_latest_comparison.csv`, pick a row:
   1. Open its `request_url` in a browser.
   2. In the second array, find the element at `record_index`, counting from 0.
   3. That element's `countryiso3code`, `date` and `value` should match the row. A value the World Bank has revised since 2026-10-08 will differ, and that's expected.
6. **A failed run leaves the reports alone.** In `config/pipeline.yml`, change `SP.DYN.LE00.IN` to `SP.DYN.LE00.XX` and run the pipeline. It must exit non-zero with `id 120` and `nothing was loaded`, and `git status` must show `output/` unchanged. Revert the config.
7. **Add a country by configuration.** Append `FRA` to `countries` and run the pipeline, then `reconcile`. Expect 280 facts (10 × 4 × 7) and all checks `OK`. Revert the config and run again.
8. **Replay an earlier load.** List the loads, then rebuild from an older one without calling the API:

   ```bash
   docker compose run --rm pipeline loads
   ```

   ```bash
   docker compose run --rm pipeline rebuild --load-id <older id>
   ```

   Then run `reconcile`. All checks should pass for that load.
9. **Dashboard.** Run:

   ```bash
   docker compose up -d --build dashboard
   ```

   Open http://localhost:8501 and go through the four tabs. Keep the page open during item 7 and refresh it after the run: France appears. After the revert run, refresh again and it's gone.
10. **CI can fail.** On a scratch branch, change `252` to `251` in `test_end_to_end` and push. The run on GitHub → Actions must go red. Then delete the branch.
11. **The whole suite.** Run:

    ```bash
    docker compose run --rm test
    ```

    Expect `66 passed`.


---

## Unfinished work and next steps

Not built:

- **The Dagster project.** The production design note describes it, but it isn't written yet. It needs a `definitions.py` that wraps the dlt source (`@dlt_assets`) and the dbt project (`@dbt_assets`), plus the export as an asset, `reconcile` as an asset check, and the schedule, sensors and freshness checks. That is a thin layer over the existing code.
- **Historical income classifications.** The v2 API returns only the current group (D-05). The World Bank's historical classification file (OGHIST) could become a seed with a `year` column, joined in `rpt_income_group_by_year`.
- **A live check of a cache-buster recovery.** No wrong body occurred in the live runs, so that path is covered by tests on recorded wrong bodies only.

Known next steps beyond the brief:

- dbt snapshot on `fct_indicator_year` and a `rpt_revisions` view for value history.
- Incremental refresh driven by `lastupdated`.
- A retention rule for `raw.api_responses`.
