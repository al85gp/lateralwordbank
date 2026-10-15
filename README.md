# World Bank Indicators Pipeline

A local, configuration-driven pipeline that pulls yearly country indicators from the public World Bank API, models them in DuckDB with dbt, and writes four analytics reports as CSV files. Every reported value can be traced back to the exact API response it came from, and missing data is reported as missing, never as zero.

| Scope | Value |
| --- | --- |
| Countries | BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF |
| Indicators | `SP.POP.TOTL`, `NY.GDP.MKTP.CD`, `NY.GDP.PCAP.CD`, `SP.DYN.LE00.IN` |
| Years | 2018–2024 |
| Stack | dlt (ingestion), DuckDB, dbt-core + dbt-duckdb, Docker Compose; Streamlit for the optional dashboard |

The requirements, with the IDs used below, are in [requirements.md](requirements.md). The details behind each section are in [docs/](#more-documentation).

---

## Setup

**Prerequisite:** Docker with Compose v2. Nothing else is needed on the host.

```bash
git clone https://github.com/al85gp/lateralwordbank.git && cd lateralwordbank
```

```bash
docker compose run --rm --build pipeline
```

That one command calls the API, runs `dbt build` with all its tests, and writes the reports to `output/`. The first image build takes about a minute, then a run takes about 10 seconds.

| Output | Contents |
| --- | --- |
| `output/rpt_missing_or_incomplete.csv` | Country-indicator-year combinations with no value |
| `output/rpt_yoy_change.csv` | Year-over-year change per country and indicator |
| `output/rpt_latest_comparison.csv` | Each country's latest available year and value, per indicator |
| `output/rpt_income_group_by_year.csv` | Indicators aggregated by income group and year |
| `data/warehouse.duckdb` | The full warehouse, for ad-hoc queries |

**Sample output** is committed in [output/](output/), from a live run on 2026-10-14 (source data last updated 2026-10-08).

Other commands:

| Command | What it does |
| --- | --- |
| `docker compose run --rm test` | Every test (66), in a container with no network |
| `docker compose run --rm pipeline loads` | Lists the completed loads |
| `docker compose run --rm pipeline rebuild --load-id <id>` | Rebuilds the models and CSVs from a stored load, with no API calls |
| `docker compose run --rm pipeline reconcile` | After a run: checks the CSVs against the warehouse and the raw API responses (13 checks) |
| `docker compose up -d --build dashboard` | Starts the Streamlit dashboard at http://localhost:8501 |
| `docker compose up --build` | Runs the pipeline and keeps the container `worldbank-pipeline` in Docker Desktop with its logs |

**Linux:** the containers run as `${UID}:${GID}`, default 1000:1000, and shells don't export those variables. If your user id isn't 1000, run `export UID GID=$(id -g)` once in your shell first.

**Without Docker** (Python 3.12):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt -r requirements-dashboard.txt -c constraints.txt
```

```bash
.venv/bin/python -m wb_pipeline run && .venv/bin/python -m pytest
```

**Changing the scope:** edit `config/pipeline.yml`, e.g. append an ISO3 code to `countries`, then run the pipeline again. The spine, the reports and the key and completeness tests widen automatically; the plausibility ranges are written for the four shipped indicators. See [docs/architecture.md](docs/architecture.md#configuration-adding-a-country-or-indicator).

---

## How it works

```text
World Bank API v2  ──►  dlt source        page loop, response validation, cache-busted retries
                              ▼
                        raw (DuckDB)      every response body exactly as received, append-only
                              ▼
                        dbt staging       one load, parsed, typed, ISO3 keys
                              ▼
                        dbt marts         dim_country, dim_indicator, fct_indicator_year
                              ▼
                        dbt reporting     one view per analytics question
                              ▼
                        output/*.csv      replaced only after dbt build and its tests pass
```

### Key decisions

1. **dlt for ingestion, with our own page loop.** dlt brings HTTP retries with backoff, atomic load packages and a load log. dlt's paginators can't reject an HTTP 200 page and fetch it again from a different URL, so `source.py` drives the paging itself.
2. **Every HTTP 200 body is checked against its request.** While mapping the API (`api_calls/`), it sometimes returned another query's cached body with HTTP 200. Each page is therefore checked for its indicator, countries, years, page number, source and row count. A rejected page is fetched again with a fresh cache-buster (`&cb=<load_id>-<attempt>`), because the same URL returns the same cached bytes.
3. **Raw keeps every response body exactly as received.** It is stored as text, so `sha256(body)` matches the stored hash. Every fact the API returned carries `request_url`, `body_sha256` and `record_index`, which point to the exact JSON record behind it; a `not_returned` fact has nothing to point to ([docs/ingestion.md](docs/ingestion.md#traceability)).
4. **The fact table is the full requested spine.** It has one row per country × indicator × year in scope, 252 rows here, so a missing value is a row with `value_status` (`reported`, `null_in_source` or `not_returned`), never an absent row or a 0.
5. **One load at a time, and a rerun creates no duplicates.** Raw is append-only, so a rerun adds a load there. The marts are rebuilt from one completed load, with `unique` tests on every grain, so a rerun on unchanged data gives the same facts (only `load_id` changes) and byte-identical CSVs.
6. **The rules in `requirements.md` decide the report logic.** "Latest available year" is each country's own latest reported year (D-04). A prior value of 0 keeps the absolute change and gives no percentage (D-03).
7. **Checks at every layer.** dbt runs 77 tests on every build, and an `error` test stops the export. 66 pytest tests replay recorded API responses with no network, and CI runs them on every push. `reconcile` checks the exported files against their source tables.
8. **One command in Docker.** The same image runs locally and in CI, and the pinned `constraints.txt` fixes every package version.

All decisions, with the alternative considered for each, are in [docs/decisions.md](docs/decisions.md).

### Data model

| Model | Grain | Business key | Relationships |
| --- | --- | --- | --- |
| `fct_indicator_year` | Country × indicator × year in scope | (`country_iso3`, `indicator_code`, `year`) | Many to one with both dimensions |
| `dim_country` | Country | `country_iso3` | One to many with the fact |
| `dim_indicator` | Indicator | `indicator_code` | One to many with the fact |

Columns, staging models and standardization are in [docs/data-model.md](docs/data-model.md).

### Reports

| Question | Report | Logic in one line |
| --- | --- | --- |
| Which combinations are missing or incomplete? | `rpt_missing_or_incomplete` | Every fact not `reported`, with its status and the series' coverage |
| Year-over-year change | `rpt_yoy_change` | Prior is exactly `year - 1`; a change that can't be computed is null, and `change_status` says why |
| Countries at the latest year | `rpt_latest_comparison` | Each country at its own latest reported year, ranked, with `years_behind` |
| Income groups by year | `rpt_income_group_by_year` | `sum` or population-weighted mean per config, with coverage columns |

On the committed data, all 252 values were reported, so the missing-data report has only a header. The largest year-over-year falls are Brazil's GDP per capita and GDP in 2020 (about −22% and −21%). Every country's latest year is 2024. More findings and the full logic are in [docs/reports.md](docs/reports.md).

### Data quality and tests

- **dbt tests on every build: 77.** A unique grain on every model, relationships from the fact to both dimensions, accepted values on the status, aggregation and source columns, and lineage that resolves to the raw record. Spine completeness and plausible ranges. Year-over-year numbers that match their status, and income groups that add up. GDP per capita checked against GDP ÷ population, as a warning.
- **pytest, offline: 66.** Paging, HTTP retries, API error bodies, the response validation rules, wrong-body recovery, idempotent reruns, corrections and replays, failures that leave `output/` untouched, the dashboard, and `reconcile`.
- **CI** runs `docker compose run --rm test` on every push.

The full test lists and a by-hand verification checklist are in [docs/testing.md](docs/testing.md).

---

## Assumptions

- Missing comes in two kinds, `null_in_source` and `not_returned`. Neither is ever 0.
- Income group is the World Bank's current classification, applied to every year: the v2 API returns only the current one (D-05).
- Year over year compares with exactly the previous year. `pct_change` is `(value − prior) / |prior| × 100`.
- "Latest available year" is per country and indicator (D-04).
- Income-group aggregates use reporting countries only, and the coverage columns show the gaps. A weighted mean counts a country only when both its value and its population are reported.
- Values stay in current US dollars as published, with no deflation or currency conversion.
- "Unchanged source data" means byte-identical response bodies, by `body_sha256`.
- A `cb` cache-buster parameter doesn't change the query: the API ignores unknown parameters.

---

## Trade-offs

| Choice | Gained | Given up |
| --- | --- | --- |
| dlt instead of plain `requests` | Retries, atomic load packages, a load log | One more tool to explain; paging and validation are still our own code |
| Validation with cache-busted retries | Wrong cached bodies are caught and usually recovered | Not guaranteed: an inner cache sometimes ignores the token, so a run can still fail after its attempts |
| DuckDB and dbt locally | No server and no account; SQL and models carry over to Snowflake | One writer at a time; `dbt build` isn't transactional, which is why the CSVs are swapped in only after a passing build |
| Raw keeps full bodies, append-only | Exact traceability and replay of any load | Raw grows every run; production needs a retention rule |
| Full re-pull every run | Simple and always consistent | Redundant requests, harmless at nine per run |
| Consistency checks warn rather than fail | Reports ship despite indicators being revised on different dates | A real error needs someone to read the warning |
| Docker for everything | The same environment everywhere, one command | The reviewer needs Docker; the images are 0.7–1.3 GB |

---

## Production design note

In production the same code would run under **[Dagster](https://dagster.io)**, which is open source and self-hosted. `dagster-dlt` and `dagster-dbt` wrap the dlt source and the dbt project as assets, and every dbt test becomes an asset check. The CSV export becomes an asset too, with `reconcile` as a check on it. The full version of this note is in [docs/production.md](docs/production.md).

**Scheduling and incremental refreshes (R-PRD-01)**
- Once a day, a Dagster sensor makes one cache-busted request to `/v2/source/2` and starts the job only when the source's `lastupdated` has moved, so most days cost one request instead of nine.
- At nine requests a full re-pull is cheaper than incremental logic. At hundreds of indicators, the indicators become partitions, and only partitions whose `lastupdated` moved are refreshed, plus a monthly full pull.
- A concurrency limit of one run keeps a single writer on the warehouse.

**Late, corrected or changed source data (R-PRD-02)**
- **Late values:** a value not yet published is a `not_returned` or `null_in_source` row, listed in `rpt_missing_or_incomplete`. When the World Bank publishes it, `lastupdated` moves, the sensor starts a run, and the row becomes `reported` with that load's lineage. Nothing is patched in place.
- The API overwrites history. Raw keeps every response body per load, so every revision survives.
- Next: a dbt snapshot on `fct_indicator_year` and a `rpt_revisions` view of values that changed between loads, with a threshold check.
- A value that goes from reported to null shows as `null_in_source`; it is never silently kept.

**Monitoring, alerting and data-quality failures (R-PRD-03)**
- **Monitoring:** the Dagster UI shows every run, its logs and the lineage from API page to CSV. Metadata per run, plotted over time: pages, retries, rejected bodies, `reported` rows and warnings.
- **Data quality:** a failing `error` test stops `dbt build`, so the CSVs aren't published; `warn` tests don't block. Extra checks: `reported` rows not dropping between loads, and a freshness check on the fact table.
- **Alerting:** open-source sensors post run failures, failed checks and stale data to Slack, Teams or email, naming the failing step.

**Backfills and replay (R-PRD-04)**
- Raw is never modified, so any past load can be rebuilt with no API call: in Dagster, the job with a `load_id` in its run config.
- More years or countries is a config change and a run. With partitions, the Dagster UI launches and tracks backfills.

**Mapping to cloud services (R-PRD-05)**

| Local | Snowflake + AWS | Azure |
| --- | --- | --- |
| Docker image | Runs as ECS Fargate tasks launched by Dagster; or the dlt step as a Glue Python shell job. EMR is overkill at this volume | Kubernetes jobs on AKS |
| Dagster (production only) | Webserver and daemon on ECS; storage in RDS PostgreSQL | AKS with the Helm chart; Azure Database for PostgreSQL |
| dlt into DuckDB `raw` | dlt's Snowflake destination, or S3 plus Snowpipe into a VARIANT table | Snowflake on Azure, or ADLS Gen2 behind an external stage |
| dbt-duckdb | dbt-snowflake with the same models; JSON paths use `:` and `FLATTEN` | Same |
| CSV swap after a passing build | Build into a staging schema, then `ALTER SCHEMA ... SWAP WITH` | Same |

---

## AI usage

I chose the stack and made the design decisions. I used Claude (Anthropic) in Claude Code to draft the code, tests and documentation within them, and to run the checks.

- **Research:** I mapped the API with live calls before writing any pipeline code; the probes, findings and recorded responses are in `api_calls/`. That work found the wrong-body caching problem that shaped the ingestion design.
- **Decisions I made:**
  - `requirements.md` wins over any other text when they disagree, for example on the latest-year (D-04) and zero-prior (D-03) rules;
  - every HTTP 200 body is validated, with cache-busted retries;
  - the HTTP timeout went from 30 to 90 seconds after I saw a 45-second response;
  - `per_page: 50`, so every run pages;
  - dbt tests are part of every build;
  - containers stay visible in Docker Desktop;
  - the dashboard was added only after I asked what it would cost;
  - CI runs on every push;
  - Dagster is the production design.
- **How I verified:**
  - the documented commands from a fresh clone;
  - live runs, all checks passing;
  - the 66 tests, locally and in Docker without network;
  - `reconcile` against the committed CSVs;
  - a deliberately broken check, to see that the tests catch it.

The step-by-step record, with what was drafted and what I changed at each step, is in [docs/ai-usage.md](docs/ai-usage.md).

---

## Next steps

- **An end-to-end missing-data test.** The live data has no missing values. A test that sends a null and a missing record through dlt, dbt and every report would exercise the paths the brief cares most about.
- **Restore after a failed build.** A load that fails a dbt test never reaches `output/`, but the warehouse keeps it until `rebuild --load-id <last good load>`. Next is to record which load is published and rebuild it automatically after a failure.
- **The Dagster project** described above: a thin layer over the existing code.
- **Historical income classifications,** from the World Bank's OGHIST file as a dbt seed.
- **A revisions view and a retention rule** for raw.

---

## More documentation

| Document | Contents |
| --- | --- |
| [docs/requirements-coverage.md](docs/requirements-coverage.md) | Every requirement ID, how it is handled, and what proves it |
| [docs/architecture.md](docs/architecture.md) | Architecture, repository layout, configuration |
| [docs/ingestion.md](docs/ingestion.md) | API calls, paging, failure handling, raw tables, traceability, idempotency |
| [docs/data-model.md](docs/data-model.md) | Every model's grain, keys and columns, and the standardization |
| [docs/reports.md](docs/reports.md) | Report logic, the `reconcile` checks, what the CSVs show |
| [docs/testing.md](docs/testing.md) | Every dbt and pytest test, CI, and a verify-it-yourself checklist |
| [docs/decisions.md](docs/decisions.md) | Every decision with its alternative, and all trade-offs |
| [docs/production.md](docs/production.md) | The full production design note |
| [docs/ai-usage.md](docs/ai-usage.md) | AI usage, step by step |
| [api_calls/](api_calls/README.md) | The API research done before the build: probes, findings and recorded responses |

Data from the World Bank's World Development Indicators, licensed under CC BY 4.0.
