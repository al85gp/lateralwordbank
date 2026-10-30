# Key decisions and trade-offs

[← Back to the README](../README.md)

Every design decision with the alternative considered and why, and what each choice gained and gave up.

## Key decisions walkthrough

| Decision | Alternative considered | Why |
| --- | --- | --- |
| Open-source dlt for ingestion | Plain `requests` | Retries, atomic load packages and a load log come built in; paging, error-body and `total` checks stay in our code, where a reviewer can read them |
| DuckDB | PostgreSQL, Snowflake | No server, no account; SQL close to Snowflake's |
| Spine-based fact table | Fact of returned rows only | Missing data becomes an explicit, testable row |
| Raw append-only with full response bodies | Overwrite raw each run | Exact traceability and replay of any load |
| Analytics read the latest completed load | Merge latest value per key | A load whose extract or load step failed is never read; values removed at the source disappear correctly. A load that fails a dbt test never reaches `output/`, but the warehouse tables keep it until `rebuild --load-id <last good load>` |
| CSVs swapped in after a green build | Write CSVs directly | A failed run never touches `output/`; each file is replaced atomically (`os.replace`) after all four are written |
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
| One `stg_wb__selected_load` model, and `stg_wb__loads` (every load up to it) that every stage model joins to | Repeat the "latest completed load" filter in each model | One place decides which loads are built; `rebuild --load-id` only changes a dbt var |
| Medallion schemas `raw` → `stage` → `gold`, with `fct_indicator_year` an incremental `merge` model | Rebuild the fact as a table from one load each run | A run touches only the keys its load requested, so a wider config adds rows and a narrower one drops none; `rebuild` (`--full-refresh`) replays every load and gives the same table |
| A cache-busted retry counter per canonical URL for the whole run | A counter per page fetch | A restart from page 1 also gets fresh tokens, and the attempt budget still bounds the total requests |
| Run the container as `${UID}:${GID}` (default 1000:1000) | Run as root | Files in `data/` and `output/` belong to that user, not root. Shells don't export `UID` and `GID`, so on Linux with another user id run `export UID GID=$(id -g)` first |
| Pinned top-level versions plus `constraints.txt` | Top-level pins only | The same transitive versions in every build |
| Dagster for production orchestration, monitoring, alerting and data quality | Airflow, Prefect, cron with scripts | Assets match the pipeline's lineage from API page to CSV. `dagster-dlt` and `dagster-dbt` wrap the existing code, and dbt tests become asset checks with no extra code. Sensors, freshness checks, partitions, backfills and the UI are all open source |

## Trade-offs

| Decision | Gained | Given up |
| --- | --- | --- |
| Open-source dlt for ingestion | Retries with backoff, atomic load packages, a load log in `_dlt_loads`, typed raw tables, a load id to put in cache-buster tokens | One more abstraction to explain; paging and response validation are our own code rather than a dlt paginator; a failed load step can leave rows that stage must filter out |
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
| `MERGE` into the fact instead of a rebuild | New keys are added and old ones kept; a run writes only its own load | A key removed from config keeps its last value; a `run` after `rebuild --load-id <old>` merges onto that older state; stage parses every load, so raw retention matters sooner |
| Failing test rows stored in `dq` | A failure can be inspected after the run; warnings print their rows | 82 small tables in the `dq` schema, rebuilt every run |
| dbt runs in-process through `dbtRunner` | One process and one exit code for the whole run; tests call it directly | dlt and dbt share a Python environment, so their dependency versions must agree (pinned in `constraints.txt`) |
| Dagster in production only | Locally, one command and nothing else to run; in production, a UI, alerts, freshness checks and backfills | Two ways to start the same steps; Dagster's webserver, daemon and PostgreSQL are infrastructure to operate; the Dagster definitions still have to be written |
