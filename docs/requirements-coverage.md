# Requirement coverage

[← Back to the README](../README.md)

How each requirement in [requirements.md](../requirements.md) is handled, where, and what proves it.


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
| R-MOD-02 | Grain, keys and relationships documented | [data-model.md](data-model.md); a description on every dbt model and column | `dbt/models/*.yml` | `unique`, `not_null`, `relationships` tests |
| R-MOD-03 | Standardized for analysts | ISO3 key, integer year, flattened objects, snake_case, typed values | Staging models | `accepted_values` and range tests |
| R-MOD-04, D-09 | Add a country or indicator by configuration | One list entry in `config/pipeline.yml` | `config.py` | `test_config_validation` parses the added entries; an added country or indicator run end to end is a manual check ([testing.md](testing.md), item 7) |
| D-01, D-02 | Missing data is an outcome, not zero | Spine-based fact with `value_status` | `fct_indicator_year` | `accepted_values`, `assert_spine_complete`. The live data has no missing values, so no automated test yet sends a null or a missing record through the whole pipeline (see Next steps in the README) |
| R-ANL-01 – R-ANL-04 | The four analytics questions | One dbt view each, exported to CSV | `dbt/models/reporting/`, `output/` | `test_end_to_end`, `assert_yoy_change_matches_status`, `assert_income_groups_reconcile`, `test_reconcile_*` |
| R-ANL-05 | *Optional* extra view or dashboard | Streamlit dashboard over the four reports: coverage, year-over-year, latest year, income groups | `dashboard/app.py`, `docker-compose.yml` | `test_dashboard_*` |
| R-DQ-01 | Correctness and safe reprocessing | dbt tests on every run; pytest suite, run by CI on every push | `dbt/tests/`, `tests/`, `.github/workflows/ci.yml` | [testing.md](testing.md) |
| R-DQ-02, D-06 | Related indicators consistent | GDP per capita within 1% of GDP ÷ population | Singular dbt test (warn) | `test_consistency_warning_does_not_block_export`; warning rows in the run output |
| R-DQ-03, D-10 | Tests never call the live API | `requests-mock` with recorded fixtures; the `test` service has no network | `tests/`, `docker-compose.yml` | Any live call fails the suite |
| R-PRD-01 – R-PRD-05 | Production design note | Dagster (open source) for scheduling, monitoring, alerting and data-quality enforcement; late data, backfills and replay, cloud mapping | README → Production design note; [full version](production.md) | — |
| R-DEL-01 | Runs locally by following the README | Docker Compose, nothing else on the host | README → Setup | Fresh-clone run |
| R-DEL-02 | Setup, assumptions, trade-offs, decisions, AI usage | One section each in the README | `README.md` | — |
| R-DEL-03 | Sample output of the reporting queries | Committed CSVs | `output/*.csv` | — |
| R-DEL-04 | Git repository with commit history | One commit per build step | This repository | `git log` |
