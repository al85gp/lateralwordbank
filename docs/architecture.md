# Architecture, layout and configuration

[← Back to the README](../README.md)

How the pieces fit together, where each file lives, and how to change the scope.

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

## Repository layout

```text
lateralwordbank/
├── README.md
├── .github/workflows/ci.yml  # CI: docker compose run --rm test on every push
├── requirements.md           # the brief's requirements with IDs
├── Dockerfile                # python:3.12-slim; stages runtime, test and dashboard
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
