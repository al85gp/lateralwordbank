# Ingestion

[← Back to the README](../README.md)

The API calls, the dlt source, paging, failure handling, the raw tables, traceability and idempotency.


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

What our code adds: **HTTP 200 does not mean success.** The API returns its errors with HTTP 200. It can also return HTTP 200 with a well-formed body that answers a *different* query; this was reproduced several times while mapping the API ([api_calls/02](../api_calls/02_indicator_observations.md), [api_calls/05](../api_calls/05_errors_and_resilience.md)). So every response is validated against the request that produced it before anything is kept:

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
- A failure inside dlt's load step can leave rows behind, but without a completed entry in `_dlt_loads`; stage reads completed loads only (`status = 0`).
- Each run first discards any load package a crashed run left behind (`pipeline.abort_packages()`), so it can never be loaded later.
- The process exits non-zero with the innermost error, and `output/` keeps the last good CSVs.

What the live runs showed:

- Every one of the nine requests was accepted on the first attempt. The extract took about 4 seconds, all pages arrived in under 0.2 seconds each, and no cache-buster was needed.
- All 252 requested values came back reported. There were no null values and no missing rows, so `rpt_missing_or_incomplete` has only a header. The `null_in_source` and `not_returned` paths are covered by the model's logic and its dbt tests, but no automated test yet sends a null or a missing record through the whole pipeline.
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

1. Every staged observation and country record, and every fact the API returned, carries `load_id`, `request_url`, `body_sha256` and `record_index`, the record's position in that page's data array. The three row-level reports carry `request_url`, `body_sha256` and `record_index`, but not `load_id`, so their CSVs don't change on a rerun. The dimensions and the income-group aggregate carry none.
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
from gold.fct_indicator_year f
join raw.api_responses r
  on r._dlt_load_id = f.load_id and r.request_url = f.request_url
where f.country_iso3 = 'BRA' and f.indicator_code = 'SP.POP.TOTL' and f.year = 2023;
```

### Idempotency

Raw is append-only: a rerun adds one load and about nine page rows. dbt merges the new load into `gold.fct_indicator_year` on its grain, with `unique` tests on every grain, so unchanged source data produces identical facts and no duplicates. An unchanged `body_sha256` shows the source did not move. A run that needed cache-buster retries produces the same facts too: gold and the CSVs carry the canonical `request_url`, never `fetched_url`. `test_idempotent_rerun` runs the pipeline twice and compares the fact table and the CSV bytes.
