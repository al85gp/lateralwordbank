# 02 — Indicator observations (the core data call)

> **Area key:** `observations` · **Samples:** `api_calls/samples/observations/` (each `<name>.json` / `.xml` has a `<name>.meta.json` sidecar)
> **Live probing:** 2026-10-14 01:11–01:22 UTC, 48 requests, no credentials · **WDI `lastupdated` at probe time:** `"2026-10-08"`
> **Adversarial verification:** 2026-10-14 01:29–01:35 UTC, 32 more requests (ids `V1`–`V32`, listed in the Verification section) plus a doc re-read. Corrections were made in place; see the Verification table.
> **Completeness critic:** 2026-10-14 01:59–02:02 UTC, 10 more requests (ids `C1`–`C10`, see "Critic additions"); findings tagged **[observed by critic]**.
> **Tags:** **[observed]** means we called the API today; the evidence row number is in the Evidence log. **[documented]** cites a doc URL. Where docs and live behavior disagree, both are recorded and live behavior drives the design.

## TL;DR

- **Recommended call shape:** one request per indicator, with all 9 countries in a single semicolon list:
  `country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/{ID}?source=2&format=json&date=2018:2024&per_page=1000`
  That is **4 requests, 1 page each, 63 rows each, 252 rows in total**. The 4 golden fixtures are exactly these URLs.
- **Coverage today:** **252 of 252 cells are present**, with a non-null value for every (country, indicator, year). No in-scope value is missing *today*, so null and absent handling is tested with the out-of-scope fixtures listed below.
- **Missing data takes two forms.** A year that is inside the source's time axis but not published comes back as a row with `"value":null`; Life expectancy 2025 is an example. A year outside the time axis is **omitted entirely**; 2026 is an example. The pipeline must build the expected 9×4×7 grid and left-join the API rows onto it. Never coerce a gap to 0.
- **Errors arrive as HTTP 200** with a 1-element body: `[{"message":[{"id":"120",...}]}]`. The docs say code 120 returns 404.
- **Response mix-up:** some URLs were answered with a body that belongs to a *different* query. This is apparently an origin-side cache. Every response must be validated against its own request (see Failure modes).
- **`value` is a JSON number: an int token or a float token, never a string, never in scientific notation, or `null`.** Ints and floats mix inside one indicator, so store it as DOUBLE.
- **Never use** `gapfill` (it fabricates values), `mrnev` (it hides gaps), `mrv` (it overrides `date`) or `scale` (it is lossy).

## Why the pipeline needs this

This call is the pipeline's only source of facts. Each row is one observation for one (indicator, country, year), and everything downstream is built on these rows: comparisons across income groups by year (joined to country metadata on ISO3), coverage and missing-data reporting, and the traceability requirement. Each output value must point back to one row in one stored raw response, identified by URL, fetch time, page, `lastupdated` and a response hash. Tests must run without the live API, so the raw bodies saved here byte-for-byte become the test fixtures.

## Endpoints & URL templates

| endpoint | URL template | used by our pipeline? | notes |
|---|---|---|---|
| Observations, single indicator (**recommended**) | `https://api.worldbank.org/v2/country/{iso3_list}/indicator/{indicator_id}?source=2&format=json&date={start}:{end}&per_page={n}&page={p}` | **YES**, 4 calls | `{iso3_list}` is a semicolon-joined list such as `BRA;CAN;...`. Shape verified live (#2–5, #45–48). |
| Observations, multiple indicators | `https://api.worldbank.org/v2/country/{iso3_list}/indicator/{id1};{id2};...?source={source_id}&format=json&date=...&per_page=...` | No (evaluated) | `source=` is **required**: a fresh call without it returns error 120 (#28, re-confirmed on a different fresh URL in V8). The docs imply this but do not use the word "required": multiple indicators are queried from "a single data source by providing the indicator codes separated by `;` as well as the source ID". The envelope `sourceid` is `null` (#6, V9). Documented limits: 60 indicators, 1,500 characters between two slashes, 4,000-character URL ([898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)). |
| Observations, default format | same URL without `format=` | No | Returns `text/xml` (#35). |
| Bulk download | `.../country/{c}/indicator/{id}?source=2&downloadformat=csv\|xml\|excel` | No | [documented] Returns a ZIP ([898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)). Not probed. |
| Advanced data query (beta) | `https://api.worldbank.org/v2/sources/{src}/country/{c}/series/{id}/time/{t}/data?format=json` | No | [documented] ([1886686](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886686-advanced-data-api-queries), section titled "BETA"). The doc examples also include an optional `/version/{v}` segment before `/data`, e.g. `.../sources/57/country/ALB/series/SP.POP.TOTL/time/all/version/199704/data?format=json`. Not probed. |

## Parameters

| param | allowed values | default | observed behavior | notes |
|---|---|---|---|---|
| `{iso3_list}` (path) | ISO3 or ISO2 codes, case-insensitive, separated by `;`. `all` and aggregates are documented. | — | ISO3 uppercase (#2), ISO2 lowercase (#9) and reversed order (#8) **all return identical rows**. The response always echoes the canonical uppercase ISO2 in `country.id` and ISO3 in `countryiso3code`. **One invalid code fails the whole request** with error 120 (#38). | Send ISO3 uppercase. Validate the list before calling. |
| `{indicator_id}` (path) | WDI series code, case-insensitive | — | Lowercase `sp.pop.totl` is accepted and echoed as `SP.POP.TOTL` (#9). An unknown code returns error 120 (#36). | — |
| `format` | `json`, `xml`, `jsonP` (needs `prefix`), `jsonstat` [documented] | `xml` [documented] | Without it the response is `text/xml` (#35). | Always send `format=json`. |
| `date` | `YYYY`, `YYYY:YYYY`, `YYYYMmm`, `YYYYQn`, `YTD:YYYY` [documented, [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581)] | All years in the source | `2018:2024` is **inclusive at both ends** (2018 and 2024 are present, #2–5). A reversed `2024:2018` returns a body byte-identical to `2018:2024` (#40 vs #1); area 05 once got a transient HTTP 400 WCF page for it (README Contradictions #2). `2018-2024` returns error 120 (#39). Years past the source's time axis are silently dropped (#10). | Send `date=2018:2024`. |
| `per_page` | integer | 50 [documented]; observed 50 (#12) | `1000` is accepted and echoed (#2). `20` paginates correctly (#13–16). | Maximum is **32767**; 32768 and above give HTTP 400 HTML [observed, areas 04/05]. |
| `page` | 1..`pages` | 1 | Pages 1–4 at per_page=20, concatenated, are **identical** to the single-page fixture (#13–16). Page 5 of 4 returns HTTP 200 with `[{meta},[]]` (#17). | Loop while `page < pages`. |
| `source` | source id (`2` = World Development Indicators) | Single indicator: WDI (`sourceid:"2"`, #1) | Single indicator: **byte-identical** with or without `source=2`, verified for all 4 indicators (#45–48 vs #2–5). Multi-indicator: **required** (#28). | Send `source=2` to pin WDI, and assert `sourceid=="2"`. |
| `mrv` | integer | — | Returns the N most recent years **for which any requested country has data**, not the last N calendar years. Gini for MEX;JPN at `mrv=4` returned 2024, 2022, 2020, 2019 (#23). It **overrides `date`**: `date=2018:2024&mrv=3` is byte-identical to `mrv=3` (#22 vs #18). Verification made this stronger: `date=2018:2024&mrv=2` for SP.POP.TOTL returned **2025**, a year outside the requested range (V28). | **Do not use.** |
| `mrnev` | integer | — | Returns the N most recent **non-null** values per country, which hides gaps. JPN returned 2020 and 2019, MEX returned 2024 and 2022 (#27). The envelope has `sourceid:null` (#19, #27, V17), and **every row omits the `unit` key** (#19, #27, V17). | **Do not use.** |
| `gapfill` | `Y`/`N`, used with `mrv` [documented] | N (inferred from behavior; the docs state no default) | **Fabricates values.** It filled the unpublished 2025 Life expectancy with the 2024 value for BRA and USA (#29), and filled Gini JPN 2022–2024 with the 2020 value (#24). `obs_status` stays `""`, so filled values cannot be told apart from real ones. | **Must NOT use.** It breaks "missing data is meaningful". |
| `frequency` | `Q`/`M`/`Y`, used with `mrv` [documented] | — | `Y` changes nothing for annual WDI data (#31 is identical to #18). | Not needed. |
| `footnote` | `y`/`n` [documented, [1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)] | n (inferred: no `footnote` key without it; the docs state no default) | Adds `"footnote":""` (an empty string) as the last key of every row (#32, #33). No non-empty footnote was seen *in this area's calls*, including on the documented example. **Cross-area correction:** area 03 found `"Break in series."` on DEU SP.POP.TOTL 2022 and 2023, and the critic's `footnote=y` captures of all four golden URLs confirm these are the only 2 non-empty footnotes in the 252-cell scope (C8–C10 + synthesis R8 variant). **The key can also appear without `footnote=y`:** the critic got a footnote-bearing body for a URL without the parameter (C2, inner-cache substitution) [observed by critic]. | Optional (README decision D4). Treat the key as optional whatever is sent. |
| `scale` | `y`/`n` [documented] | n (inferred; the docs state no default) | USA GDP 2024 became `"value":29,"scale":"trillions"` (#34), and CAN GDP 2024 became `"value":2,"scale":"trillions"` (V26). This is **lossy**: it rounds to `decimal` places, which is 0 here. | **Must NOT use.** |
| `ctrycode` | `y`/`n` [documented] | n | Not probed | Not needed. `countryiso3code` is always present. |
| Language prefix `/v2/{lang}/` | `en`, `es`, `fr`, `ar`, `zh`, ... [documented] | `en` | Not probed | Do not use. Names would change. |

## Response envelope & field dictionary

A successful body is a **2-element JSON array** of the form `[ {envelope}, [rows...] ]`. An error body is a **1-element array**: `[ {"message":[...]} ]`. Field order within a row, as observed: `indicator, country, countryiso3code, date, value, [scale], [unit], obs_status, decimal, [footnote]`. **The key set varies** (found during verification): `unit` is missing from `mrnev` responses (#19, #27, V17), and `"scale":""` appears on the null-value rows of multi-indicator responses (#41, V9). The loader must treat every key except `indicator`, `country`, `countryiso3code`, `date` and `value` as optional.

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `$[0].page` | number (int) | `1` | no | `api_page` | Echoes the requested page, even past the last page (#17). |
| `$[0].pages` | number (int) | `1` | no | `api_pages` | `ceil(total/per_page)` |
| `$[0].per_page` | number (int) | `1000` | no | `api_per_page` | Echoes the request. Used in the request/response consistency check. |
| `$[0].total` | number (int) | `63` | no | `api_total_rows` | **Includes null-value rows**: the LE 2023:2026 call has total 27, of which 9 are null (#10). Covers all pages. |
| `$[0].sourceid` | **string** or **null** | `"2"` | `null` for multi-indicator calls (#6, #41, #44), for `mrnev` (#19) and in the zero-match envelope (area 04 #49) | `source_id` | A quoted string, not a number. |
| `$[0].lastupdated` | string `YYYY-MM-DD` or **null** | `"2026-10-08"` | Not seen null in this area; **`null` in the zero-match envelope** `[{"page":0,…,"lastupdated":null},null]` (area 04 #49, area 05 shape B) | `source_last_updated` | The date of the source's last update [documented, [1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)]. It was the same for all 4 indicators. |
| `$[1]` | array or **null** | `[...]` | `[]` past the last page (#17). **`null`** on a zero-match (area 04 #49). **Missing** on error. | — | Row list |
| `$[1][*].indicator.id` | string | `"SP.POP.TOTL"` | no | `indicator_id` | Canonical uppercase |
| `$[1][*].indicator.value` | string | `"Population, total"` | no | `indicator_name` | — |
| `$[1][*].country.id` | string | `"US"` | no | `country_iso2` | **ISO2, not ISO3.** Confirmed for all 9 countries: BR, CA, DE, GB, IN, JP, MX, US, ZA (#2). This holds for WDI (source 2) only: an out-of-scope source-15 response returned `"id":"IND"` (ISO3) here (V32). |
| `$[1][*].country.value` | string | `"United States"` | no | `country_name` | — |
| `$[1][*].countryiso3code` | string | `"USA"` | Not empty in scope. **Can be `""`** in other sources: source 15 returned `""` (V32). | `country_iso3` | **Join key** to country metadata, and part of the natural key. Assert that it is non-empty and in the requested set. |
| `$[1][*].date` | **string** | `"2024"` | no | `year` (cast to INT) | A quoted 4-digit year, not a number. Sub-annual sources use other formats: `YYYYMmm` / `YYYYQn` are [documented] for the `date` *parameter*, and a source-15 monthly response returned `"date":"2026M08"` (V32). An INT cast must fail loudly on such strings. |
| `$[1][*].value` | **number (int *or* float token)** or **null** | `340003797`, `2185821610689.31`, `null` | `null` = not published (#10, #11) | `value` (DOUBLE) | Never a string or scientific notation in any response we probed. See the numeric notes below. |
| `$[1][*].unit` | string, or **key absent** | `""` | `""` (empty string, never null) in every non-`mrnev` sample. **The key is absent** in `mrnev` responses (#19, #27, V17), and in wrong-body responses that carry an `mrnev` body (V18, V19). | `unit` | Treat as an optional key. `NULLIF(unit,'')` in staging. |
| `$[1][*].obs_status` | string | `""` | `""` everywhere in scope. `"F"` (forecast) appears in source 27 (#42). | `obs_status` | `NULLIF(...,'')` |
| `$[1][*].decimal` | number (int) | `0`, `1` | no | `display_decimals_hint` | **Not** the number of decimals in `value`. LE has `decimal:0` but values like `82.1080487804878`. GDP per capita has `decimal:1`. Documented as a precision hint used with `scale`. |
| `$[1][*].footnote` | string, or **key absent** | `""`, `"Break in series."` | Usually present only with `footnote=y`, but **not reliably**: a request without the parameter returned footnote keys (C2, `burst_01` in area 05), and `footnote=n` still returns them (area 03 V8) | `footnote` | Optional key. Non-empty only for DEU SP.POP.TOTL 2022/2023 in scope (critic census C8–C10). |
| `$[1][*].scale` | string | `"trillions"`, `""` | Present with `scale=y` (#34, V26). **It also appears as `"scale":""` on null-value rows in multi-indicator (`source=2`) responses** without `scale=y`: all 9 LE 2025 rows in #41, and CAN/MEX LE 2025 in V9. It was **not** seen on null rows from single-indicator calls, with or without `source=2` (#10, #11, V6, V7). | — | Never request it. Tolerate (ignore) a stray `scale:""`. |
| error `$[0].message[*].id` | **string** | `"120"` | — | `api_error_id` | — |
| error `$[0].message[*].key` | string | `"Invalid value"` | — | `api_error_key` | — |
| error `$[0].message[*].value` | string | `"The provided parameter value is not valid"` | — | `api_error_message` | — |

## Behavior & gotchas

### Call-shape comparison (single vs. many countries vs. many indicators)

| shape | URL (abridged) | rows | pages @1000 | bytes | `sourceid` | evidence |
|---|---|---|---|---|---|---|
| 1 country × 1 indicator | `country/USA/indicator/SP.POP.TOTL?date=2018:2024` | 7 | 1 | 1,511 | `"2"` | #1 |
| **9 countries × 1 indicator** (recommended) | `country/BRA;...;ZAF/indicator/{ID}?source=2&date=2018:2024&per_page=1000` | 63 | 1 | 12,560–14,288 | `"2"` | #2–5, #45–48 |
| 9 countries × 4 indicators (`source=2`) | `country/BRA;...;ZAF/indicator/A;B;C;D?source=2&...` | 252 | 1 | 53,658 | **`null`** | #6 |
| 9 countries × 4 indicators, no `source` | same, without `source` | error 120 (fresh URL) | — | 102 | — | #28 (and anomaly #7) |

- [observed] **The rows are identical across shapes.** All 252 rows of the multi-indicator call (#6) equal the union of the 4 per-indicator calls (#2–5): zero differences after a key-by-key comparison.
- [observed] **Per-indicator calls are recommended**, for these reasons:
  1. The envelope keeps `sourceid:"2"`, which is useful lineage. A multi-indicator call returns `sourceid:null`.
  2. Multi-indicator calls *require* `source=`, and live behavior around a missing `source` was inconsistent: a fresh URL returned error 120 (#28), while a cached one returned 200 (#7).
  3. Failures and retries are isolated to one indicator. One bad code in a multi call fails the whole request (#36, #38).
  4. Each stored raw response maps to exactly one indicator, which keeps traceability and fixtures simple.
  5. Latency: one 54 KB multi call took 14.8 s and gave no time advantage.
  The cost is 4 requests instead of 1, which is negligible.
- [observed] **A 9-country semicolon list is safe.** 9 ISO3 codes add 35 characters to the path, far below the documented 1,500-character limit per segment.

### Coverage matrix for the full scope (as of 2026-10-14, WDI `lastupdated` 2026-10-08)

Legend: `P` = row present with a non-null value · `NULL` = row present with `"value":null` · `ABSENT` = no row returned. Source: the 4 golden fixtures `samples/observations/<INDICATOR>__9_countries__2018_2024.json`.

| country | indicator | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | present / null / absent |
|---|---|---|---|---|---|---|---|---|---|
| BRA | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| BRA | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| BRA | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| BRA | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| CAN | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| CAN | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| CAN | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| CAN | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| DEU | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| DEU | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| DEU | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| DEU | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| GBR | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| GBR | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| GBR | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| GBR | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| IND | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| IND | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| IND | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| IND | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| JPN | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| JPN | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| JPN | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| JPN | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| MEX | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| MEX | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| MEX | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| MEX | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| USA | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| USA | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| USA | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| USA | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |
| ZAF | SP.POP.TOTL | P | P | P | P | P | P | P | 7 / 0 / 0 |
| ZAF | NY.GDP.MKTP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| ZAF | NY.GDP.PCAP.CD | P | P | P | P | P | P | P | 7 / 0 / 0 |
| ZAF | SP.DYN.LE00.IN | P | P | P | P | P | P | P | 7 / 0 / 0 |

**Summary:** 9 countries × 4 indicators × 7 years = **252 expected cells → 252 present, 0 null, 0 absent.** Each golden fixture has `total: 63`, `pages: 1`, and no duplicate (country, year) keys.

### How missing data appears

- [observed] **Unpublished year inside the source's time axis → a row with `"value":null`.** In `date=2023:2026` for Life expectancy, every 2025 row is null, for example `{"countryiso3code":"BRA","date":"2025","value":null,"unit":"","obs_status":"","decimal":0}` (#10). In-range gaps for the sparse Gini series (out of scope) are also null rows: 63 rows returned for 63 cells, with 25 of them null (#11).
- [observed] **A year outside the source's time axis → no row at all.** In `date=2023:2026`, 2026 is **absent**: 27 rows, not 36 (#10, #41). `total` counts the null rows but not the absent ones.
- [observed] Each indicator reaches a different year. On 2026-10-14, 2025 values **already exist** for SP.POP.TOTL, NY.GDP.MKTP.CD and NY.GDP.PCAP.CD (USA 2025 GDP = `30769700000000`), while SP.DYN.LE00.IN 2025 is null (#41). Our scope stops at 2024 by requirement.
- **Pipeline implication:** generate the expected grid (9 ISO3 × 4 indicators × 7 years) and left-join the API rows onto it. Classify each cell as `present`, `reported_null` (row present, `value` null) or `not_returned` (no row). None of these may become 0, and an empty-string `unit` or `obs_status` must not be mistaken for missing data.

### Ordering

- [observed] Within one indicator, rows are **sorted by country (alphabetical by code), then `date` descending** (2024 → 2018), regardless of the order of the request list. Requesting `ZAF;USA;...;BRA` still returns BRA first (#8). The order is not by country *name*, because GBR (United Kingdom) comes before IND (India). For our 9 countries, ISO2 order and ISO3 order are the same. **Verification resolved this:** a request for `AUT;AUS` returned AUS (ISO2 `AU`) before AUT (ISO2 `AT`) (V30). ISO2 order would put AT first, so the sort is **not ISO2**. It is consistent with **ISO3 ascending**, though an internal key that happens to match cannot be ruled out.
- [observed] In a multi-indicator call, rows are **grouped by indicator in the request order** and then by country (#44 requested LE, PCAP, GDP, POP and received that order).
- [observed] Ordering is stable across pages: pages 1–4 concatenate to the single-page body (#13–16). **Do not rely on it**; dedupe on the natural key instead.

### Numeric representation

- [observed] `value` is always a JSON number token or `null`. No quoted numbers and no exponent (`e`/`E`) appeared in any sample (regex scan of the raw bytes).
- [observed] **Ints and floats mix within one indicator.** All SP.POP.TOTL values are int tokens (max `1450935791`, India 2024). NY.GDP.MKTP.CD has 56 float tokens and **7 int tokens**: all USA years, e.g. `29298013000000` for 2024, which is larger than int32. NY.GDP.PCAP.CD has floats with 9–11 decimals. SP.DYN.LE00.IN has floats with 2–13 decimals, e.g. `82.1080487804878`. Even a "decimal" series can emit an int token: Gini IND 2023 is `23` (#11).
- [observed] At most 15 significant digits were seen, so every value round-trips exactly through IEEE-754 float64. Use **DOUBLE** for `value`, or DECIMAL(38,15) if exact decimal text matters. Do not use INT, because GDP overflows int32 and fractional values would be truncated.
- *Design note (not verified against dlt):* with type inference, a column first seen as `bigint` may produce a variant column such as `value__v_double` when float tokens arrive. Pin a dlt column hint `value: {data_type: double}`.
- [observed] `decimal` is not the precision of `value`; see the field dictionary. [documented] It controls rounding when `scale=y` ([1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)).

### Envelope, headers and caching

- [observed] Envelope types: `page`, `pages`, `per_page` and `total` are JSON numbers. `sourceid` is a string (`"2"`) or `null`. `lastupdated` is a string (`"2026-10-08"`).
- [observed] Responses go through Cloudflare (`server: cloudflare`, `cf-cache-status: MISS|HIT`) with `cache-control: public, max-age=86400`. A re-run within 24 h can therefore be served from the edge (#43 was a `HIT` in 0.06 s). The 4 golden URLs re-fetched during verification were all edge `HIT`s (`age: 434–435`) and byte-identical to the fixtures (V1–V4). `last-modified` is the time the origin generated the response: on a `MISS` it equals the response time, and on a `HIT` it equals the original miss time (V1: `last-modified 01:21:50`, the fixture's fetch time). It is useless for change detection. No `ETag` header was observed in any of the 32 verification responses either. Cloudflare also sets `__cf_bm` and `_cfuvid` cookies, which the pipeline does not need to keep.
- [observed] gzip is supported (`content-encoding: gzip` with `Accept-Encoding: gzip`). The decompressed body was byte-identical to the fixture (#43): 930 bytes on the wire versus 12,560 bytes. This was re-confirmed in V31 (930 bytes) and on a fresh `MISS` for PCAP in V16 (1,339 bytes, which decompressed to the 13,894-byte fixture).
- [observed] **`Content-Type` varies.** Usually it is `application/json;charset=utf-8` with `access-control-*` headers. Some fast responses (0.08–0.34 s) came back as plain `application/json` with no `access-control-*` headers. Every confirmed wrong-body response had this second form; see Failure modes. Do not branch on the charset parameter.
- [observed by critic, offline re-analysis of the saved headers] **The plain `application/json` responses look fresh in every other header.** All 13 cf-`MISS` responses with plain `application/json` in the saved and scratch header files (C2, C7, V5, V7, V18, V19, V24, area 05 `burst_01`, trailing slash, `jsonp`, unknown param, area 04 V27 (`source=11`), area 03 V8 (`footnote=n`)) have `last-modified` equal to `date` (±1 s) and `max-age=86400`, exactly like a genuine origin render, yet several carried another query's body. So `last-modified`/`max-age` reveal the origin *output-cache* copies (old `last-modified`, `max-age` < 86400) but **not** this inner path. The only header marker is the missing `charset` (plus sub-second latency on a request that would otherwise be slow). Log the raw `Content-Type` per attempt so lineage can flag these responses. Live repro: C2, C7.
- [observed] **Latency is highly variable:** 0.06 s (edge hit) to **39.5 s**. The original probing saw a maximum of 22.6 s (#5, a 63-row LE call). Verification saw **39.5 s for an 8-row multi-indicator call** (V9), 25.0 s for a 7-row single-country call (V15), and 23.3 s for an empty past-the-end page (V14). Several other calls took more than 14 s (#6, #10, #17, #41, #44). Latency does not depend on response size. Use a read timeout of at least 60 s, preferably 90 s, plus retries.
- [observed] The data was **deterministic across re-fetches**: the golden calls fetched about 10 minutes apart, with and without `source=2`, were byte-identical (#2–5 vs #45–48).
- [documented] "The World Bank API is not guaranteed to have 100% uptime"; cache results and fail gracefully ([902064](https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices)).

## Failure modes

| failure | how it looks | how to detect | handling |
|---|---|---|---|
| Invalid indicator, country, date syntax, or multi-indicator without `source` | **HTTP 200**, body `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]` (102 bytes) (#28, #36–39) | `len(body)==1 and "message" in body[0]` | Fail fast and do not retry, because this is a config bug. **Docs disagree:** [898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes) lists 120 as HTTP **404**. Live behavior returns 200, so never trust the status code alone. |
| One bad code among valid ones | The whole request fails with the 120 body, with **no partial rows** (#38) | as above | Validate codes against the country metadata before calling |
| **Wrong body for the request** (origin-side cache confusion) | HTTP 200 with a well-formed body that belongs to another query. #25 asked for `mrnev=2` and received the `mrv=4&gapfill=Y` body (8 rows instead of 4). A retry was a Cloudflare `HIT` with the same wrong body (#26). #20 asked for `gapfill=Y` and received the plain `mrv=3` body. #7, a multi-indicator call **without** `source`, returned 200 with the `source=2` body, while a fresh equivalent errored (#28). | Validate each response against its request: (a) `meta.page`/`meta.per_page` equal the requested values; (b) single-indicator calls have `sourceid=="2"`; (c) every row's `indicator.id` equals the requested id, `countryiso3code` is in the requested set, and `int(date)` is within `[2018,2024]`; (d) the row count across pages equals `meta.total`, with no duplicate keys. | Retry with backoff, then fail loudly. **Critic finding [observed by critic]: retry with a NEW cache-buster param, not the same URL.** Reproduced in the recommended call shape (C1–C4, MEX;ZAF NY.GDP.PCAP.CD `source=2`): the URL *without* `footnote` returned the `footnote=y` body 1 s later (plain `application/json`, 0.21 s, `last-modified` = `date`, `max-age=86400`), an exact retry was a Cloudflare `HIT` with the same wrong body, and the same URL + `&cb=<token>` returned the correct body (fresh render, charset present). A cache-buster does not always escape the inner cache (C7, and V24 below), so give each retry attempt its own token and validate every attempt. *Hypothesis, not proven:* the origin cache ignores some query parameters (`source`, `gapfill`, `mrnev`, `frequency`) when matching. **Verification reproduced the anomaly:** after `CAN;DEU ... SP.DYN.LE00.IN?format=json&mrnev=2` (V17), the `mrv=2` URL (V18) and the `mrv=2&gapfill=Y` URL (V19) both returned the **mrnev body byte-for-byte** (`sourceid:null`, no `unit` key). Both came back in about 0.17 s as plain `application/json`, with `cf-cache-status: MISS`. So the cache also seems to confuse `mrv` with `mrnev`. The core params were honored in verification too: `date=2018:2024` then `date=2020:2024` returned 14 and 10 correct rows (V20, V21), and `page=1`/`page=2` at `per_page=5` returned the correct slices (V22, V23). An unknown parameter (`&zz_probe=1`) is **silently ignored**: there was no error, and the body was byte-identical to the URL without it (V24). That response was itself served with the cached-body signature, so **an unknown parameter does not work as a cache-buster**. Our recommended URLs only vary the honored params plus `source`, and `source` was verified harmless for single-indicator calls (#45–48, V6 vs V7). |
| Page past the end | `[{"page":5,"pages":4,...},[]]` (#17) | Empty row list | Stop condition: stop when `page >= pages`, and also stop on an empty list. |
| Slow or hanging origin | 9–40 s responses observed (#4, #5, #10, #17, #41; verification V9 39.5 s, V14 23.3 s, V15 25.0 s); the critic saw 36.9 s on the R10 URL + `footnote=y` (C9) and 45.2 s on an aggregate call (area 01 C1) | timeout | Read timeout 90 s (README contract), plus up to 5 attempts with exponential backoff and jitter |
| Service unavailable / unexpected error | [documented] 105 → HTTP 503, 199 → HTTP 500 ([898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes)) | HTTP 5xx, or an error body with id 105/199 | Retry with backoff. **Not observed today.** |
| `format=` forgotten | `text/xml` body (#35) | Content-Type is not JSON | Always send `format=json`. Reject non-JSON. |
| Data revised between runs | `lastupdated` changes and values may change. Not observed today. | Store `lastupdated` and a response hash on every load | Merge on the natural key `(indicator_id, country_iso3, year)` so a re-run is idempotent. Keep raw responses for audit. |

## Calls our pipeline will make

Four requests, exactly these URLs (they are byte-for-byte the golden fixture URLs):

```
https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?source=2&format=json&date=2018:2024&per_page=1000
https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000
https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000
https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000
```

| indicator | expected `total` | pages | rows/page | body bytes (today) | fixture |
|---|---|---|---|---|---|
| SP.POP.TOTL | 63 | 1 | 63 | 12,560 | `SP.POP.TOTL__9_countries__2018_2024.json` |
| NY.GDP.MKTP.CD | 63 | 1 | 63 | 13,193 | `NY.GDP.MKTP.CD__9_countries__2018_2024.json` |
| NY.GDP.PCAP.CD | 63 | 1 | 63 | 13,894 | `NY.GDP.PCAP.CD__9_countries__2018_2024.json` |
| SP.DYN.LE00.IN | 63 | 1 | 63 | 14,288 | `SP.DYN.LE00.IN__9_countries__2018_2024.json` |

- **Request count:** `Σ_indicators ceil(63 / per_page)` = 4 × ceil(63/1000) = **4 requests, 252 rows, about 54 KB**. If `per_page` were left at the default 50, the count would be 4 × 2 = 8. The single multi-indicator alternative would be 1 request, but it is not recommended (see above).
- `63 = 9 × 7` is the *expected maximum*, because every in-range year is returned as a row, either with a value or null. Fewer rows would mean some years are `ABSENT`. More rows would mean a contract violation.
- Still implement the generic paging loop: `page=1..pages`, stopping on an empty list, and assert that the concatenated row count equals `total`. This costs nothing and protects against a lower `per_page` cap.
- Per-row lineage to persist: `request_url`, `api_page`, `fetched_at_utc`, `source_id`, `source_last_updated`, `raw_response_sha256` (or the stored raw file path), and the row's index in `$[1]`.

## Evidence log

All calls were HTTP GET with no auth, made between 2026-10-14 01:11:46Z and 01:21:54Z. The full call log with timings was kept in the session's scratch space and is not included; every call is listed below. Sample paths are relative to `samples/observations/`.

| # | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=50` | 200 | 7 rows 2024→2018. `sourceid:"2"`, `lastupdated:"2026-10-08"`, `date` is a string, `value` an int. 0.28 s | `single_country_single_indicator__USA__SP.POP.TOTL__2018_2024.json` |
| 2 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000` | 200 | 63 rows, 1 page, all int values, `country.id` ISO2 | superseded by #45 (byte-identical) |
| 3 | `.../indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=1000` (same countries) | 200 | 63 rows. 56 float and 7 int tokens (all USA), max 15 significant digits | superseded by #46 (byte-identical) |
| 4 | `.../indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=1000` | 200 | 63 rows, `decimal:1`, 9.2 s | superseded by #47 (byte-identical) |
| 5 | `.../indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=1000` | 200 | 63 rows, `decimal:0` with 2–13 decimals, **22.6 s** | superseded by #48 (byte-identical) |
| 6 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | 200 | 252 rows, `sourceid:null`, rows identical to #2–5, 14.8 s | `multi_indicator__4_indicators__9_countries__2018_2024__source2.json` |
| 7 | same as #6 **without** `source=2` | 200 | Body **byte-identical to #6**, 0.2 s, plain `application/json`. Suspected cached body (see #28). | `anomaly__suspected_cached_body__multi_indicator_no_source_returned_source2_body.json` |
| 8 | `https://api.worldbank.org/v2/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.POP.TOTL?format=json&date=2024&per_page=1000` | 200 | Output is still sorted BRA…ZAF, not in request order | `ordering__countries_reversed__SP.POP.TOTL__2024.json` |
| 9 | `https://api.worldbank.org/v2/country/br;ca;de;gb;in;jp;mx;us;za/indicator/sp.pop.totl?format=json&date=2024&per_page=1000` | 200 | Lowercase ISO2 and indicator accepted, canonical ids echoed, same rows as #8 | `codes__iso2_lowercase__sp.pop.totl__2024.json` |
| 10 | `.../indicator/SP.DYN.LE00.IN?format=json&date=2023:2026&per_page=1000` (9 countries) | 200 | `total:27`. 2025 rows have `value:null`, **2026 rows are absent**. 16.8 s | `date_range_beyond_latest__SP.DYN.LE00.IN__9_countries__2023_2026.json` |
| 11 | `.../indicator/SI.POV.GINI?format=json&date=2018:2024&per_page=1000` (9 countries, out of scope) | 200 | 63 rows, 25 with `value:null`. In-range gaps are null rows. IND 2023 is the int token `23`. | `missing_data_example__SI.POV.GINI__9_countries__2018_2024.json` |
| 12 | `.../indicator/SP.POP.TOTL?format=json&date=2018:2024` (9 countries, no per_page) | 200 | Default `per_page:50`, `pages:2`, 50 rows | `paging__default_per_page__SP.POP.TOTL__9_countries__2018_2024.json` |
| 13 | `...&per_page=20&page=1` | 200 | `page:1,pages:4`, 20 rows (plain `application/json`, content verified) | `paging__per_page_20_page_1__SP.POP.TOTL__9_countries__2018_2024.json` |
| 14 | `...&per_page=20&page=2` | 200 | 20 rows, DEU 2018 → JPN 2020 | `paging__per_page_20_page_2__SP.POP.TOTL__9_countries__2018_2024.json` |
| 15 | `...&per_page=20&page=3` | 200 | 20 rows | `paging__per_page_20_page_3__SP.POP.TOTL__9_countries__2018_2024.json` |
| 16 | `...&per_page=20&page=4` | 200 | 3 rows. Pages 1–4 concatenated == #2 rows | `paging__per_page_20_page_4__SP.POP.TOTL__9_countries__2018_2024.json` |
| 17 | `...&per_page=20&page=5` | 200 | `[{"page":5,"pages":4,...},[]]`, 16.8 s | `paging__per_page_20_page_5_beyond_last__SP.POP.TOTL__9_countries__2018_2024.json` |
| 18 | `https://api.worldbank.org/v2/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3` | 200 | 2024, 2023, 2022. The null 2025 is skipped. | `optional_param__mrv_3__SP.DYN.LE00.IN__BRA_USA.json` |
| 19 | `...BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrnev=3` | 200 | Same rows as #18, but `sourceid:null` | `optional_param__mrnev_3__SP.DYN.LE00.IN__BRA_USA.json` |
| 20 | `...BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&gapfill=Y` | 200 | **Wrong body:** byte-identical to #18 (compare with the fresh #29) | `anomaly__wrong_body__gapfill_url_returned_plain_mrv_body__SP.DYN.LE00.IN__BRA_USA.json` |
| 21 | `...BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&frequency=Y` | 200 | Identical to #18, plain content type. The fresh #31 agrees. | `anomaly__suspected_cached_body__frequency_Y_url_returned_plain_mrv_body__SP.DYN.LE00.IN__BRA_USA.json` |
| 22 | `...BRA;USA/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&mrv=3` | 200 | Byte-identical to #18. `mrv` overrides `date`. | `optional_param__date_and_mrv_combined__SP.DYN.LE00.IN__BRA_USA.json` |
| 23 | `https://api.worldbank.org/v2/country/MEX;JPN/indicator/SI.POV.GINI?format=json&mrv=4` | 200 | Years 2024, 2022, 2020, 2019 (years with any data), with null rows | `optional_param__mrv_4__SI.POV.GINI__MEX_JPN.json` |
| 24 | `...MEX;JPN/indicator/SI.POV.GINI?format=json&mrv=4&gapfill=Y` | 200 | Years 2025–2022, nulls carried forward (JPN 2024 = 32.3, the 2020 value), `obs_status:""` | `optional_param__mrv_4_gapfill_Y__SI.POV.GINI__MEX_JPN.json` |
| 25 | `...MEX;JPN/indicator/SI.POV.GINI?format=json&mrnev=2` | 200 | **Wrong body:** byte-identical to #24 (8 rows) | `anomaly__wrong_body__mrnev_2_url_returned_gapfill_body__SI.POV.GINI__MEX_JPN.json` |
| 26 | same as #25 (retry) | 200 | Same wrong body, `cf-cache-status: HIT`, 0.08 s | (scratch only) |
| 27 | `https://api.worldbank.org/v2/country/JPN;MEX/indicator/SI.POV.GINI?format=json&mrnev=2` | 200 | Correct mrnev result: 4 rows (JPN 2020, 2019; MEX 2024, 2022), `sourceid:null` | `optional_param__mrnev_2__SI.POV.GINI__JPN_MEX.json` |
| 28 | `https://api.worldbank.org/v2/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.DYN.LE00.IN;NY.GDP.PCAP.CD;NY.GDP.MKTP.CD;SP.POP.TOTL?format=json&date=2018:2024&per_page=1000` | 200 | Error body `id:"120"`. Multi-indicator without `source` fails. | `error__multi_indicator_without_source__http200_error_120.json` |
| 29 | `https://api.worldbank.org/v2/country/USA;BRA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&gapfill=Y` | 200 | **Fabricated 2025:** BRA 2025 = 76.023 (= 2024), USA 2025 = 78.890243902439 (= 2024) | `optional_param__mrv_3_gapfill_Y__SP.DYN.LE00.IN__USA_BRA_fresh.json` |
| 30 | `https://api.worldbank.org/v2/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | Byte-identical to #3 (and #46). Fixed sort. | `single_indicator_with_source2__NY.GDP.MKTP.CD__9_countries_reversed__2018_2024.json` |
| 31 | `https://api.worldbank.org/v2/country/USA;BRA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&frequency=Y` | 200 | Byte-identical to #18. `frequency=Y` has no effect. | `optional_param__mrv_3_frequency_Y__SP.DYN.LE00.IN__USA_BRA_fresh.json` |
| 32 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y` | 200 | Adds `"footnote":""` to each row | `optional_param__footnote_y__USA__SP.POP.TOTL__2018_2024.json` |
| 33 | `https://api.worldbank.org/v2/country/CHN/indicator/SL.AGR.EMPL.ZS?format=json&date=2018:2020&footnote=y` | 200 | Documented example. Footnotes are still `""`. | `optional_param__footnote_y__CHN__SL.AGR.EMPL.ZS__2018_2020.json` |
| 34 | `https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024&scale=y` | 200 | `"value":29,"scale":"trillions"` (lossy) | `optional_param__scale_y__USA__NY.GDP.MKTP.CD__2024.json` |
| 35 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?date=2024` | 200 | `text/xml` default. Attributes on `<wb:data page= pages= per_page= total= sourceid= lastupdated=>` | `format_default_xml__USA__SP.POP.TOTL__2024.xml` |
| 36 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTLX?format=json&date=2018:2024` | 200 | Error 120 | `error__invalid_indicator_code__SP.POP.TOTLX.json` |
| 37 | `https://api.worldbank.org/v2/country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | Error 120 | `error__invalid_country_code__XXX.json` |
| 38 | `https://api.worldbank.org/v2/country/USA;XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | Error 120, no partial USA rows | `error__mixed_valid_and_invalid_country__USA_XXX.json` |
| 39 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?format=json&date=2018-2024` | 200 | Error 120 | `error__bad_date_format__2018-2024.json` |
| 40 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?format=json&date=2024:2018` | 200 | Reversed range. Byte-identical to #1. | `date_range_reversed__USA__SP.POP.TOTL__2024_2018.json` |
| 41 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?source=2&format=json&date=2025:2026&per_page=1000` | 200 | 36 rows, all 2025: POP, GDP and PCAP present, LE null. 2026 is absent. 18.9 s | `out_of_scope_years__4_indicators__9_countries__2025_2026__source2.json` |
| 42 | `https://api.worldbank.org/v2/country/CHN/indicator/NYGDPMKTPKDZ?format=json&mrv=3` | 200 | Source 27: `obs_status:"F"`, future dates 2026–2028, `lastupdated:"2026-06-11"` | `obs_status_example__CHN__NYGDPMKTPKDZ__mrv3.json` |
| 43 | #2 URL with `Accept-Encoding: gzip` | 200 | `content-encoding: gzip`, 930 bytes on the wire, `cf-cache-status: HIT`. Decompressed == fixture. | (scratch only) |
| 44 | `https://api.worldbank.org/v2/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.DYN.LE00.IN;NY.GDP.PCAP.CD;NY.GDP.MKTP.CD;SP.POP.TOTL?source=2&format=json&date=2024&per_page=1000` | 200 | Indicators come back in request order, countries in fixed order, `sourceid:null`. 17.2 s | `ordering__multi_indicator_reversed__source2__9_countries_reversed__2024.json` |
| 45 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?source=2&format=json&date=2018:2024&per_page=1000` | 200 | **Golden.** Byte-identical to #2 | `SP.POP.TOTL__9_countries__2018_2024.json` |
| 46 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | **Golden.** Byte-identical to #3 | `NY.GDP.MKTP.CD__9_countries__2018_2024.json` |
| 47 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | **Golden.** Byte-identical to #4 | `NY.GDP.PCAP.CD__9_countries__2018_2024.json` |
| 48 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | 200 | **Golden.** Byte-identical to #5. Served in 0.18 s as plain `application/json` (origin-cache pattern). | `SP.DYN.LE00.IN__9_countries__2018_2024.json` |

## Open questions

1. **Origin-side cache keying is unknown.** Which query params does it ignore, and can a recommended URL ever receive another query's body? The core params were always honored today, but the request/response validation above is the mitigation. No documented cache-busting parameter exists. *Verification update:* an unknown parameter is silently ignored, and it did **not** bypass the origin cache (V24). The anomaly was reproduced between `mrnev` and `mrv` / `mrv&gapfill` (V17–V19). `date` and `page` were honored (V20–V23).
2. ~~**Maximum `per_page`** was not probed here; it is deferred to the pagination area. 1000 works.~~ **Answered by areas 04/05:** 32767; 32768+ gives HTTP 400 HTML.
3. **Revisions:** how often do 2018–2024 values change between WDI releases, and is `lastupdated`, which is source-level and not per observation, the only signal? No per-row timestamp exists.
4. **Could an in-range WDI year (2018–2024) ever be `ABSENT` rather than a null row?** It was not observed. The time axis seems to end at 2025 today. The pipeline must handle it either way.
5. ~~**Does the sort use ISO2 or ISO3?**~~ **Resolved in verification:** it is not ISO2. `AUT;AUS` returned AUS first (V30), which is consistent with ISO3 ascending. This is irrelevant anyway if we dedupe on the natural key.
6. **Footnote content:** ~~no non-empty `footnote` was ever observed~~ **Answered:** area 03 and the critic's census of all four golden URLs with `footnote=y` (C8–C10 plus `samples/synthesis/obs_SP.POP.TOTL__golden_url_plus_footnote_y.json`) found exactly 2 non-empty footnotes in scope, DEU SP.POP.TOTL 2022 and 2023 "Break in series.". Whether to request them is README decision D4; fixtures for both choices now exist.
7. **`unit` and `obs_status`** were always `""` for our 4 indicators. Could a future release populate them, for example with estimate or forecast flags in WDI?
8. **5xx and error 105/199 behavior** (HTTP status and body shape) was not observed live. Retry design relies on the docs.
9. **2025 values for SP.POP.TOTL and the GDP series are now published.** The assignment fixes 2018–2024. Confirm that the scope stays fixed rather than "latest 7 years".
10. **dlt int/float inference** on `value` needs verifying at implementation time; pin `double`.

## Verification

**Verifier run (UTC date): 2026-10-14**, 01:27–01:35 UTC. 32 live GET requests with no credentials, spaced about 1 s apart, plus a re-read of the official docs ([898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581), [898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes), [1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api), [898599](https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries), [902064](https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices), [1886686](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886686-advanced-data-api-queries)). All 42 sample files named in the original doc were checked offline: each exists, parses as JSON (or, for the one `.xml`, as XML), and has a `.meta.json` with `url`, `http_status`, `content_type`, `fetched_at_utc` and `notes`. Each meta `url` equals the evidence-log URL exactly, or for the abbreviated `...` rows matches it in substance. The 9 new `verify__*` fixtures also parse and have sidecars. All of the doc's byte-identity claims between fixtures were re-checked with `cmp`, and all hold. New verifier fixtures are prefixed `verify__` in `samples/observations/`.

### Verification calls

| V# | URL | HTTP | time / edge | observation | sample file |
|---|---|---|---|---|---|
| V1–V4 | the 4 golden URLs exactly as in "Calls our pipeline will make" | 200 ×4 | 0.08–0.16 s, `HIT` | Byte-identical to the 4 golden fixtures. `age` 434–435, no ETag | (scratch) |
| V5 | `https://api.worldbank.org/v2/country/bra;can;deu;gbr;ind;jpn;mex;usa;zaf/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | 200 | 0.19 s, `MISS` | Bypasses the edge cache. Byte-identical to the LE golden fixture, so 63/63 non-null is re-confirmed past the edge | (scratch) |
| V6 | `https://api.worldbank.org/v2/country/BRA;USA/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2024:2026&per_page=1000` | 200 | 0.30 s | `total:4`. 2025 rows have `value:null` with **no `scale` key**. 2026 is absent | `verify__null_rows_single_indicator_source2__SP.DYN.LE00.IN__BRA_USA__2024_2026.json` |
| V7 | same as V6 without `source=2` | 200 | 0.73 s | Byte-identical to V6 | (scratch) |
| V8 | `https://api.worldbank.org/v2/country/CAN;MEX/indicator/SP.DYN.LE00.IN;SP.POP.TOTL?format=json&date=2020&per_page=100` | 200 | 0.23 s | Error 120 body (102 bytes). Multi-indicator without `source` fails | (scratch) |
| V9 | `https://api.worldbank.org/v2/country/CAN;MEX/indicator/SP.DYN.LE00.IN;SP.POP.TOTL?source=2&format=json&date=2024:2025&per_page=100` | 200 | **39.5 s** | `sourceid:null`. Grouped LE then POP, in request order. **The LE 2025 null rows carry `"scale":""`**. POP 2025 is published | `verify__multi_indicator_null_rows_have_scale_empty__CAN_MEX__2024_2025__source2.json` |
| V10 | `https://api.worldbank.org/v2/country/CAN/indicator/NY.GDP.MKTP.CDX?format=json&date=2020` | 200 | 0.22 s | Error 120 | (scratch) |
| V11 | `https://api.worldbank.org/v2/country/BRA;ZZZ/indicator/NY.GDP.PCAP.CD?format=json&date=2020` | 200 | 0.18 s | Error 120, no partial BRA rows | (scratch) |
| V12 | `https://api.worldbank.org/v2/country/CAN/indicator/SP.POP.TOTL?format=json&date=2019-2020` | 200 | 0.24 s | Error 120 | (scratch) |
| V13 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024` | 200 | 11.0 s | Default `per_page:50`, `pages:2`, 50 rows, all equal to the golden rows | (scratch) |
| V14 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=50&page=3` | 200 | 23.3 s | `[{"page":3,"pages":2,...},[]]` | (scratch) |
| V15 | `https://api.worldbank.org/v2/country/CAN/indicator/SP.DYN.LE00.IN?format=json&date=2024:2018` | 200 | 25.0 s | The reversed range returns the same 7 rows as the golden CAN subset | (scratch) |
| V16 | `https://api.worldbank.org/v2/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000` + `Accept-Encoding: gzip` | 200 | 0.27 s, `MISS` | gzip, 1,339 bytes, decompresses to the PCAP golden fixture byte-for-byte (fixed sort) | (scratch) |
| V17 | `https://api.worldbank.org/v2/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrnev=2` | 200 | 0.28 s, charset | `sourceid:null`. **Rows have no `unit` key** | `verify__mrnev_2_rows_have_no_unit_key__SP.DYN.LE00.IN__CAN_DEU.json` |
| V18 | `https://api.worldbank.org/v2/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrv=2` | 200 | 0.17 s, plain `application/json` | **Wrong body:** byte-identical to V17 | `verify__anomaly__mrv_2_url_returned_mrnev_2_body__SP.DYN.LE00.IN__CAN_DEU.json` |
| V19 | `https://api.worldbank.org/v2/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrv=2&gapfill=Y` | 200 | 0.17 s, plain `application/json` | **Wrong body:** byte-identical to V17 | `verify__anomaly__mrv_2_gapfill_Y_url_returned_mrnev_2_body__SP.DYN.LE00.IN__CAN_DEU.json` |
| V20 | `https://api.worldbank.org/v2/country/GBR;JPN/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | 9.0 s | 14 rows, equal to the golden GBR+JPN subset | (scratch) |
| V21 | same with `date=2020:2024` | 200 | 0.24 s | 10 rows. `date` was honored | (scratch) |
| V22 / V23 | same as V20 with `per_page=5&page=1` / `&page=2` | 200 | 0.30 / 0.27 s | `pages:3`. The slices equal golden rows 1–5 and 6–10 | (scratch) |
| V24 | V20 URL + `&zz_probe=1` | 200 | 0.24 s, plain `application/json` | Unknown param ignored, byte-identical to V20 | (scratch) |
| V25 | `https://api.worldbank.org/v2/country/br;za/indicator/ny.gdp.pcap.cd?format=json&date=2024` | 200 | 0.23 s | Lowercase ISO2 and indicator accepted. Canonical `NY.GDP.PCAP.CD`, `BR`/`BRA` echoed | (scratch) |
| V26 | `https://api.worldbank.org/v2/country/CAN/indicator/NY.GDP.MKTP.CD?format=json&date=2024&scale=y` | 200 | 0.26 s | `"value":2,"scale":"trillions"` (lossy) | (scratch) |
| V27 | `https://api.worldbank.org/v2/country/CAN/indicator/SP.POP.TOTL?date=2024` | 200 | 0.14 s | `text/xml` default | (scratch) |
| V28 | `https://api.worldbank.org/v2/country/GBR;IND/indicator/SP.POP.TOTL?format=json&date=2018:2024&mrv=2` | 200 | 0.28 s | Returns **2025** and 2024. `mrv` overrides `date` | `verify__mrv_overrides_date__SP.POP.TOTL__GBR_IND__date_2018_2024_mrv_2.json` |
| V29 | `https://api.worldbank.org/v2/country/IND;ZAF/indicator/SP.DYN.LE00.IN?format=json&mrv=2&gapfill=Y` | 200 | 0.23 s | 2025 fabricated as the 2024 value (IND 72.235, ZAF 66.312). `obs_status:""` | `verify__gapfill_fabricates_2025__SP.DYN.LE00.IN__IND_ZAF.json` |
| V30 | `https://api.worldbank.org/v2/country/AUT;AUS/indicator/SP.POP.TOTL?format=json&date=2024` | 200 | 3.3 s | AUS (AU) is returned before AUT (AT), so the sort is not ISO2 | `verify__sort_key_is_iso3__SP.POP.TOTL__AUT_AUS__2024.json` |
| V31 | golden SP.POP.TOTL URL + `Accept-Encoding: gzip` | 200 | 0.07 s, `HIT` | 930 bytes on the wire, which decompress to the fixture | (scratch) |
| V32 | `https://api.worldbank.org/v2/country/IND/indicator/DPANUSSPF?format=json&mrv=2&frequency=M` (out of scope, source 15) | 200 | 0.33 s | `date:"2026M08"`. **`country.id:"IND"` (ISO3), `countryiso3code:""`** | `verify__out_of_scope_source15_monthly__IND__DPANUSSPF__mrv2_freqM.json` |

### Claim-by-claim results

| claim | result | URL or doc used |
|---|---|---|
| The 4 golden fixtures are exactly the recommended URLs, with 63 rows, 1 page and `total:63` each | confirmed | Fixture `.meta.json` urls, offline parse, V1–V4 |
| Coverage: 252/252 cells present and non-null, 0 null, 0 absent, no duplicate keys | confirmed (offline on all 4 fixtures; the LE fixture re-confirmed from origin, bypassing the edge) | V1–V5 |
| Multi-indicator (`source=2`) rows == union of the 4 per-indicator calls | confirmed (252 = 252, set-equal) | `multi_indicator__...source2.json` vs golden fixtures |
| Multi-indicator without `source` → HTTP 200 + error 120 | confirmed on a new fresh URL | V8 |
| Docs say `source` is "required" for multi-indicator calls | corrected: the docs **imply** it ("from a single data source ... as well as the source ID") but do not say "required" | [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| Envelope `sourceid` is a string `"2"` for single-indicator calls and `null` for multi-indicator and `mrnev` calls | confirmed | V6, V9, V17 |
| `date` is a quoted string; `value` is a number token or null, never quoted, no exponent | confirmed (regex scan of the raw bytes of every fixture) | all fixtures |
| GDP has 56 float + 7 int tokens, all ints USA; USA 2024 = `29298013000000` | confirmed | `NY.GDP.MKTP.CD__...json` |
| POP is all ints, max `1450935791` (IND 2024); PCAP has 9–11 decimals; LE has 2–13 decimals and contains `82.1080487804878`; max 15 significant digits | confirmed | golden fixtures |
| Gini IND 2023 is the int token `23`; Gini has 63 rows, 25 null | confirmed | `missing_data_example__SI.POV.GINI__...json` |
| `decimal` is 0 for POP/GDP/LE and 1 for PCAP, and is not the precision of `value`; it drives `scale` rounding | confirmed | fixtures; [1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api) ("precision ... based on the `<wb:decimal>` tag value") |
| `country.id` is ISO2 and `countryiso3code` is ISO3 for all 9 | confirmed for WDI. Added the caveat that source 15 returns ISO3 in `country.id` and `""` in `countryiso3code` | fixtures, V32 |
| `unit` is **always** `""`, never null, in all samples | **corrected:** `""` in every non-`mrnev` sample → **key absent** in `mrnev` responses | `optional_param__mrnev_*` fixtures, V17 |
| `scale` is only present with `scale=y` | **corrected:** it is also present as `"scale":""` on null-value rows of multi-indicator `source=2` responses, but not on single-indicator null rows | `out_of_scope_years__...source2.json` (9 rows), V9; V6/V7 |
| Field order `indicator, country, countryiso3code, date, value, [scale], unit, obs_status, decimal, [footnote]` | corrected: `unit` is optional too | all fixtures |
| `total` counts null rows but not absent years; a year outside the time axis (2026) is absent; 2025 LE is null | confirmed | V6, `date_range_beyond_latest__...json` |
| 2025 is already published for POP/GDP/PCAP (USA GDP 2025 = `30769700000000`) and null for LE | confirmed | `out_of_scope_years__...json`, V9 |
| Default `per_page` = 50 | confirmed (live and documented: "The default setting is 50 results per page.") | V13; [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| Pages concatenate to the single-page result; a page past the last → HTTP 200 `[{meta},[]]`, with `page` echoed | confirmed | paging fixtures, V14, V22–V23 |
| Reversed date range returns the same rows; `2018:2024` is inclusive | confirmed | V15, fixtures |
| Malformed date / invalid indicator / invalid country / one bad code in the list → HTTP 200 + error 120, no partial rows | confirmed on new fresh URLs | V10, V11, V12 |
| Docs map 120 → HTTP 404, 105 → 503, 199 → 500 | confirmed (doc table re-read) | [898620](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes) |
| Rows are sorted by country code, then date descending, regardless of request order; the key (ISO2 vs ISO3) is unknown | confirmed, and the key is **resolved: not ISO2**, consistent with ISO3 | V16, V30 |
| Multi-indicator responses group indicators in request order | confirmed | V9, `ordering__multi_indicator_reversed__...json` |
| Lowercase ISO2 country codes and a lowercase indicator are accepted and echoed canonically | confirmed | V25 |
| `mrv` overrides `date` | confirmed, more strongly: it returned 2025, outside `date=2018:2024` | V28 |
| `gapfill=Y` fabricates values and leaves `obs_status` empty | confirmed | V29 |
| `scale=y` is lossy | confirmed | V26 |
| `format` defaults to XML; formats are xml/json/jsonP/jsonstat; limits are 60 indicators, 1,500 characters per segment, 4,000-character URL; date formats | confirmed | V27; [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| Defaults `gapfill=N`, `footnote=n`, `scale=n` | corrected to "inferred from behavior": the docs list the values but state no default | [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581), [1886674](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api) |
| Wrong-body anomaly (origin-side cache) | confirmed and **reproduced** (`mrv` and `mrv&gapfill` URLs got the `mrnev` body) | V17–V19 |
| Core params (`date`, `per_page`, `page`) are always honored | confirmed for `date` and `page`/`per_page` | V20–V23 |
| An unknown param was not tested | resolved: it is ignored, does not error, and does not bust the origin cache | V24 |
| `source=2` is harmless for single-indicator calls | confirmed | V6 vs V7 (byte-identical) |
| Latency is 0.06–22.6 s | **corrected 22.6 s → 39.5 s** maximum observed | V9, V14, V15 |
| `cache-control: public, max-age=86400`; no ETag; `last-modified` = response time | confirmed, with a refinement: on an edge `HIT`, `last-modified` is the original miss time | V1 headers (and all 32 header files) |
| gzip is supported, 930 bytes on the wire for the POP fixture | confirmed | V31, V16 |
| "The World Bank API is not guaranteed to have 100% uptime" | confirmed (verbatim) | [902064](https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices) |
| Advanced data query (beta) URL template | confirmed as beta; added the optional `/version/{v}` segment seen in the doc examples | [1886686](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886686-advanced-data-api-queries) |
| Monthly/quarterly `date` strings look like `2012M01` / `2013Q1` | confirmed for monthly in a response (`2026M08`). Quarterly is documented for the parameter only. **UNVERIFIED:** the quarterly *response* format | V32; [898581](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581) |
| Multi-indicator call took 14.8 s (#6); `footnote` is always `""`; 5xx / error 105 / error 199 shapes | unverified: not re-probed (#6 timing is a single historical observation; 5xx cannot be induced) | — |

## Critic additions

Completeness-critic pass, 2026-10-14 01:59–02:02 UTC: 10 live requests in this area (`curl -sS -m 90`, about 1 s apart, no credentials). Each sample has a `.meta.json` and a `.headers.txt` (cookie values redacted). Findings are tagged **[observed by critic]** above. Sample paths: C1–C7 in `samples/observations/`, C8–C10 in `samples/synthesis/`.

| C# | URL | HTTP | time / edge / Content-Type | observation | sample file |
|---|---|---|---|---|---|
| C1 | `https://api.worldbank.org/v2/country/MEX;ZAF/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000&footnote=y` | 200 | 1.95 s, `MISS`, charset | Fresh render; 14 rows, all with a `footnote` key (`""`) | `critic__inner_cache_A_footnote_y__MEX_ZAF__NY.GDP.PCAP.CD.json` |
| C2 | same URL **without** `footnote` | 200 | 0.21 s, `MISS`, **plain** `application/json`, `last-modified` = `date`, `max-age=86400` | **Wrong body:** byte-identical to C1 (footnote keys present although not requested) | `critic__inner_cache_B_no_footnote__MEX_ZAF__NY.GDP.PCAP.CD.json` |
| C3 | C2 URL again | 200 | 0.07 s, `HIT` | Same wrong body: an exact retry cannot recover | `critic__inner_cache_C_no_footnote_retry_same_url__MEX_ZAF__NY.GDP.PCAP.CD.json` |
| C4 | C2 URL + `&cb=critic20261014b` | 200 | 1.06 s, `MISS`, charset | **Correct body:** 14 rows, no `footnote` key, values equal to C1 | `critic__inner_cache_D_no_footnote_cachebuster__MEX_ZAF__NY.GDP.PCAP.CD.json` |
| C5 | `https://api.worldbank.org/v2/country/ESP;PRT/indicator/SP.DYN.LE00.IN?format=json&mrnev=2` | 200 | 0.35 s, `MISS`, charset | Genuine `mrnev` body: `sourceid:null`, no `unit` key | `critic__inner_cache_F_mrnev_2__ESP_PRT__SP.DYN.LE00.IN.json` |
| C6 | same pair, `mrv=2` (1 s later) | 200 | 0.40 s, `MISS`, charset | **No substitution this time:** correct `mrv` body (`sourceid:"2"`, `unit` present). The V17–V19 anomaly is intermittent | `critic__inner_cache_G_mrv_2__ESP_PRT__SP.DYN.LE00.IN.json` |
| C7 | C6 URL + `&cb=critic20261014c` | 200 | 0.21 s, `MISS`, **plain** `application/json` | Served from the inner path with C6's body: the cache-buster escaped Cloudflare but **not** the inner cache (like V24) | `critic__inner_cache_H_mrv_2_cachebuster__ESP_PRT__SP.DYN.LE00.IN.json` |
| C8 | golden R9 URL + `&footnote=y` | 200 | 0.93 s, `MISS`, charset | 63 rows, all footnotes `""`; envelope and values identical to golden R9 | `obs_NY.GDP.MKTP.CD__golden_url_plus_footnote_y.json` |
| C9 | golden R10 URL + `&footnote=y` | 200 | **36.9 s**, `MISS`, charset | 63 rows, all footnotes `""`; identical to golden R10 apart from the key | `obs_NY.GDP.PCAP.CD__golden_url_plus_footnote_y.json` |
| C10 | golden R11 URL + `&footnote=y` | 200 | 0.93 s, `MISS`, charset | 63 rows, all footnotes `""`; identical to golden R11 apart from the key | `obs_SP.DYN.LE00.IN__golden_url_plus_footnote_y.json` |

**What this changes for the client contract (README §3, §5):**
- A failed validation, or an envelope `lastupdated` that disagrees with R0, must be retried with a **fresh cache-buster token per attempt** (e.g. `&cb=<run_id>-<attempt>`), because the exact URL is pinned to the wrong copy at the edge (C3; #26). This is a retry-only measure: first attempts keep the canonical golden URL.
- The token is not a guarantee (C7, V24): the inner cache sometimes ignores it. Validate every attempt; after the attempt budget, fail.
- Cross-file corrections made here: maximum `per_page` (now 32767 from areas 04/05), footnote content (2 non-empty values exist in scope), `footnote` key optionality, `lastupdated`/`sourceid`/`$[1]` nullability in the zero-match envelope, and read timeout aligned with the README (90 s).
