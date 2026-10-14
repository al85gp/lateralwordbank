# Production design note (full version)

[← Back to the README](../README.md)

The README has a one-page version of this note. This is the full version, with the asset mapping and the monitoring detail.


In production the same code would run under **[Dagster](https://dagster.io)**. It is open source (Apache 2.0) and self-hosted, so it needs no account. Dagster owns scheduling, monitoring, alerting and the enforcement of data quality.

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
- **`reconcile`** runs as an asset check on `reports_csv`. A failure marks the reports asset failed and alerts; it can't block anything downstream, because the CSVs are the last step.
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
