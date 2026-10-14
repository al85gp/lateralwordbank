# Data quality and tests

[← Back to the README](../README.md)

The dbt tests that run on every build, the offline pytest suite, CI, and a checklist to verify the pipeline by hand.


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

## Verify it yourself

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
