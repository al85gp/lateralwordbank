# 04 — Pagination & Response Envelope

> Area key: `pagination` · Samples: [`samples/pagination/`](samples/pagination/) · Live calls made: **66** (2026-10-14 01:11–01:17 UTC, spaced about 1 s apart, no credentials).
> Tags: **[observed]** means we called it during this session (URL, status and excerpt are cited). **[documented]** means it comes from World Bank docs (the doc URL is cited). Where the two disagree, both are recorded, and **live behavior drives the design**.
> Dataset version seen on every observation call: `"lastupdated":"2026-10-08"` (WDI, `sourceid "2"`).

## Why the pipeline needs this

Every World Bank v2 JSON list response uses the same wrapper: a top-level **array** `[meta, rows]`. The loader needs to know these things about it:

1. **Where the rows are** (`$[1]`) and **when to stop paging** (`$[0].pages`).
2. **How the edge cases look**: a zero-match result has `rows = null`, a page past the end has `rows = []`, and an invalid parameter returns a one-element error array. All three return **HTTP 200**, so they can't be told apart by status code.
3. **The JSON types**. The same meta key is a number on one endpoint and a string on another. Observation `value` mixes integer and float literals.
4. **Whether paging is deterministic.** Idempotent re-runs and "every value traceable to the API response" both depend on stable, reproducible pages.
5. **How the API fails** (HTML 400s, slow origin responses, CDN caching), so retries and validation can be written for the real behavior. The **saved raw bodies become offline test fixtures** for the paginator.

## Endpoints & URL templates

| endpoint | URL template | used by our pipeline? | notes |
|---|---|---|---|
| Observations, one indicator, country list | `https://api.worldbank.org/v2/country/{ISO3;ISO3;…}/indicator/{INDICATOR}?format=json&date={Y1}:{Y2}&per_page={N}&page={P}` | **Yes** (×4 indicators) | Meta has numeric `page/pages/per_page/total`, plus `sourceid` (string) and `lastupdated` (string). [observed] |
| Observations, multiple indicators | `https://api.worldbank.org/v2/country/{ISO3;…}/indicator/{IND1;IND2;…}?source=2&format=json&date={Y1}:{Y2}&per_page={N}&page={P}` | Optional alternative | Meta `sourceid` is JSON `null`. 252 rows in one page for our scope. [observed] Without `source=2`, the same call returns HTTP 200 + error 120 (verification V23). [observed] |
| Country list / selected countries | `https://api.worldbank.org/v2/country[/{ISO3;…}]?format=json&per_page={N}&page={P}` | **Yes** (country metadata, income group) | Meta `per_page` is a **string**. No `sourceid`/`lastupdated`. [observed] |
| Income levels | `https://api.worldbank.org/v2/incomeLevel?format=json&per_page={N}&page={P}` | Optional (income-group reference) | **All four** meta values are **strings**. [observed] |
| Indicator metadata | `https://api.worldbank.org/v2/indicator[/{INDICATOR}]?format=json&per_page={N}&page={P}` | Optional (indicator names/units) | Meta `per_page` is a **string**. [observed] |
| Same paths without `format` | e.g. `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?date=2018:2024` | **No** | Returns XML (`text/xml`). [observed] [documented] |
| `format=jsonstat` | `…?format=jsonstat` | No | JSON-stat object, no paging meta. [observed] |

## Parameters

| param | allowed values | default | observed behavior | notes |
|---|---|---|---|---|
| `format` | `xml`, `json`, `jsonP` (+`prefix`), `jsonstat` [documented: [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)] | **XML** ("By default, all requests will respond with valid XML.") [documented] | With no `format`, the response is `text/xml` and byte-identical to `format=xml` (calls #1, #4). An `Accept: application/json` header is **ignored**: still XML (#64). `format=JSON` (uppercase) works (#65). | **`format=json` is mandatory** on every call. Do not rely on content negotiation. |
| `per_page` | Integer **1 … 32767** [observed] | **50** [documented][observed #10] | 1, 10, 50, 1000, 20000, 32500 and 32767 all return HTTP 200, with `per_page` echoed back. **32768, 50000 and 100000 return HTTP 400 `text/html` "Request Error"** (#38, #39, #42). `0`, `-1` and `abc` return HTTP 200 with error 120 (#53–#55). | Docs state **no** maximum [documented]. Third-party pages claim 32500 or 1000. Live behavior matches a **signed 16-bit cap (32767)**. Values above 1000 really are honored: 1862 rows came back in one page (#56). Pipeline uses **1000**. |
| `page` | Integer ≥ **1** | 1 | Pages are 1-based. `page=0` and `page=-1` return error 120 (#51, #52). `page > pages` returns HTTP 200 with `rows: []` (#43, #44, #62, #63). | Start at 1 and stop at `meta.pages`. |
| `date` | `YYYY`, `YYYY:YYYY` (also `M`/`Q`/`YTD` forms) [documented] | all years | A range that partly overlaps the data is clipped: `1955:1961` returns 1960–1961 (#48). **A range entirely outside the data is silently ignored and ALL years come back**: `1900:1901` gives total 594 (1960–2025) (#45), and `2030:2031` gives 66 rows (#47). A **single** out-of-range year (`1900`) gives a true zero-match (#49). | Always assert that returned `date`s fall inside the requested range. |
| `source` | Source id, e.g. `2` (WDI) | — | Needed for multi-indicator calls: indicators must come from a single source, max 60 indicators [documented, [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)]. Live: `country/BRA;USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD` with **no** `source` returns HTTP 200 + error 120 (verification V23) [observed]. `source=11` on a single-indicator WDI call was ignored: it still returned `sourceid "2"` and the same 7 rows (#50). | Owned by another area doc; recorded here only for its effect on the envelope. |

## Response envelope & field dictionary

### Envelope shapes (all HTTP 200 unless noted)

| case | top-level shape | `$[0]` (meta) | `$[1]` (rows) | evidence |
|---|---|---|---|---|
| Normal page | `[meta, [row…]]` | object | non-empty array | #2, #15–#21 |
| Page beyond `pages` | `[meta, []]` | echoes the requested `page` (e.g. 999) and real `pages`/`total` | **empty array `[]`** | #43, #44, #62, #63 |
| Zero match | `[meta, null]` | `{"page":0,"pages":0,"per_page":0,"total":0,"sourceid":null,"lastupdated":null}` | **`null`** | #49 |
| Invalid param value | `[{"message":[…]}]` (**1 element**) | `{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}` | **absent** | #51–#55; V13–V15, V23 (multi-indicator without `source`), V34 (`date=abcd`) |
| `per_page ≥ 32768` | not JSON: **HTTP 400**, `text/html`, body begins with a **UTF-8 BOM** (`EF BB BF`), `<title>Request Error</title>` | — | — | #38, #39, #42 |
| No `format` param | XML: `<wb:data page="1" pages="1" per_page="50" total="7" sourceid="2" lastupdated="2026-10-08">` | XML attributes (all strings) | `<wb:data>` children | #1 |

### Meta-key JSON types per endpoint (exact, as returned)

| endpoint | `page` | `pages` | `per_page` | `total` | `sourceid` | `lastupdated` | evidence |
|---|---|---|---|---|---|---|---|
| Observations (1 indicator) | number | number | **number** | number | string `"2"` | string `"2026-10-08"` | #2: `{"page":1,"pages":1,"per_page":50,"total":7,"sourceid":"2","lastupdated":"2026-10-08"}` |
| Observations (multi-indicator, `source=2`) | number | number | number | number | **null** | string | #61: `{"page":1,"pages":1,"per_page":1000,"total":252,"sourceid":null,…}` |
| Observations, zero-match | number `0` | number `0` | number `0` | number `0` | **null** | **null** | #49 |
| `country` (list or selected) | number | number | **string** | number | (absent) | (absent) | #5: `{"page":1,"pages":148,"per_page":"2","total":296}` |
| `indicator` (list or single) | number | number | **string** | number | (absent) | (absent) | #8: `{"page":1,"pages":1,"per_page":"50","total":1}` |
| `incomeLevel` | **string** | **string** | **string** | **string** | (absent) | (absent) | #7: `{"page":"1","pages":"1","per_page":"50","total":"7"}`. Also #63 `"page":"2"` |

**Always coerce with `int(...)` before comparing.** Never use `is`/type-based checks on meta values.

### Observation row field dictionary (`$[1][*]`)

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `$[0].page` | number (obs) / string (incomeLevel) | `1` | `0` on zero-match | `api_page` | Lineage. |
| `$[0].pages` | number / string | `7` | `0` on zero-match | `api_pages` | **Pagination stop condition.** |
| `$[0].per_page` | number (obs) / string (metadata endpoints) | `10` / `"50"` | `0` on zero-match | `api_per_page` | |
| `$[0].total` | number / string | `63` | `0` on zero-match | `api_total` | Validate `Σ len(rows) == total`. |
| `$[0].sourceid` | string | `"2"` | **null** on multi-indicator and zero-match | `api_source_id` | Absent on metadata endpoints. |
| `$[0].lastupdated` | string (`YYYY-MM-DD`) | `"2026-10-08"` | **null** on zero-match | `source_last_updated` | Dataset version. Store it on every row. |
| `$[1]` | array \| **null** | — | null (zero-match), `[]` (page beyond) | — | **Data selector.** |
| `$[1][*].indicator.id` | string | `"SP.POP.TOTL"` | no | `indicator_code` | |
| `$[1][*].indicator.value` | string | `"Population, total"` | no | `indicator_name` | |
| `$[1][*].country.id` | string | `"BR"` | no | `country_iso2` | **ISO2**, not ISO3. Aggregates use codes like `"XD"`. |
| `$[1][*].country.value` | string | `"Brazil"` | no | `country_name` | |
| `$[1][*].countryiso3code` | string | `"BRA"` | **`""` for income-group aggregates** (XD, XM, XN, XT, XY) | `country_iso3` | #56 (`country/all`). All 9 of our countries are populated. |
| `$[1][*].date` | **string** | `"2024"` | no | `year` (cast to INT) | Quoted, not a number. |
| `$[1][*].value` | **number (int literal OR float literal)** \| **null** | `211998573`, `2185821610689.31`, `29298013000000` | **null** = missing (seen for `XY`, #56). No nulls today in our 9×4×7 grid. | `value` (DOUBLE/DECIMAL; **declare explicitly**) | GDP column mixes the two: USA rows are integer literals, the rest are floats (#58). |
| `$[1][*].unit` | string | `""` | always `""` in our samples | `unit` | Empty string, not null. |
| `$[1][*].obs_status` | string | `""` | always `""` in our samples | `obs_status` | Empty string, not null. |
| `$[1][*].decimal` | number | `0` (POP, GDP, LE), `1` (GDP per capita) | no | `decimal_places` | Display precision hint, not a scale. |
| `$[0].message[*].id` (error) | **string** | `"120"` | — | `api_error_id` | Only in the error envelope. |
| `$[0].message[*].key` / `.value` (error) | string | `"Invalid value"` / `"The provided parameter value is not valid"` | — | — | |

## Behavior & gotchas

**Envelope & types**
- [observed] The JSON wrapper is a top-level **array**, not an object: `[{"page":1,…},[…]]` (#2). JSONPath for rows is **`$[1]`** and for page count is **`$[0].pages`**. Both compile and work in dlt 1.31.0's jsonpath (verified offline against the fixtures; see the dlt notes below).
- [observed] `per_page` is a **number** on observation endpoints (`"per_page":50`, #2) but a **string** on `country` (`"per_page":"2"`, #5), `indicator` (`"per_page":"50"`, #8) and `incomeLevel`. On `incomeLevel`, `page`/`pages`/`total` are strings too (`{"page":"1","pages":"1","per_page":"50","total":"7"}`, #7).
- [documented] The docs' JSON sample for indicator metadata also shows `"per_page": "50"` as a string ([898599](https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries)). This is consistent with live behavior.
- [observed] `value` is a JSON number that can be an **integer literal** (`"value":29298013000000`, USA GDP, #58) or a **float literal** (`"value":2185821610689.31`, BRA GDP). Schema-inferring loaders (dlt included) type the column from the first value they see. If integers arrive first, a later float creates a variant column. **Declare `value` as `double` (or decimal) explicitly.** No exponent notation was seen.
- [observed] `date` is a **quoted string** (`"date":"2024"`). `decimal` is a number. `unit` and `obs_status` are `""` (empty string), never null, in every sample.
- [observed] `sourceid` is `"2"` on single-indicator calls and **`null`** on multi-indicator calls (#61).
- [observed] Income-group aggregates have **`countryiso3code: ""`** in observation rows (`XD` High income, `XM`, `XN`, `XT`, `XY`; #56). This only matters if we ever pull aggregates. Our 9 countries all carry ISO3.
- [observed] (cross-area, from the country sample #6) Region names can have a trailing space (`"value":"Latin America & Caribbean "` for BRA/MEX, `"value":"Sub-Saharan Africa "` for ZAF), and key casing differs between levels: top-level `iso2Code` vs nested `iso2code`. Trim strings, and map keys explicitly.

**Paging mechanics**
- [documented] `page` is "for paging through large result-sets", and `per_page` "default setting is 50 results per page" ([898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)). No maximum is documented.
- [observed] Default `per_page` is 50: with no `per_page`, the response showed `{"page":1,"pages":2,"per_page":50,"total":63}` and 50 rows (#10). This was byte-identical to an explicit `per_page=50&page=1` (#12).
- [observed] Pages are 1-based, and `pages = ceil(total / per_page)`: 63 rows gives 63 pages at `per_page=1`, 7 at 10, 2 at 50, and 1 at 1000 (#11, #15, #12, #14). Every page but the last has exactly `per_page` rows. The last page has the remainder (page 7 = 3 rows, #21; page 2 at 50 = 13 rows, #13).
- [observed] **Max `per_page` = 32767.** `per_page=32767` returns `"per_page":32767` (#41). `per_page=32768` returns **HTTP 400 text/html** (#42). 50000 and 100000 behave the same way (#38, #39). This disagrees with third-party claims of 32500 or 1000 (not official). Docs are silent.
- [observed] Large `per_page` is honored, not silently capped at 1000: `country/all/…SP.POP.TOTL?date=2018:2024&per_page=32767` returned `total 1862` and **1862 rows in one page** (#56). Page 2 at `per_page=1000` (862 rows, #57) equals slice `[1000:2000]` of that response.
- [observed] **A page past the end gives HTTP 200 and `rows: []`**, not null and not an error: `[{"page":999,"pages":7,"per_page":10,"total":63,…},[]]` (#43). The same holds for `country` (#62) and `incomeLevel` (#63, where `"page":"2"` is a string).
- [observed] **A zero-match gives `rows: null`** and all-zero meta: `[{"page":0,"pages":0,"per_page":0,"total":0,"sourceid":null,"lastupdated":null},null]` (#49).
- [observed] **Invalid paging values (`page=0`, `page=-1`, `per_page=0|-1|abc`) return HTTP 200** with a **one-element** array `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]` (#51–#55).
- [documented] Error 120 is listed as **HTTP 404**, "Parameter "XXX" has an invalid value." ([898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes)). **Docs and live behavior disagree**: live returns 200, a different message text, and `id` as a string. The design follows live behavior, so the loader checks the body as well as the status.

**Ordering & determinism**
- [observed] **Pagination is stable and deterministic.** For 9 countries × `SP.POP.TOTL` × 2018:2024 at `per_page=10`, run 1 (#15–#21) concatenated to exactly 63 rows, **0 duplicates, 0 gaps**, in the **same order as the single-page call** (#14). Runs 2 and 3 were byte-identical to run 1, but that only shows **caching**, not determinism. Run 2 (#22–#28) was a Cloudflare HIT. Run 3 (#29–#35, params reordered) was a Cloudflare MISS, but an **origin-side cache** served it: `last-modified` matched run 1 (e.g. `01:12:14`) and `max-age` was below 86400 (86364/86379). See Caching below. *Verification re-test with fresh origin responses:* `per_page=9`, pages fetched 7→1 (V16). Every page had `last-modified` = `date` and `max-age=86400`. The union equalled the `per_page=1000` reference sequence exactly, with 63 unique rows. The determinism conclusion holds.
- [observed] Row order inside a call follows the World Bank 3-letter country id (BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF), then `date` **descending** (2024→2018). For aggregates the order also follows the 3-letter id, not ISO2 and not name: `XD;XM;XN;XT;XY` came back as XD (HIC), XY (INX), XM (LIC), XN (LMC), XT (UMC) (V30). It **does not follow request order**: a reversed country list returned the identical row sequence (#40). For `country/all`, aggregates come first, then economies by name (Afghanistan, Albania, Algeria, …) (#56). Multi-indicator results were grouped by indicator (#61). **Don't depend on order.** Key rows on `(country_iso3, indicator_code, year)`.
- [observed] Determinism was only proven within one dataset version (`lastupdated` 2026-10-08). Whether the order changes after a data release is unknown.

**Date filter**
- [observed] **An out-of-range date *range* is silently dropped.** `country/{9}/indicator/SP.POP.TOTL?date=1900:1901` returned `"pages":12,…,"total":594` with years 1960–2025 (#45, #46). `BRA … date=2030:2031` returned 66 rows (#47). A single out-of-range year (`date=1900`) gives the true zero-match envelope (#49). A partial overlap is clipped (`1955:1961` gives 1960, 1961; #48).
- [observed] `SP.POP.TOTL` already has **2025** values (seen in #45, #47). Our `date=2018:2024` is a real filter, not "all available years".

**Format**
- [observed] With no `format`, the response is XML, `content-type: text/xml` (#1). That matches the docs: "By default, all requests will respond with valid XML." [documented, 898581]
- [observed] Content-type is usually `application/json;charset=utf-8`, but plain `application/json` (no charset) appeared on two uncached responses (#50, #65). Match on the prefix `application/json`.
- [observed] `format=jsonstat` returns `{"WDI":{"label":"WDI data; total 63 records.",…}}` with no paging meta. It returned all 63 values with no `per_page` (#66). Not used.

**Caching & latency**
- [observed] Responses go through Cloudflare with `cache-control: public, max-age=N` and `cf-cache-status: HIT|MISS|EXPIRED|BYPASS`. Error 400s are `BYPASS`. **There are two cache layers:**
  1. **Cloudflare**, keyed on the exact URL. Reordering query params gives a CF MISS.
  2. **An origin-side cache behind it** that ignores query-param order. A CF MISS can still return a copy the origin made earlier. When that happens, `max-age` is below 86400 because it counts down from when the origin made the copy, and `last-modified` is the origin's generation time.

  Examples:
  - V07 (`?date=2018:2024&format=json`, a reorder of #10): CF MISS, `max-age=85493`, `last-modified 01:11:58`, which is #10's response.
  - #57: CF MISS, `max-age=52545`, `last-modified Tue, 13 Oct 2026 15:51:24 GMT`, served at 01:15 on Oct 14.
  - V31 (`indicator/NY.GDP.MKTP.CD`): CF HIT, `age: 26421`, `max-age=36166`, `last-modified Tue, 13 Oct 2026 04:12:37 GMT`, i.e. about 21 h old.

  A fresh origin response has `max-age=86400` and `last-modified` = `date`. Because the two layers' lifetimes add up to the origin's 86400 s, a copy can be **up to about 24 h old whether it is a HIT or a MISS**. `last-modified` (not `cf-cache-status`) is the field that shows how old the copy is. Reordering params does **not** get a fresh copy.
  3. **[observed by critic] A third, inner path** that is invisible in those two headers. Responses served as plain `application/json` (no charset) in under ~0.4 s carry `last-modified` = `date` and `max-age=86400`, i.e. they look like fresh renders, yet they can hold another query's body (area 02 C2: a URL without `footnote` got the `footnote=y` body 1 s later; area 02 V18/V19). All 13 plain-`application/json` cf-`MISS` responses in the saved and scratch headers (11 from earlier passes plus area 02 C2 and C7) have this fresh-looking signature, while all 10 output-cache copies (old `last-modified`, `max-age` < 86400) kept the charset. So `last-modified = date` does **not** prove a fresh computation; the missing charset is the only header marker. Log the raw `Content-Type`.
- [observed] **Responses that miss the origin cache can be slow**: 15.2 s for a 9.9 KB page (#10), 9.1 s (#36), 21.9 s for 375 KB (#56). The verification pass saw **32.0 s on pipeline call #5** (`SP.DYN.LE00.IN`, V05), 31.5 s (V16 page 7) and 37.1 s (`jsonstat`, V33). Cached responses take about 0.1–0.6 s. **A 30 s client timeout would have failed pipeline call #5.** Use a read timeout of ≥ 90 s plus retries. The critic later saw **45.2 s** for a cold `country/HIC;UMC;LMC;LIC/indicator/NY.GDP.MKTP.CD` call and 36.9 s for the R10 URL + `footnote=y` [observed by critic, areas 01 C1 / 02 C9]; 90 s still has a 2× margin.
- [observed] No rate-limit headers (`Retry-After`, `X-RateLimit-*`) appeared in any of the 66 responses, or in the 40 verification responses. No 429/5xx was hit at about 1 req/s.

**dlt `rest_api` compatibility (verified offline with dlt 1.31.0 against the saved fixtures, no network)**
- `PageNumberPaginator(base_page=1, page_param="page", total_path="$[0].pages")` + `data_selector="$[1]"` requested pages `[1..7]` and yielded 63 rows from the per_page=10 fixtures. dlt does `int(total)`, so string `pages` (incomeLevel) is fine.
- Zero-match (`rows: null`): dlt's `extract_response` logs a warning and returns `[]`. The paginator stops (`pages=0`). Correct.
- **Error-120 body (HTTP 200)**: with the default `stop_after_empty_page=True`, dlt **silently treats it as an empty page and stops with 0 rows, raising nothing**. With `stop_after_empty_page=False` it raises `ValueError: Total pages not found…`. **We need a response hook or a custom resource that raises on a `message` envelope.**

## Failure modes

| failure | how it looks | evidence | loader handling |
|---|---|---|---|
| Missing `format=json` | HTTP 200 `text/xml` | #1 [observed] | Always send `format=json`. Assert the content-type starts with `application/json`. |
| `per_page` > 32767 | **HTTP 400 `text/html`**, BOM-prefixed "Request Error" page | #38, #39, #42 [observed] | Config validation (`1 ≤ per_page ≤ 32767`). Treat 400 as **non-retryable**. |
| Invalid `page`/`per_page` value (also `date=abcd`, or multi-indicator without `source`) | **HTTP 200** + `[{"message":[{"id":"120",…}]}]` | #51–#55; V13–V15, V23, V34 [observed]. Docs say 404 [documented, 898620] | Detect `len(body)==1 and "message" in body[0]`, then raise `WorldBankApiError(id, key, value)`. Non-retryable. |
| Zero match | HTTP 200, `[{…"total":0…}, null]` | #49 [observed] | `rows = body[1] or []`. Valid outcome: record "no data", never zero values. |
| Page beyond end | HTTP 200, `rows: []` | #43 [observed] | Shouldn't happen if we stop at `pages`. Treat it as end-of-data, but log it. |
| Date range outside data | HTTP 200, **all years** returned | #45, #47 [observed] | Assert every `int(date)` is in `[Y1, Y2]`. Fail the run otherwise. |
| Slow origin | 9–37 s responses when the origin cache misses. Three were over 30 s, including pipeline call #5 | #10, #36, #56; V05, V16, V33 [observed] | Read timeout ≥ 90 s. Retry with exponential backoff on timeout or connection error. |
| Service unavailable / unexpected error | Documented: 105 → HTTP 503, 199 → HTTP 500 | [documented, 898620]. Not observed. | Retry 5xx with backoff (e.g. 5 attempts). |
| Dataset updated mid-pagination | Would show as `lastupdated`/`total` changing between pages | Not observed (theoretical) | Capture `total`, `pages` and `lastupdated` on page 1. If a later page differs, **restart the whole fetch**. |
| Stale cached copy | Up to about 24 h old, on a Cloudflare HIT **or MISS** (origin-side cache). `last-modified` shows the copy's age; `max-age` < 86400 means a cached copy | [observed] headers (#57, V07, V31) | Store `lastupdated` + `fetched_at` + `last-modified` + `cf-cache-status` per response for lineage. Optionally accept staleness ≤ 24 h. |

## Calls our pipeline will make

Recommended `per_page=1000`. Every target result fits in one page with a large margin, and 1000 sits well under the 32767 ceiling. The loop is still implemented in full and tested with the `per_page=10` fixtures.

> **Canonical URLs live in the README call plan (R0–R11).** The URLs below were this area's measurements and differ in harmless ways: the README's country call uses `per_page=50` (its golden fixture's URL; README Contradictions #8), the observation calls add `source=2` (a no-op, Contradictions #5), and `&page=1` is what a generic paginator appends (byte-identical bodies, V02–V05). Build from the README table.

| # | purpose | exact URL | expected pages | expected rows (observed 2026-10-14) |
|---|---|---|---|---|
| 1 | Country metadata (incl. `incomeLevel`) | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=1000&page=1` | 1 | 9 (observed `total 9` at default per_page, #6; exact URL re-checked in V01: `{"page":1,"pages":1,"per_page":"1000","total":9}`) |
| 2 | Population | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000&page=1` | 1 | 63 (#14) |
| 3 | GDP (current US$) | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=1000&page=1` | 1 | 63 (#58) |
| 4 | GDP per capita | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=1000&page=1` | 1 | 63 (#59) |
| 5 | Life expectancy | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=1000&page=1` | 1 | 63 (#60) |
| 6 (opt.) | Income-level reference | `https://api.worldbank.org/v2/incomeLevel?format=json&per_page=1000&page=1` | 1 | 7 (#7) |

- **Total: 5 requests (6 with income levels) and 252 observation rows**. The expected grid is 9 countries × 4 indicators × 7 years = 252. Today every cell has a non-null value. Future releases may null out or drop cells, so completeness is measured against the expected grid, not against `total`.
- The alternative is a single call: `…/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` returns 252 rows in 1 page (#61). It is **not recommended**. `sourceid` becomes null, one failure takes out every indicator, and the per-indicator lineage (one response ↔ one indicator) is lost.

### Recommended pagination algorithm

```text
for each request template (one per indicator + country metadata):
    page = 1; first_meta = None; n_rows = 0
    loop:
        GET url with params {format: json, per_page: 1000, page: page, (date: 2018:2024)}
            timeout: connect 10 s, read 90 s; retry 5x with exp. backoff on timeout/conn error/5xx/429
            HTTP 400 or other 4xx -> fail fast (non-retryable; README §3 allows ONE retry for a WCF "Request Error" 400)
            validation failure -> retry with a fresh &cb=<run_id>-<attempt> token (the exact URL is edge-cached; area 02 C3/C4)
        assert content-type startswith "application/json"   # catches XML default and the 400 HTML page
        body = json.loads(raw_bytes.decode("utf-8-sig"))     # tolerant of the BOM seen on error pages
        archive raw_bytes + url + fetched_at_utc + sha256 + cf-cache-status + last-modified   # lineage / fixture parity
        if body is list and len(body) == 1 and "message" in body[0]: raise WorldBankApiError(body[0]["message"])
        assert body is list and len(body) == 2
        meta, rows = body[0], (body[1] or [])                # null rows == zero match
        pages, total = int(meta["pages"]), int(meta["total"])   # int(): incomeLevel sends strings
        if first_meta is None: first_meta = (pages, total, meta.get("lastupdated"))
        elif (pages, total, meta.get("lastupdated")) != first_meta: restart this request from page 1 (data changed)
        assert int(meta["page"]) == page or pages == 0
        assert len(rows) == min(per_page, total - (page-1)*per_page) or pages == 0
        yield rows tagged with (request_url, page, source_last_updated, response_sha256, fetched_at_utc)
        n_rows += len(rows)
        if pages == 0 or page >= pages: break               # stop condition: meta.pages (1-based)
        page += 1
    assert n_rows == first_meta.total
    assert every int(row.date) in [2018, 2024]               # guards the "out-of-range range is ignored" trap
    assert (country_iso3, indicator_code, date) unique
```

**Generic REST-client config** (e.g. dlt `rest_api`), if used:
```python
"paginator": {"type": "page_number", "base_page": 1, "page_param": "page",
              "total_path": "$[0].pages", "stop_after_empty_page": True},
"data_selector": "$[1]",
"params": {"format": "json", "per_page": 1000, "date": "2018:2024"},
# PLUS a response hook that raises when body is [{"message": [...]}]; without it error 120 becomes a silent empty load
# PLUS an explicit column hint: value -> double (int/float literal mix)
```

## Evidence log

All calls were `GET`, `curl -sS -m 30`, no auth, on 2026-10-14 UTC. "Sample" paths are relative to `samples/pagination/`. Calls with no sample were kept in scratch only (too large, or redundant).

| # | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?date=2018:2024` | 200 | `text/xml`. `<wb:data page="1" pages="1" per_page="50" total="7" sourceid="2" lastupdated="2026-10-08">`. XML is the default. | `default_format_no_format_param.xml` |
| 2 | `…/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=json` | 200 | `[{"page":1,"pages":1,"per_page":50,"total":7,"sourceid":"2","lastupdated":"2026-10-08"},[…7 rows]]`. CDN HIT. | `format_json_bra_sp_pop_totl.json` |
| 3 | `…/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=jsonstat` | 200 | `{"WDI":{"label":"WDI data; total 7 records.",…}}`, no paging meta | `format_jsonstat_bra_sp_pop_totl.json` |
| 4 | `…/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=xml` | 200 | byte-identical to #1 | `format_xml_explicit_bra_sp_pop_totl.xml` |
| 5 | `https://api.worldbank.org/v2/country?format=json&per_page=2` | 200 | `{"page":1,"pages":148,"per_page":"2","total":296}`: per_page is a string | `envelope_country_list_per_page_2.json` |
| 6 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json` | 200 | `{"page":1,"pages":1,"per_page":"50","total":9}`, 9 rows | `envelope_country_9_selected.json` |
| 7 | `https://api.worldbank.org/v2/incomeLevel?format=json` | 200 | `{"page":"1","pages":"1","per_page":"50","total":"7"}`: all strings | `envelope_incomelevel_list.json` |
| 8 | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` | 200 | `{"page":1,"pages":1,"per_page":"50","total":1}` | `envelope_indicator_metadata_sp_pop_totl.json` |
| 9 | `https://api.worldbank.org/v2/indicator?format=json&per_page=2` | 200 | `{"page":1,"pages":14772,"per_page":"2","total":29544}` | `envelope_indicator_list_per_page_2.json` |
| 10 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | default per_page 50: `"pages":2,"per_page":50,"total":63`, 50 rows. **15.2 s** (MISS) | `obs_SP.POP.TOTL_9c_default_per_page.json` |
| 11 | #10 + `&per_page=1` | 200 | `"pages":63,"per_page":1`, 1 row | `obs_SP.POP.TOTL_9c_per_page_1_page_1.json` |
| 12 | #10 + `&per_page=50&page=1` | 200 | 50 rows. Byte-identical to #10 | `obs_SP.POP.TOTL_9c_per_page_50_page_1.json` |
| 13 | #10 + `&per_page=50&page=2` | 200 | `"page":2,"pages":2`, 13 rows | `obs_SP.POP.TOTL_9c_per_page_50_page_2.json` |
| 14 | #10 + `&per_page=1000` | 200 | `"pages":1,"per_page":1000,"total":63`, 63 rows (reference set) | `obs_SP.POP.TOTL_9c_per_page_1000.json` |
| 15–21 | #10 + `&per_page=10&page={1..7}` (run 1) | 200 ×7 | 10,10,10,10,10,10,3 rows. `"pages":7,"total":63` on every page | `obs_SP.POP.TOTL_9c_per_page_10_page_{1..7}.json` |
| 22–28 | same URLs as 15–21 (run 2) | 200 ×7 | CDN HIT. Byte-identical to run 1 | — (scratch) |
| 29–35 | `…/indicator/SP.POP.TOTL?per_page=10&date=2018:2024&format=json&page={7..1}` (run 3, params reordered, pages fetched in reverse) | 200 ×7 | CDN MISS, but served from the **origin-side cache**: `last-modified` = run 1's, `max-age` 86364–86379. Byte-identical to run 1. Union = #14 sequence, 0 dups, 0 gaps. Not a fresh computation (see V16) | — (scratch) |
| 36 | #10 + `&per_page=20000` | 200 | `"per_page":20000`, 63 rows. 9.1 s | — (scratch) |
| 37 | #10 + `&per_page=32500` | 200 | `"per_page":32500`, 63 rows | — (scratch) |
| 38 | #10 + `&per_page=50000` | **400** | `text/html`, BOM, `<title>Request Error</title>` "The server encountered an error processing the request." `cf-cache-status: BYPASS` | `obs_SP.POP.TOTL_9c_per_page_50000_http_400.html` |
| 39 | #10 + `&per_page=100000` | **400** | same HTML as #38 | — (scratch) |
| 40 | `…/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000` | 200 | rows identical to #14: order independent of request order | — (scratch) |
| 41 | #10 + `&per_page=32767` | 200 | `"per_page":32767` | — (scratch) |
| 42 | #10 + `&per_page=32768` | **400** | same HTML as #38. **Ceiling is 32767** | — (scratch) |
| 43 | #10 + `&per_page=10&page=999` | 200 | `[{"page":999,"pages":7,"per_page":10,"total":63,…},[]]` | `page_beyond_range_page_999.json` |
| 44 | #10 + `&per_page=10&page=8` | 200 | `[{"page":8,"pages":7,…},[]]` | `page_beyond_range_page_8_of_7.json` |
| 45 | `…/country/BRA;…;ZAF/indicator/SP.POP.TOTL?format=json&date=1900:1901` | 200 | **filter ignored**: `"pages":12,"per_page":50,"total":594`, first row `"date":"2025"` | `date_1900_1901_out_of_range_filter_ignored.json` |
| 46 | #45 + `&page=2` | 200 | page 2 of 12, rows from 1975 | — (scratch) |
| 47 | `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=2030:2031` | 200 | filter ignored: `"total":66` (1960–2025) | — (scratch) |
| 48 | `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=1955:1961` | 200 | clipped: `"total":2` (1960, 1961) | — (scratch) |
| 49 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=1900` | 200 | **zero-match**: `[{"page":0,"pages":0,"per_page":0,"total":0,"sourceid":null,"lastupdated":null},null]` | `zero_match_date_1900_single_year.json` |
| 50 | `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&source=11` | 200 | `source` ignored: `"sourceid":"2"`, 7 rows. content-type `application/json` (no charset) | — (scratch) |
| 51 | #10 + `&per_page=10&page=0` | 200 | `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]` | `invalid_page_0_error_120.json` |
| 52–55 | #10 + `page=-1` / `per_page=0` / `per_page=-1` / `per_page=abc` | 200 ×4 | same error-120 body as #51 | — (scratch) |
| 56 | `https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=32767` | 200 | `"total":1862`, 1862 rows in one page (375 KB, **21.9 s**). Aggregates XD/XM/XN/XT/XY have `countryiso3code:""`. XY values are null | — (too large) |
| 57 | `…/country/all/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000&page=2` | 200 | 862 rows = slice [1000:2000] of #56 | — (too large) |
| 58 | `…/country/BRA;…;ZAF/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=1000` | 200 | 63 rows: 56 float + 7 int literals (USA, e.g. `29298013000000`) | `obs_NY.GDP.MKTP.CD_9c_per_page_1000.json` |
| 59 | `…/indicator/NY.GDP.PCAP.CD?…&per_page=1000` | 200 | 63 rows, all floats, `decimal:1` | `obs_NY.GDP.PCAP.CD_9c_per_page_1000.json` |
| 60 | `…/indicator/SP.DYN.LE00.IN?…&per_page=1000` | 200 | 63 rows, all floats, no nulls | `obs_SP.DYN.LE00.IN_9c_per_page_1000.json` |
| 61 | `…/country/BRA;…;ZAF/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | 200 | `"total":252,"sourceid":null`, 1 page | `obs_multi_indicator_4ind_9c_source_2_per_page_1000.json` |
| 62 | `https://api.worldbank.org/v2/country?format=json&per_page=2&page=999` | 200 | `[{"page":999,"pages":148,"per_page":"2","total":296},[]]` | `country_list_page_beyond_range_page_999.json` |
| 63 | `https://api.worldbank.org/v2/incomeLevel?format=json&page=2` | 200 | `[{"page":"2","pages":"1","per_page":"50","total":"7"},[]]` | `incomelevel_list_page_beyond_range_page_2.json` |
| 64 | `…/country/BRA/indicator/SP.POP.TOTL?date=2018:2024` with header `Accept: application/json` | 200 | still `text/xml`: Accept header ignored | — (scratch) |
| 65 | `…/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=JSON` | 200 | JSON (format value is case-insensitive). content-type `application/json` | — (scratch) |
| 66 | `…/country/BRA;…;ZAF/indicator/SP.POP.TOTL?format=jsonstat&date=2018:2024` | 200 | `"WDI data; total 63 records."`, 63 values (no 50-row truncation) | — (scratch) |

Docs consulted: [About the Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation), [API Basic Call Structures](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581), [Indicator API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries), [API Error Codes](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes).

## Open questions

1. **Rate limits**: unknown. No rate-limit headers and no 429s at about 1 req/s over 66 calls, plus 40 verification calls. The documented 503/105 "Service currently unavailable" was never seen. What does throttling look like in practice?
2. **Ordering across data releases**: the row order was deterministic within dataset version `2026-10-08`. Does it survive a WDI update? (Mitigation regardless: never rely on order. Use natural keys.)
3. **Mid-pagination dataset updates**: can `total`/`lastupdated` change between page 1 and page N of one fetch? We can't reproduce this. The algorithm guards against it by restarting.
4. **Cache staleness vs. `lastupdated`**: a cached copy can be up to about 24 h old. This applies to a Cloudflare HIT and also to a Cloudflare MISS served from the origin-side cache (see Caching & latency). Is that acceptable for the assignment, or should the pipeline record `cf-cache-status` and `last-modified` as lineage? (Recommended: record them, especially `last-modified`.) ~~UNVERIFIED: adding junk query params to bust the cache was never tested.~~ **Tested in other areas:** an unknown param (`cb=`, `zz_probe=`) always gives a Cloudflare MISS; it usually also escapes the origin caches (area 03 V6/V7, synthesis T5, area 02 C4) but not always (area 02 V24 and C7 came from the inner cache). It is undocumented; the README uses it only for retries after a validation failure.
5. **Missing cells for our grid**: today all 252 cells (9 × 4 × 7) have non-null values. When a cell is missing in a future release, will WDI send a row with `"value":null` (the pattern seen for aggregate `XY` in #56) or omit the row? The design must handle both (left join against the expected grid). It is unknown which one occurs for single-country WDI calls.
6. **Status code for error 120**: docs say HTTP 404, live returns HTTP 200. Is the 404 ever returned (e.g. for other parameters)? The errors area doc should confirm. *Critic note:* area 05 saw id 120 with HTTP 200 for every cause it tried (country, indicator, date, page, per_page, source, incomeLevel, empty list item); the only 404s were the HTML unknown-path page and the JSON gateway object, neither carrying id 120. A 404 + id 120 was never observed.
7. **Why per_page caps at 32767** (it looks like an Int16 parse) and why the over-limit error is an HTML 400 rather than a JSON error envelope. This is undocumented. We treat it as a config validation rule.
8. Does the `date` "out-of-range range is ignored" behavior also apply when only **some** of the requested countries lack data in the range? Not tested. The date-range assertion in the loader covers it either way.

## Verification

Adversarial verification pass on **2026-10-14 UTC** (01:23–01:31). We made **40 fresh live calls** (V01–V34 below, `GET`, no credentials, about 1 s apart). They used `curl -sS -m 90` rather than `-m 30`, so slow responses could be measured instead of cut off. We also re-read the docs and checked the saved fixtures offline. Raw bodies and headers from these calls are kept in scratch only. No new fixtures were added.

**Fixture integrity.** All 33 sample files named in this doc exist in `samples/pagination/`. All 30 `.json` bodies parse, and none starts with a BOM. Each sample has a `.meta.json` with `url`/`http_status`/`content_type`/`fetched_at_utc`/`notes`. The 400 `.html` body starts with `EF BB BF`. The two `.xml` bodies are byte-identical. The per_page=10 pages 1–7 concatenate to the per_page=1000 fixture exactly. The per_page=50 pages 1+2 do the same, and the default-per_page fixture is byte-identical to `per_page_50_page_1`.

| claim | result | URL or doc used |
|---|---|---|
| Envelope is top-level array `[meta, rows]`; rows at `$[1]`, stop at `$[0].pages` | confirmed | V02–V05, all fixtures |
| Pipeline calls #2–#5 return 1 page, 63 rows each; bodies match the saved fixtures | confirmed (live bodies **byte-identical** to `obs_*_9c_per_page_1000.json`) | V02 `…/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000&page=1`, V03–V05 same pattern |
| Pipeline call #1 (country metadata) returns 9 rows, 1 page | confirmed: `{"page":1,"pages":1,"per_page":"1000","total":9}`, income ids BRA/MEX/ZAF=UMC, CAN/DEU/GBR/JPN/USA=HIC, IND=LMC | V01 `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=1000&page=1` |
| Default `per_page` = 50 | confirmed (`"pages":2,"per_page":50,"total":63`) | V07 `…/SP.POP.TOTL?date=2018:2024&format=json`; doc [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) "default setting is 50 results per page" |
| Max `per_page` = 32767; ≥ 32768 gives HTTP 400 `text/html` with BOM, `cf-cache-status: BYPASS` | confirmed (32767 → 200 `"per_page":32767`; 32768 and 40000 → 400, 1647 bytes, BOM, BYPASS) | V08 `…&per_page=32767`, V09 `…&per_page=32768`, V10 `…&per_page=40000` |
| Docs give no `per_page` maximum | confirmed | [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| `pages = ceil(total/per_page)`, every page but the last is full | confirmed (per_page=9 → 7 pages × 9 rows) | V16 `…&per_page=9&page={7..1}` |
| Page past the end → HTTP 200, `rows: []`, meta echoes requested page | confirmed (observations and country; country `per_page` string `"1"`) | V11 `…&per_page=10&page=8`, V32 `https://api.worldbank.org/v2/country/BRA;CAN;DEU?format=json&per_page=1&page=4` → `[{"page":4,"pages":3,"per_page":"1","total":3},[]]` |
| Zero-match → `[{"page":0,…,"sourceid":null,"lastupdated":null},null]` | confirmed (also for all 9 countries, and for `date=2031`) | V12 `…/country/BRA;…;ZAF/indicator/SP.POP.TOTL?date=1900&format=json`, V21 `…/country/BRA/…?format=json&date=2031` |
| Invalid `page`/`per_page` → HTTP 200 + one-element error-120 array | confirmed. **Extended:** `page=abc`, `date=abcd` and multi-indicator without `source` give the same body | V13 `page=0`, V14 `per_page=abc`, V15 `page=abc`, V34 `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=abcd`, V23 |
| Docs list error 120 as HTTP 404 (docs vs live disagree) | confirmed (docs: 120 → 404 "Parameter "XXX" has an invalid value."; live: 200) | [898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes) |
| Meta types: obs numeric; `country`/`indicator` `per_page` string; `incomeLevel` all four strings | confirmed | V01 (`"per_page":"1000"`), V31 `https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.CD?format=json` (`"per_page":"50"`), V06 `https://api.worldbank.org/v2/incomeLevel?format=json&per_page=1000&page=1` (`{"page":"1","pages":"1","per_page":"1000","total":"7"}`) |
| Docs' indicator sample shows `"per_page": "50"` as a string | confirmed | [898599](https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries) |
| `sourceid` null on multi-indicator (`source=2`); rows grouped by indicator | confirmed | V24 `…/country/BRA;USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?source=2&format=json&date=2023:2024` |
| Multi-indicator "needs `source`" was tagged [documented] only | confirmed, now also [observed]: without `source` → HTTP 200 error 120. Docs: single source, max 60 indicators | V23 `…/country/BRA;USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2023:2024`; [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| Out-of-range date **range** silently ignored → all years 1960–2025 | confirmed (`1900:1901`, `1950:1959`, `2026:2030` each → `total 66`, 2025…1960) | V17, V18, V20 `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date={range}&per_page=100` |
| Partial overlap is clipped; single out-of-range year → zero-match | confirmed (`1958:1960` → 1 row 1960; `2024:2030` → 2 rows 2025, 2024; `2031` → zero-match) | V19, V22, V21 |
| `SP.POP.TOTL` already has 2025 values | confirmed (BRA 2025 = 212812405) | V17, V22 |
| `source=11` ignored on single-indicator call | confirmed (`"sourceid":"2"`, 5 rows; content-type `application/json` with no charset) | V27 `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=2020:2024&source=11` |
| No `format` → XML (documented default); `Accept: application/json` ignored; `format=JSON` works | confirmed | V25 (Accept header → `text/xml`), V26 `…&format=JSON`; [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) "By default, all requests will respond with valid XML." |
| `jsonstat` has no paging meta | confirmed (`{"WDI":{"label":"WDI data; total 6 records.",…}}`) | V33 `…/country/BRA;USA/indicator/SP.POP.TOTL?format=jsonstat&date=2022:2024` |
| Row order independent of request order | confirmed (`USA;BRA;ZAF` vs `ZAF;USA;BRA` byte-identical) | V28, V29 `…/country/{perm}/indicator/SP.POP.TOTL?format=json&date=2022:2023` |
| Row order "follows the country code" | refined → it is the **World Bank 3-letter id**, not ISO2 and not name (aggregates returned XD, XY, XM, XN, XT = HIC, INX, LIC, LMC, UMC) | V30 `https://api.worldbank.org/v2/country/XD;XM;XN;XT;XY/indicator/SP.POP.TOTL?format=json&date=2024` |
| Aggregates have `countryiso3code: ""`; XY value null | confirmed (all 5 `""`; XY 2024 `null`) | V30; offline: #56 scratch body has 7 null rows, all XY |
| `country/all`: aggregates first (by name), then economies by name; 1862 rows in one page; page 2 @1000 = slice [1000:2000] | confirmed offline from the original agent's saved #56/#57 bodies. Not re-called (375 KB) | scratch `all_countries_pop_2018_2024_per_page_{32767,1000_page_2}.json` |
| Value mixes int and float literals (USA GDP ints); `date` string; `unit`/`obs_status` `""`; `decimal` 0/0/1/0 | confirmed (GDP: 56 floats + 7 int literals, all USA; PCAP `decimal` 1, others 0) | fixtures, re-matched live by V02–V05 |
| Region trailing whitespace (LAC only cited) | corrected "Latin America & Caribbean " only → also `"Sub-Saharan Africa "` (ZAF) | fixture `envelope_country_9_selected.json`, V01 |
| Pagination deterministic, shown by runs 1–3 being byte-identical (run 3 = CDN MISS) | corrected: runs 2–3 were **cached copies** (run 3 had `last-modified` = run 1's and `max-age` 86364–86379, from an origin-side cache), so they did not test determinism → re-tested with fresh origin pages (`per_page=9`, reverse order, `last-modified`=`date`): union = reference, 63 unique. **Conclusion still holds** | original scratch headers for #29–#35; V16 |
| "Reordering query params changes the cache key" / "`last-modified` on a HIT shows when the CDN copy was made" | corrected → reordering changes only the **Cloudflare** key. An origin-side cache that ignores param order still serves the old copy, so a CF MISS can be hours old. `last-modified` is the origin generation time. `max-age` counts down (< 86400 = cached copy). The ≤ 24 h staleness bound holds | V07 (`max-age=85493`, `last-modified` = #10's), V31 (HIT, `age 26421`, `max-age 36166`, `last-modified` 13 Oct 04:12:37 → about 21 h old), #57 (MISS, `max-age 52545`, `last-modified` 13 Oct 15:51:24) |
| `cache-control: public, max-age=86400` | corrected → `max-age=86400` only on fresh origin responses. Values from 36166 to 86379 were seen on cached copies | V07, V08, V11, V13, V14, V31 |
| Uncached responses 9–22 s; "30 s timeout too tight" | corrected → 9–**37** s. Three calls were over 30 s: V05 (pipeline call #5, `SP.DYN.LE00.IN`, 32.0 s), V16 p7 (31.5 s), V33 (37.1 s). A 30 s timeout **would fail**. Recommendation raised to read timeout ≥ 90 s | V05, V16, V33 timings |
| No rate-limit headers; no 429/5xx | confirmed for 40 more responses (still says nothing about the real limits; open question 1 stands) | all V calls |
| dlt 1.31.0: `PageNumberPaginator` + `$[1]` pages 7 fixtures to 63 rows; `int(total)` handles string pages; error-120 silently stops under `stop_after_empty_page=True`, raises `ValueError` when False | confirmed (original offline script re-run against the fixtures with dlt 1.31.0; `paginators.py` line 180 does `total = int(total)`; `_stop_after_this_page` = `stop_after_empty_page and not data`) | offline, no network |
| Cache-busting via junk query params "is possible" | **UNVERIFIED** (never tested in this area); marked in open question 4. *Critic note:* tested elsewhere (area 03 V6/V7, synthesis T5, area 02 V24 and C4/C7): always escapes Cloudflare, only sometimes the inner origin cache | — |
| Rate limits, ordering across releases, mid-pagination release, future missing-cell shape (null row vs absent row), partial-country out-of-range ranges | unverified (cannot be determined with live calls today); open questions 1–3, 5, 8 stand | — |

Verification calls (V-numbers cited above; base `https://api.worldbank.org/v2`, `9c` = `BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF`):

| # | URL | HTTP status | observation |
|---|---|---|---|
| V01 | `/country/9c?format=json&per_page=1000&page=1` | 200 | per_page `"1000"` string, total 9; CF HIT |
| V02–V05 | `/country/9c/indicator/{SP.POP.TOTL,NY.GDP.MKTP.CD,NY.GDP.PCAP.CD,SP.DYN.LE00.IN}?format=json&date=2018:2024&per_page=1000&page=1` | 200 ×4 | 63 rows each; byte-identical to fixtures; 0.6 s / 5.5 s / 0.3 s / **32.0 s** |
| V06 | `/incomeLevel?format=json&per_page=1000&page=1` | 200 | all meta strings, total `"7"` |
| V07 | `/country/9c/indicator/SP.POP.TOTL?date=2018:2024&format=json` | 200 | per_page 50, pages 2; CF MISS but `max-age=85493`, `last-modified` 01:11:58 (origin cache) |
| V08 / V09 / V10 | same + `&per_page=32767` / `32768` / `40000` | 200 / **400** / **400** | 400s: `text/html`, BOM, BYPASS |
| V11 | `/country/9c/indicator/SP.POP.TOTL?per_page=10&page=8&format=json&date=2018:2024` | 200 | `[{"page":8,"pages":7,…},[]]` |
| V12 | `/country/9c/indicator/SP.POP.TOTL?date=1900&format=json` | 200 | zero-match, rows `null` |
| V13 / V14 / V15 | same base + `page=0` / `per_page=abc` / `page=abc` | 200 ×3 | error 120 body |
| V16 | `/country/9c/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=9&page={7..1}` | 200 ×7 | fresh (`last-modified`=`date`); union = reference, 63 unique; page 7 took 31.5 s |
| V17 / V18 / V20 | `/country/BRA/indicator/SP.POP.TOTL?format=json&date={1900:1901 / 1950:1959 / 2026:2030}&per_page=100` | 200 ×3 | filter ignored, total 66 (2025→1960) |
| V19 / V22 | same with `date=1958:1960` / `2024:2030` | 200 ×2 | clipped: 1 row (1960) / 2 rows (2025, 2024) |
| V21 | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2031` | 200 | zero-match |
| V23 | `/country/BRA;USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2023:2024` | 200 | error 120 (no `source`) |
| V24 | same + `source=2` | 200 | total 8, `sourceid null`, grouped by indicator |
| V25 | `/country/BRA/indicator/SP.POP.TOTL?date=2020:2024` + `Accept: application/json` | 200 | `text/xml` |
| V26 | `/country/BRA/indicator/SP.POP.TOTL?date=2020:2024&format=JSON` | 200 | JSON |
| V27 | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2020:2024&source=11` | 200 | `sourceid "2"`, 5 rows, `application/json` (no charset) |
| V28 / V29 | `/country/USA;BRA;ZAF/…` / `/country/ZAF;USA;BRA/indicator/SP.POP.TOTL?format=json&date=2022:2023` | 200 ×2 | byte-identical; BRA, USA, ZAF order |
| V30 | `/country/XD;XM;XN;XT;XY/indicator/SP.POP.TOTL?format=json&date=2024` | 200 | iso3 `""` ×5, XY `null`; order XD, XY, XM, XN, XT; 17.2 s |
| V31 | `/indicator/NY.GDP.MKTP.CD?format=json` | 200 | per_page `"50"`; HIT, copy about 21 h old |
| V32 | `/country/BRA;CAN;DEU?format=json&per_page=1&page=4` | 200 | `[{"page":4,"pages":3,"per_page":"1","total":3},[]]` |
| V33 | `/country/BRA;USA/indicator/SP.POP.TOTL?format=jsonstat&date=2022:2024` | 200 | no paging meta; **37.1 s** |
| V34 | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=abcd` | 200 | error 120 body |

## Critic additions

The completeness critic made no new pagination calls in this area. It added the inner-cache finding to **Caching & latency** (evidence: area 02 C1–C7 and an offline re-analysis of every saved header file) and the 45.2 s latency observation (area 01 C1). Cross-file corrections made here: a pointer from "Calls our pipeline will make" to the README's canonical R0–R11 URLs, the cache-buster status in open question 4 and the verification table, the WCF-400 and validation-retry lines in the pseudocode, and an answer to open question 6 from area 05.
