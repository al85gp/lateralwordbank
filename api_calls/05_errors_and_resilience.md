# 05 — Errors, failure modes & resilience

Area key: `errors` · Samples: `api_calls/samples/errors/` · Live calls made 2026-10-14 between 01:11Z and 01:18Z (UTC), 67 HTTP requests in total, no credentials sent.

Tags: **[observed]** = called live today (URL + status + excerpt in the Evidence log). **[documented]** = stated in the World Bank Data Help Desk docs (URL cited). Where they disagree, this doc says so, and the observed behavior drives the design.

Doc sources used:
- API Error Codes: https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes
- API Basic Call Structures: https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
- Development Best Practices: https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices
- About the Indicators API: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation
- New Features in V2: https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api

## Why the pipeline needs this

- Most API errors come back as **HTTP 200 with an error body**. Bad country codes, bad indicators, bad dates, bad `per_page`/`page` and unsupported languages all do this. A loader that only calls `raise_for_status()` will treat them as success. It then either crashes on a `KeyError` or, worse, loads zero rows and reports success. Because missing data has to be a meaningful outcome in this project, we must tell three things apart: "the API said no data", "the API rejected our request" and "the API is down".
- The assessment requires us to handle API failures and paging, run idempotent re-runs, trace every value back to the API response it came from, and run tests without the live API. The samples in this folder are the error fixtures for those tests: one file per failure shape.
- The API sits behind Cloudflare and an origin cache, and both cache **errors** as well as data for up to 24 h or more. Some cache-miss requests take 4–11 s, and the verification pass saw two trivial cold misses take 21.3 s and 23.7 s. One took more than 30 s. Timeouts and retries need to be tuned for this.

## Endpoints & URL templates

| endpoint | URL template | used by our pipeline? | notes |
|---|---|---|---|
| Indicator observations (single indicator) | `https://api.worldbank.org/v2/country/{ISO3;ISO3;...}/indicator/{INDICATOR}?format=json&date={Y1}:{Y2}&per_page={N}&page={P}` | **Yes**, one call per indicator | Bad values give HTTP 200 + error id 120. `per_page` > 32767 gives HTTP 400 HTML (reproduced). A reversed date range gave HTTP 400 HTML once (01:12Z) but **HTTP 200 with the normalized range** on re-check (01:28Z). [observed] |
| Country metadata | `https://api.worldbank.org/v2/country/{ISO3;ISO3;...}?format=json&per_page={N}` | **Yes**. It is the country dimension and doubles as a pre-flight check of the configured codes. | One invalid code fails the whole request with id 120. [observed] |
| Indicator metadata | `https://api.worldbank.org/v2/indicator/{INDICATOR}?format=json` | Optional pre-flight / indicator dimension | Unknown code gives id 120. Archived codes give id 175 on the **data** endpoint only. `/v2/indicator/IC.BUS.EASE.XQ` (archived) returns **HTTP 200 with a normal metadata record** (`source.id` `"57"`, "WDI Database Archives"), so this pre-flight does **not** detect archived indicators unless it also checks `source.id`. Here `meta.per_page` is a quoted string (`"50"`) and there are no `sourceid`/`lastupdated` keys. [observed] |
| Multi-indicator observations | `https://api.worldbank.org/v2/country/{...}/indicator/{IND1;IND2}?source=2&format=json&date=...` | **No** (not recommended) | Without `source=` it returns error 120. With `source=2` it works, but `meta.sourceid` is `null` and one call took 11 s. [observed] |
| Language-prefixed | `https://api.worldbank.org/v2/{lang}/...` | No (English is the default) | Unsupported `xx` gives 200 + error id 150. [observed] |
| Unknown path | `https://api.worldbank.org/v2/{unknown}` | No | **404 text/html** "Endpoint not found." [observed] |
| Wrong API version | `https://api.worldbank.org/v{N}/...` | No | **404 JSON object** `{ "statusCode": 404, "message": "Resource not found" }` [observed] |
| Unversioned (old v1 style) | `https://api.worldbank.org/countries/...` | No | **301** to `/v2/countries/...` with a plain-text body. [observed] |
| Plain HTTP | `http://api.worldbank.org/...` | No (always use https) | **302** to the same URL on https (from Cloudflare). [observed] |

## Parameters

How each parameter behaves when it is invalid or at its edges.

| param | allowed values | default | observed behavior | notes |
|---|---|---|---|---|
| `format` | `xml`, `json`, `jsonP` (needs `prefix`), `jsonstat` [documented, 898581] | `xml` [documented & observed] | Missing gives XML (`text/xml`). `format=JSON` (upper case) works. `format=yaml` gives **200 `text/xml` error id 111**. `format=jsonp` without `prefix` returns plain JSON. An `Accept: application/json` header is **ignored** (XML still returned). [observed] | Always send `format=json` explicitly. Docs list code 111 with response code 404, but live returns 200. |
| `date` | `YYYY`, `YYYY:YYYY` (plus months, quarters and `YTD:` per docs) | none (all years) | `abc` gives 200 + id 120. `2018:abc` gives 200 + id 120. **`2024:2018` gave 400 text/html WCF "Request Error" (4.1 s) at 01:12Z, but at 01:28Z the same URL gave 200 with the 7 rows for 2018–2024 (byte-identical to the baseline). A fresh `2022:2019` (USA) also gave 200 (total 4)**, so the API normalizes reversed ranges and the earlier 400 was not reproducible. **A range entirely outside the available years has its filter silently ignored**: `1800:1801`, `1900:1959` and `2030:2031` all returned the full 1960–2025 series (total 66). A range that is only *partly* outside (`1950:1965` → 1960–1965, total 6; `2024:2026` → 2024–2025, total 2) is clipped correctly. A *single* out-of-range year (`1800`, `2030`) gives 200 with the no-data envelope. `2018%3A2024` (encoded colon) is accepted. [observed] | Validate `start <= end` locally. Check every returned row's `date` against the requested range. |
| `per_page` | 1 … **32767** [observed] (no max documented) | 50 [documented & observed] | `0`, `-1` and `abc` give 200 + id 120. **`32768` and `100000` give 400 text/html WCF "Request Error"** (6–11 s). `32767` is accepted. [observed] | The maximum is Int16. Use something like 100–1000. |
| `page` | 1-based integer | 1 | `0` and `abc` give 200 + id 120. `999` (past the last page) gives **200 with meta `page:999, pages:1, total:7` and rows `[]`** (11.2 s). [observed] | Loop on `meta.pages`, not until an empty page comes back. |
| country list (path) | ISO3 or ISO2 codes joined with `;`, case-insensitive | n/a | Lower case accepted (byte-identical body). `BRA;XXX` gives **200 + id 120 for the whole request (no partial result)**. `BRA;;USA` gives id 120. `%3B` is accepted. Duplicates are de-duplicated (`BRA;BRA` → total 2 for 2023:2024). 15 codes work. **The ISO2 code `XX` on the data endpoint gives 200 with the no-data envelope, not an error.** Other unknown ISO2 codes (`QZ`, `ZZ`) give 200 + id 120, like unknown ISO3 codes, so this is specific to `XX`. [observed] | Use ISO3 codes only, and validate them before calling the data endpoint. |
| indicator (path) | indicator code, case-insensitive | n/a | Unknown gives 200 + id 120. **Archived `IC.BUS.EASE.XQ` gives 200 + id 175** (undocumented). Two codes without `source=` give id 120. Valid+invalid with `source=2` gives id 120 for the whole request. [observed] | Up to 60 indicators per call [documented, 898581]. |
| `source` | numeric source id | none | `9999` gives 200 + **two** id-120 messages. [observed] | Not needed for single-indicator calls. |
| language prefix | `en`, `es`, `fr`, `ar`, `zh` (+ some local) [documented, 898581] | `en` | `xx` gives 200 + id 150 "Language is not yet supported in the API". [observed] | Docs list 150 with response code 400; live is 200. |
| `incomeLevel` (country filter) | income-level codes | none | `ZZZ` gives 200 + id 120. [observed] | Only matters if we filter countries by income. |
| unknown params | any | n/a | `foo=bar` is silently ignored and the body is byte-identical. It does create a new CDN cache key (MISS). [observed] | A typo in a param name will **not** raise an error. |
| trailing `/` | n/a | n/a | Accepted, body byte-identical. [observed] | |
| path/URL length | ≤ 1,500 chars between slashes, ≤ 4,000 chars per URL [documented, 898581] | n/a | 15-code list (59 chars) works. 299-char segment works (slow, see Failure modes). **1,599-char segment gives 400 text/html "Bad Request - Invalid URL"**. [observed] | Our 9-country list is 35 characters, far below any limit. |

## Response envelope & field dictionary

### Shapes the loader must recognize

| # | shape | when | HTTP | Content-Type (observed) | sample |
|---|---|---|---|---|---|
| A | Success: `[ {meta}, [ {row}, ... ] ]` | valid request with data | 200 | `application/json;charset=utf-8` **or** `application/json` | `baseline_valid_bra_pop_2018_2024.json` |
| B | No data: `[ {"page":0,"pages":0,"per_page":0,"total":0,"sourceid":null,"lastupdated":null}, null ]` | valid but empty (e.g. a single out-of-range year `date=2030` or `date=1800`), **or the ISO2 code `XX`** (other unknown ISO2 codes such as `QZ` give id 120) | 200 | `application/json;charset=utf-8` | `future_year_2030.json`, `verify_date_single_year_1800_no_data.json`, `invalid_iso2_country_xx_data_empty_envelope.json` |
| C | Past last page: `[ {"page":999,"pages":1,...,"total":7}, [] ]` | `page > pages` | 200 | json | `page_beyond_last_page_999.json` |
| D | **API error (JSON)**: `[ {"message":[ {"id":"120","key":"Invalid value","value":"..."} ]} ]` | any invalid parameter value | **200** | json | `invalid_country_xxx_data.json`, `archived_indicator_doing_business.json`, `invalid_source_9999.json` |
| E | **API error (XML)**: `<wb:error xmlns:wb="http://www.worldbank.org"><wb:message id="111" key="Invalid format">...</wb:message></wb:error>` | bad `format`, or any error when `format` is missing | **200** | `text/xml` | `bad_format_value_yaml.xml`, `invalid_country_xxx_xml_error.xml` |
| F | Gateway error (JSON **object**): `{ "statusCode": 404, "message": "Resource not found" }` | wrong API version path | 404 | `application/json` | `invalid_api_version_v9.json` |
| G | WCF HTML "Request Error" (UTF-8 BOM + XHTML) | `per_page` > 32767 (reproduced); reversed date range (seen once, **not reproducible**: it returned 200 on re-check) | 400 | `text/html` | `reversed_date_range_2024_2018.html`, `per_page_32768.html` |
| H | WCF HTML "Endpoint not found." (BOM + XHTML) | unknown path under /v2 | 404 | `text/html; charset=UTF-8` | `unknown_endpoint_path.html` |
| I | HTTP.sys HTML "Bad Request - Invalid URL" | path segment too long | 400 | `text/html; charset=us-ascii` | `path_segment_over_1500_chars.html` |
| J | Redirect stubs | `http://` (302 Cloudflare HTML) and unversioned path (301 plain text, no Content-Type) | 302 / 301 | `text/html; charset=UTF-8` / none | `http_scheme_no_tls.html`, `retired_v1_endpoint_no_version.txt` |
| K | Success (XML) | `format` omitted | 200 | `text/xml` | `missing_format_param_default_xml.xml` |

### Field dictionary (error-relevant paths)

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `$` (top level) | array (A–D) **or** object (F) | `[...]` / `{...}` | never null | — | Check the top-level type first. An object means a gateway error. |
| `$.length` | — | 2 (A/B/C), 1 (D) | — | — | 1-element array whose element has a `message` key = API error. |
| `$[0].message` | array of objects | `[{"id":"120",...}]` | can hold >1 entry (`invalid_source_9999.json` has 2) | — | **Discriminator for API errors.** |
| `$[0].message[*].id` | **string** (quoted digits) | `"120"`, `"150"`, `"175"` | no | `api_error_id` | Quoted string, not a number. Cast to int for lookups. |
| `$[0].message[*].key` | string | `"Invalid value"`, `"Invalid format"` | no | `api_error_key` | Id 175 uses key `"Invalid format"` even though it means "indicator not found" (misleading). |
| `$[0].message[*].value` | string | `"The provided parameter value is not valid"` | no | `api_error_message` | Does **not** name the offending parameter. |
| `$.statusCode` (shape F) | number | `404` | — | `gateway_status` | Only on the version-path error. |
| `$.message` (shape F) | string | `"Resource not found"` | — | `gateway_message` | Note: a string here, while in shape D `message` is an array. |
| `$[0].page` | number | `1`, `999`, `0` | 0 in shape B | `meta_page` | Echoes the requested page even past the end (shape C). |
| `$[0].pages` | number | `1`, `2`, `0` | 0 in shape B | `meta_pages` | |
| `$[0].per_page` | number on the data endpoint; **quoted string** on `/country` and `/indicator` metadata | `50`, `1000`, `32767`, `0`; metadata: `"100"`, `"50"` | 0 in shape B | `meta_per_page` | Cast with `int()` before comparing. This area's country call returned `"per_page":"100"` [observed]; the README's canonical R2 sends `per_page=50` and gets `"50"`. |
| `$[0].total` | number | `7`, `63`, `105`, `0` | 0 in shape B | `meta_total` | Use it to reconcile rows received across pages. |
| `$[0].sourceid` | string **or null** | `"2"` | **null** in shape B **and** on multi-indicator calls; **key absent** on `/country` and `/indicator` metadata responses | `meta_source_id` | Type changes with call style. |
| `$[0].lastupdated` | string `YYYY-MM-DD` or null | `"2026-10-08"` | null in shape B; **key absent** on `/country` and `/indicator` metadata responses | `meta_last_updated` | Data-version signal. Prefer it over HTTP `Last-Modified`. |
| `$[1]` | array, **empty array**, or **null** | `[...]`, `[]`, `null` | `[]` past the last page, `null` when no data | — | Treat `null` the same as `[]`, and keep the reason. |
| XML `/wb:error/wb:message/@id` | attribute string | `120`, `111` | — | `api_error_id` | XML errors only appear if `format=json` is missing or invalid. |
| XML `/wb:error/wb:message/@key` | attribute string | `Invalid value` | — | `api_error_key` | |
| XML `/wb:error/wb:message/text()` | text | `The provided parameter value is not valid` | — | `api_error_message` | |

### Error codes: documented vs observed

| id | documented response code / message [documented, 898620] | observed today | agree? |
|---|---|---|---|
| 105 | 503, "Service currently unavailable" | not seen | unknown |
| 110 | 404, "API Version "XXX" not found." | `/v9/...` gave **404 JSON object** `{statusCode, message:"Resource not found"}`, no id 110 | **No**: different body |
| 111 | 404, "Format "XXX" not found." | **200** `text/xml`, id 111, key "Invalid format", "The requested response format was not found." | **No**: HTTP 200 |
| 112 | 404, "Method "XXX" not found." | not seen | unknown |
| 115 | 404, "Missing required parameter" | not seen | unknown |
| 120 | 404, "Parameter "XXX" has an invalid value." | **200**, key "Invalid value", "The provided parameter value is not valid" (does not name the parameter) | **No**: HTTP 200, different text |
| 140 | 400, "Endpoint "XXX" not found." | **404 text/html** WCF page "Endpoint not found.", no id | **No** |
| 150 | 400, "Language with ISO2 code ... is not yet supported" | **200**, key "Language is not yet supported in the API" | **No**: HTTP 200 |
| 160 | 400, filtering on an indicator value requires a date range | not tested (we don't filter by value) | unknown |
| 175 | **not documented** | **200**, key "Invalid format", "The indicator was not found. It may have been deleted or archived." | Undocumented |
| 199 | 500, "Unexpected error" | not seen | unknown |
| (v1) | docs: old endpoints return "Resource not found" [documented, 889392] | unversioned path gave **301** to `/v2/...` with the text "The v1 was discontinued on June 19, 2020..." | **No** |

## Behavior & gotchas

**Error signaling**
- [observed] **HTTP 200 is not success.** 22 error probes returned HTTP 200 with a JSON error payload (shape D), and 2 more returned HTTP 200 with an XML error (shape E). Examples include `.../country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` and `per_page=0`. The excerpt was `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]`.
- [documented, 898620] The error-code table lists HTTP response codes 400/404/500/503 for these errors. [observed] Live, the parameter-level errors (111, 120, 150, 175) all came back with **HTTP 200**. Design for the observed behavior.
- [observed] Error id 120 is generic. Invalid country, invalid indicator, bad date, bad `per_page`, bad `page`, bad `source`, bad `incomeLevel` and an empty list item all return the **same 102 bytes** (sha256 `f230e5b6…`). The body cannot tell you which input was wrong.
- [observed] **No partial success.** `country/BRA;XXX/...` and `country/BRA;XXX` (metadata) both fail completely with id 120. `indicator/SP.POP.TOTL;NOT.A.CODE?source=2` also fails completely. One bad code poisons a batched request.
- [observed] **The ISO2 code `XX` does not error on the data endpoint.** `country/XX/indicator/SP.POP.TOTL?...` gave HTTP 200 with the shape-B no-data envelope (`[{"page":0,...,"total":0,"sourceid":null,"lastupdated":null},null]`), and did so again on re-check. The same `XX` on `/country/XX` gives error 120. Other unknown ISO2 codes (`QZ`, `ZZ`) return error 120 on the data endpoint, so this is a quirk of `XX`, not of ISO2 codes in general. Still, "no data" can hide a configuration error. Use ISO3 and validate codes up front.
- [observed] Archived indicators return an **undocumented id 175** with the misleading key `"Invalid format"`: `"The indicator was not found. It may have been deleted or archived."` (from `IC.BUS.EASE.XQ`). This is the failure we would hit if one of our four indicators were retired.
- [observed] When the format is missing or invalid, errors come back as XML (`text/xml`, `<wb:error>`), even though everything else is JSON. Always send `format=json`. The `Accept` header is ignored.
- [observed] Request-level faults bypass the wb envelope entirely and return HTML. Examples: reversed date range and `per_page` ≥ 32768 (400 WCF "Request Error", BOM-prefixed), unknown path (404 WCF "Endpoint not found.") and an overlong segment (400 HTTP.sys "Bad Request - Invalid URL"). A wrong version returns a JSON *object*, not an array. The `per_page` ≥ 32768, overlong-segment, unknown-path and wrong-version cases reproduced on re-check. The **reversed-date 400 did not**: the same URL returned 200 with data 16 minutes later. So a WCF "Request Error" is not always deterministic. They are also slow: the 400 WCF pages took 4.1–10.6 s.

**Silent "success" that is wrong**
- [observed] **The date filter can be silently ignored.** `date=1800:1801` returned HTTP 200 with the full series (total 66, years 2025…1960) instead of an empty result. The verification pass reproduced this and found the pattern: any range lying *entirely* outside the available years is ignored (`1900:1959` and `2030:2031` also returned total 66). Partly-overlapping ranges are clipped correctly, and a single out-of-range year returns the no-data envelope. The loader must check every row's `date` against the requested range, and drop or flag rows outside it.
- [observed] `page` beyond `pages` returns HTTP 200 with `"page":999,"pages":1` and `[]` rows. It is not an error, so the paginator must stop at `meta.pages`.
- [observed] Unknown query params are ignored (`foo=bar` gave a byte-identical body), so a misspelled param such as `perpage=` silently falls back to defaults. `per_page` defaults to 50 [documented, 898581; observed `"per_page":50`].
- [observed] `meta.sourceid` is the string `"2"` on single-indicator calls but `null` on multi-indicator calls (`source=2`) and in the no-data envelope.

**Transport, latency, caching**
- [observed] Latency (`time_total`) over 57 HTTP-200 responses: min 0.06 s, median 0.19 s, p90 0.33 s, max 11.2 s. Cache misses sometimes took 4–11 s: 4.1 s (reversed date), 4.8 s (15-country list), 4.1 s (gzip GDP call), 6.0–6.4 s (large per_page), 10.6–11.2 s (page 999, multi-indicator). The real pipeline calls (9 countries × 1 indicator, and 9-country metadata) took 0.17–0.45 s on a miss. The verification pass (01:27–01:31Z) saw **two small cold misses take 23.7 s (`BRA`, `date=2024:2026`) and 21.3 s (`BRA;BRA`, `date=2023:2024`)**. So 20 s+ origin times are not rare one-offs, even for 2-row responses.
- [observed] **One client timeout:** `country/BRA;…(×75)/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` hit curl's 30 s limit with 0 bytes. The origin finished anyway: `last-modified: 01:15:12 GMT`, about 32 s after the request started. **The retry, 2 minutes later, was a Cloudflare HIT in 0.12 s** (`cf-cache-status: HIT`, `age: 90`). Retrying after a timeout is effective because the CDN keeps the result the origin eventually produced.
- [observed] No intermittent 5xx, 429, Cloudflare 52x or connection errors in 67 requests.
- [observed] Infrastructure headers: `server: cloudflare`, `cf-ray: …-ORD`, `cf-cache-status` (MISS/HIT/EXPIRED/BYPASS/DYNAMIC), `x-powered-by: ASP.NET`, and `request-context: appId=cid-v1:…` (an Azure Application Insights correlation header). The error pages are WCF-style. Cloudflare sets `__cf_bm`/`_cfuvid` cookies, which the loader does not need. There are **no** `Via`, `X-Cache`, `ETag`, `RateLimit-*`, `X-RateLimit-*` or `Retry-After` headers on any response (also none in the 47 verification responses). UNVERIFIED: Requests resolved to two Cloudflare IPs (104.18.42.231, 172.64.145.25). There is no evidence-log entry for this, and it was not re-checked.
- [observed] **Two cache layers, both caching errors.** JSON responses (success *and* shape-D errors) carry `cache-control: public, max-age=86400`. The origin computes `max-age = 86400 − (now − last-modified)`. For example, the baseline returned `max-age=64703` with `last-modified: Tue, 13 Oct 2026 19:10:06 GMT` at 01:11:43Z the next day: 86400 − 21697 = 64703. That means an origin-side cache entry with a 24 h TTL. Cloudflare then caches on top of it. `/country/XX` was a HIT with `age: 64145` while `max-age=60849`, so the edge served it beyond max-age, from an entry generated about 25 h earlier. **Data can be stale by ~a day or more after a World Bank update.** Use `meta.lastupdated` as the data-version signal.
- [observed] 400 HTML errors are `cf-cache-status: BYPASS` (never cached, so every repeat hits the origin). The 404 version error is `DYNAMIC`. The 404 unknown-path page is cacheable (`max-age=14400`).
- [observed] Identical URLs return **byte-identical bodies** while cached. All 10 burst calls had sha256 `0e4577f4…`. Lower-case codes, a trailing slash, `%3A` encoding and an unknown param all produced the same bytes as the baseline (sha256 `24c384c0…`). The body hash is therefore a stable idempotency/dedup key for a given data version.
- [observed] `If-Modified-Since` gets **304** from Cloudflare. `Last-Modified` reflects when the cache entry was generated, not when the data changed, so it is not useful for incremental loads.
- [observed] `Accept-Encoding: gzip` is honored (`content-encoding: gzip`). Compute the body hash on the **decoded** bytes (`requests`' `.content` is already decoded).
- [observed] `Content-Type` varies for equivalent JSON: `application/json;charset=utf-8` on most calls, plain `application/json` on others (trailing slash, jsonp, unknown param, the 9-country burst URL). Match on the media type `application/json`, never on the exact string.
- [observed] JSON bodies have no BOM and no trailing newline. All 4 WCF HTML error pages start with a UTF-8 BOM (`EF BB BF`).
- [observed] `http://` returns 302 to `https://`, and the unversioned path returns 301 to `/v2/...`. Disable redirects or assert the final URL, so a misconfigured base URL fails loudly.
- [documented, 902064] "The World Bank API is not guaranteed to have 100% uptime". The docs recommend caching and defensive handling of downtime.
- [documented] **No rate limits are documented** on 898581, 898620, 902064 or 1886674. [observed] A sequential burst of 10 calls (about 1 s total) showed no throttling and no rate-limit headers. The real threshold is unknown, so stay polite: sequential calls, or concurrency ≤ 2.
- [documented, 898581] Limits: 60 indicators per request, 1,500 chars between slashes, 4,000 chars per URL, no sorting. [observed] A 1,599-char segment is rejected (400) and a 299-char segment is accepted. The 4,000-char URL limit was not tested separately.
- [observed] Row order in a multi-country response is by country code ascending, then `date` descending (15-country sample: ARG, AUS, BRA, CAN, CHN, DEU, FRA, …). [documented, 898581] Sorting is not supported, so do not rely on the order.

## Failure modes

### Classification table

| # | failure | how it shows up (observed unless noted) | class | loader action |
|---|---|---|---|---|
| F1 | Connect error / DNS / TLS / connection reset | not observed | transient | retry with backoff |
| F2 | Read timeout | observed once: 30 s, 0 bytes (`path_segment_299_chars_client_timeout.meta.json`). Origin finished at ~32 s. | transient | retry; the CDN usually has the answer on the next attempt |
| F3 | HTTP 5xx / Cloudflare 520–524 / 429 | not observed. Docs: 105 → 503, 199 → 500 [documented, 898620] | transient | retry with backoff, honor `Retry-After` if present |
| F4 | HTTP 200 + error payload (shape D/E), ids 111/120/150/175 | observed many times | **fatal: config / request bug** | fail fast, no retry. Log the URL and message. |
| F5 | HTTP 200 + error id 105 or 199 | not observed (documented as 503/500) | transient | retry (defensive) |
| F6 | HTTP 400 HTML (WCF Request Error, HTTP.sys Invalid URL) | observed (reversed date, per_page ≥ 32768, 1,599-char segment). `per_page` ≥ 32768 and the 1,599-char segment reproduced on re-check. **The reversed-date 400 did not**: it returned 200 with data 16 min later. | **fatal** for HTTP.sys "Invalid URL". For a WCF "Request Error", **one bounded retry, then fatal**. | It is never cached (BYPASS), so a retry reaches the origin. Our own requests should never trigger it (validate `start <= end` locally, use `per_page` ≤ 1000). |
| F7 | HTTP 404 (HTML endpoint page or JSON gateway object) | observed | **fatal** | fail fast: wrong base URL or path |
| F8 | 3xx redirect | observed (http→https 302, v1→v2 301) | **fatal** (config) | do not follow silently. Fix the base URL. |
| F9 | HTTP 200 non-JSON body (XML/HTML) | observed when `format` is missing or invalid | **fatal** | assert the `application/json` media type |
| F10 | HTTP 200 no-data envelope (shape B) | observed (`date=2030`, `date=1800`, ISO2 `XX`) | **data outcome**, unless the codes are invalid | record "no data" for that request. Raise a warning if a pre-validated country/indicator returns total 0 for the full range. |
| F11 | HTTP 200 but filter ignored / rows outside request | observed (`date=1800:1801`, `1900:1959` and `2030:2031` each returned all 66 rows; it happens when the whole range is outside the available years) | **data-quality → fatal** | ~~drop out-of-range rows and count them in the run log~~ **Superseded by README Contradictions #15: fail the response** (retry once with a cache-buster, then fail). With a fixed 2018:2024 request, an out-of-range row means an ignored filter or a substituted body |
| F12 | Paging inconsistencies (`page > pages`, `total` changes mid-run, rows ≠ `total`) | page>pages observed; others are defensive | transient-ish | restart pagination once, then fail |
| F13 | Truncated / unparseable JSON with HTTP 200 | not observed | transient | retry |
| F14 | Stale data (CDN + origin cache ≥ 24 h) | observed via headers | expected | record `meta.lastupdated`, `cf-cache-status`, `age` and `last-modified` for lineage |

### Recommended resilience policy

**Timeouts (requests/httpx):** `connect=10 s`, `read=60 s`. Cold misses took up to 11.2 s in the first pass and 21–24 s in the verification pass, and the slowest origin computation observed was about 32 s, so 30 s is too tight. Add an overall per-request deadline of about 90 s. *Superseded by the README contract (Contradictions #1): read timeout 90 s.* Other areas saw 39.5 s (area 02 V9) and the critic saw **45.2 s** (area 01 C1) [observed by critic], which is too close to 60 s.

**Retries:** up to **5 attempts** in total (1 + 4 retries). Use exponential backoff with full jitter: `sleep = random.uniform(0, min(30, 1 * 2**(attempt-1)))` → caps of 1, 2, 4 and 8 s. If a `Retry-After` header is present (none observed), honor it, capped at 120 s. Retry on:
- exceptions: `ConnectionError`, `ConnectTimeout`, `ReadTimeout`, `ChunkedEncodingError`/incomplete read, and JSON decode errors on a `200 application/json` response (F13);
- HTTP statuses: 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524 and 530 (Cloudflare origin errors);
- API error ids **105** and **199** in a shape-D body (documented as transient: service unavailable / unexpected error).
- **[observed by critic] How to retry an HTTP-200 body.** Shape-D errors and successful bodies are cached by Cloudflare for the exact URL (`max-age=86400`), so re-sending the same URL returns the same bytes (area 02 C3: a wrong body came back as a `HIT`; `/country/XX` error served at `age 64145`). A retry of anything that arrived with HTTP 200 (ids 105/199, a validation failure, a `lastupdated` mismatch) must add a fresh cache-buster token per attempt, e.g. `&cb=<run_id>-<attempt>`. It always gets past Cloudflare and usually past the origin caches (area 02 C4), but not always (area 02 C7). Timeouts need no token: the timeout case was rescued by the edge copy (row 47). Whether Cloudflare would cache a 5xx body from this origin is **unknown** (never observed), so a token on 5xx retries is a harmless precaution.

**Fatal, no retry (fail fast, raise `WorldBankRequestError` with URL, status, error ids and body excerpt):**
- HTTP 400, 401, 403, 404, 405, and any 3xx (run the client with `allow_redirects=False` and assert the host/path prefix `https://api.worldbank.org/v2/`). One exception: a 400 whose HTML body is the WCF "Request Error" page gets **one** retry before failing, because the reversed-date 400 was not reproducible;
- shape-D/E error ids **110, 111, 112, 115, 120, 140, 150, 160, 175**, and any *unknown* id (conservative);
- HTTP 200 with a non-JSON media type (XML/HTML);
- a gateway object (top level is a JSON object, shape F).
- On a batched country request that fails with id 120, the error does not say which code is bad. Run the `/country/{code}` pre-flight per code to name it in the exception. The config is wrong, so the run must fail; do not load a partial result.

**Detecting an error payload served with HTTP 200 (classify before any row parsing):**

> *Critic note:* the README §2 `classify()` supersedes this one (README Contradictions #7). The version below treats every top-level JSON object as a gateway error, which would reject the R7 Metadata API success body (an object with a `source` key).

```python
def classify(resp) -> tuple[str, object]:
    ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if resp.status_code != 200:
        return ("http_error", resp.status_code)
    if ctype != "application/json":                # XML (format dropped / bad), HTML, text
        return ("non_json", ctype)
    body = resp.json()                             # ValueError -> transient (retry)
    if isinstance(body, dict):                     # {"statusCode":..., "message":...}
        return ("gateway_error", body)
    if (isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict)
            and "message" in body[0]):             # [{"message":[{"id":"120",...}]}]
        ids = [int(m["id"]) for m in body[0]["message"]]
        return ("api_error", ids)                  # 105/199 -> retry; others -> fatal
    if not (isinstance(body, list) and len(body) == 2 and isinstance(body[0], dict)):
        return ("bad_envelope", body)              # fatal + save raw body
    meta, rows = body
    if meta.get("total") == 0 and rows is None:
        return ("no_data", meta)                   # shape B: legitimate empty result
    return ("ok", (meta, rows or []))
```

**Response validation (per page, and per request after all pages):**
1. Envelope: `len == 2`. On the **observation (data) endpoint**, `meta` has the keys `page, pages, per_page, total, sourceid, lastupdated` with ints for the first four. On the **`/country` and `/indicator` metadata endpoints**, `meta` has only `page, pages, per_page, total`, and `per_page` is a quoted string (`"100"`). Do not require `sourceid`/`lastupdated` there, and cast `per_page` with `int()`. [observed] `rows` is a list (or null, for no data).
2. `meta.page == requested page`. `int(meta.per_page) == requested per_page`. `len(rows) <= per_page`. Rows are non-empty when `page <= pages` and `total > 0`.
3. Across pages: `meta.total` and `meta.lastupdated` are the same on every page. If they change mid-run, restart pagination once, then fail. `sum(len(rows)) == meta.total`. No duplicate `(countryiso3code, indicator.id, date)`.
4. Per row: required keys `indicator.id`, `countryiso3code`, `date` and `value` are present (`value` may be `null`, which means missing; never coerce it to 0). `countryiso3code` is in the requested set. `indicator.id` equals the requested code (case-insensitively). `int(date)` is within the requested `[start, end]` (F11). ~~Out-of-range rows are dropped and counted.~~ An out-of-range row fails the response (README Contradictions #15).
5. Completeness (warning, not error): for the expected countries × years grid (9 × 7 = 63 per indicator), report missing cells explicitly so a "missing" status is recorded for each one.

**What to log per HTTP attempt (one structured record, e.g. a `_raw_requests` table plus an on-disk raw body):**
`run_id`, `request_id` (a stable hash of the logical request: endpoint + sorted params), `attempt`, `method`, `url` (full, as sent), `fetched_at_utc` (start), `duration_ms`, `http_status`, `content_type`, `content_encoding`, `bytes` (decoded length), `body_sha256` (of the decoded bytes; the raw file is stored as `raw/<sha256>.json`), `outcome` (`ok` | `no_data` | `api_error:<ids>` | `http_error` | `timeout` | `non_json` | `bad_envelope`), `retry_reason`, `sleep_before_ms`, `meta_page`, `meta_pages`, `meta_total`, `meta_lastupdated`, `rows_received`, `rows_dropped_out_of_range`, and the response headers `cf-ray`, `cf-cache-status`, `age`, `last-modified` and `cache-control`. Each loaded observation row carries `source_url`, `source_page` and `response_sha256` (plus `fetched_at_utc`), so any value can be traced back to the exact stored response.

**Idempotency notes:** identical URLs return byte-identical bodies while cached, so `body_sha256` can dedupe raw files across re-runs. Use `meta.lastupdated` (not HTTP `Last-Modified`) to version the data. Do not add cache-busting params *on first attempts*: unknown params are accepted and change the CDN key, but they add origin load for no benefit given the origin's own 24 h cache. *Critic qualification:* the body hash is **not** a "data unchanged" signal, because equivalent data can arrive with or without `footnote` keys (README Contradictions #13; area 02 C2), and cache-busters are needed on *retries* of HTTP-200 bodies (see Retries above).

## Calls our pipeline will make

Every call is sequential, ≥ 0.5–1 s apart, with `format=json` always set. ISO3 codes are upper case, joined with `;` (35 chars).

> **Canonical URLs live in the README call plan (R0–R11).** The README's country call uses `per_page=50` (not 100; Contradictions #8) and the observation calls add `source=2` (a no-op; Contradictions #5). The table below records what this area measured.

| # | purpose | exact URL | expected pages | expected rows |
|---|---|---|---|---|
| 1 | Country dimension + pre-flight validation of the 9 codes | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=100` | 1 | 9. Assert exactly the 9 requested ids came back. On id 120, probe `/v2/country/{code}?format=json` per code to name the bad one, then fail. |
| 2 (optional) | Indicator pre-flight. It detects an unknown code (id 120). It does **not** detect an archived one: the archived `IC.BUS.EASE.XQ` returns 200 metadata with `source.id` `"57"`. To catch that, also assert `source.id == "2"`, the source our data calls report in `meta.sourceid`; id 175 still surfaces on the data call itself. | `https://api.worldbank.org/v2/indicator/{SP.POP.TOTL\|NY.GDP.MKTP.CD\|NY.GDP.PCAP.CD\|SP.DYN.LE00.IN}?format=json` | 1 each (4 calls) | 1 each |
| 3–6 | Observations, one call per indicator | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/{INDICATOR}?format=json&date=2018:2024&per_page=1000` | 1 (but still loop to `meta.pages`) | 63 each (9 × 7); `value` may be null |

- Normal run: **5 requests** (9 with the indicator pre-flight). Worst case with retries: 5 attempts × 9 = 45 requests, still very light.
- Observed: the 4 observation calls for these exact countries and years returned `total: 63`, `pages: 1` at `per_page=100` (SP.POP.TOTL, NY.GDP.PCAP.CD and SP.DYN.LE00.IN measured here). Cold latency was 0.17–0.27 s. With `Accept-Encoding: gzip`, NY.GDP.MKTP.CD took 4.1 s on a cold miss. Verification re-ran all 4 calls with the exact `per_page=1000` URLs above. Each returned `total: 63`, `pages: 1`, `per_page: 1000` (number), `sourceid: "2"` and `lastupdated: "2026-10-08"`, with rows ordered BRA 2024, BRA 2023, … (all cf HITs). The country call returned exactly BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF with `"per_page":"100"` as a string.
- Do **not** batch the 4 indicators into one call. That needs `source=2`, makes `meta.sourceid` null, took 11 s once, and one bad indicator would fail all four.

### Fixture mapping for offline tests

| test | fixture(s) |
|---|---|
| parses success envelope | `baseline_valid_bra_pop_2018_2024.json`, `burst_01_nine_countries_pop.json`, `long_country_list_15_codes.json` |
| detects 200 + error payload → fatal | `invalid_country_xxx_data.json`, `invalid_indicator_data.json`, `mixed_valid_invalid_countries_bra_xxx.json`, `per_page_zero.json`, `invalid_date_abc.json` |
| multiple messages in one error | `invalid_source_9999.json` |
| archived indicator (id 175) → fatal with a clear message | `archived_indicator_doing_business.json` |
| no-data envelope (`[meta, null]`) → "no data" outcome, not a crash | `future_year_2030.json`, `invalid_iso2_country_xx_data_empty_envelope.json` |
| page past end → stop, no error | `page_beyond_last_page_999.json` |
| out-of-range rows → response rejected, run fails (README Contradictions #15) | `date_range_1800_1801_filter_ignored.json`, `verify_date_range_1900_1959_filter_ignored.json`, `verify_date_range_2030_2031_filter_ignored.json` (request 2018:2024 in the test) |
| wrong body on HTTP 200 → retry with a fresh cache-buster; exact-URL retry returns the same bytes | `../observations/critic__inner_cache_B_no_footnote__MEX_ZAF__NY.GDP.PCAP.CD.json` (substituted body), `…_C_no_footnote_retry_same_url…` (same-URL retry, same bytes), `…_D_no_footnote_cachebuster…` (recovered). B's substitution is benign (only `footnote` keys differ) and passes validation, so a test must pair these with a failing body such as `anomaly__wrong_body__*.json` to exercise the retry |
| reversed range normalized (200) | `verify_reversed_date_range_2024_2018_now_200.json`, `verify_reversed_date_range_2022_2019_usa_200.json` |
| metadata meta block (`per_page` string, no `sourceid`) parses | `verify_country_metadata_nine_codes.json`, `verify_archived_indicator_metadata_returns_200.json` |
| unknown ISO2 other than `XX` → fatal id 120 | `verify_invalid_iso2_country_qz_data_error_120.json` |
| non-JSON 200 (XML) → fatal | `missing_format_param_default_xml.xml`, `bad_format_value_yaml.xml`, `invalid_country_xxx_xml_error.xml` |
| HTML 400/404 → fatal, BOM tolerated | `reversed_date_range_2024_2018.html`, `per_page_32768.html`, `unknown_endpoint_path.html`, `path_segment_over_1500_chars.html` |
| gateway JSON object → fatal | `invalid_api_version_v9.json` |
| redirect → fatal | `http_scheme_no_tls.html`, `retired_v1_endpoint_no_version.txt` (+ `.headers.txt`) |
| timeout then success on retry | simulate with `path_segment_299_chars_client_timeout.meta.json` → `path_segment_299_chars_retry_cdn_hit.json` |
| Content-Type variants accepted | `trailing_slash_data.headers.txt` (`application/json`) vs `baseline_valid_bra_pop_2018_2024.headers.txt` (`application/json;charset=utf-8`) |

Each body has a `.meta.json` sidecar (url, http_status, content_type, fetched_at_utc, notes) and a `.headers.txt` with the raw response headers (Cloudflare cookie values redacted).

## Evidence log

All calls were made with `curl -q -sS -m 30` (one retry used `-m 90`) on 2026-10-14 UTC. Times are `time_total`. "error payload" means shape D. Burst calls 2–10 are collapsed into one row. Rows 33, 34 and 47 abbreviate a long, repetitive path; the full URLs are in the `.meta.json` files.

| # | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | Positive control: `[meta, rows]`, total 7; MISS but origin-cached (max-age 64703, last-modified 6h earlier) (0.13 s) | `samples/errors/baseline_valid_bra_pop_2018_2024.json` |
| 2 | `https://api.worldbank.org/v2/country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | **200 + error payload** id `"120"` Invalid value (0.23 s) | `samples/errors/invalid_country_xxx_data.json` |
| 3 | `https://api.worldbank.org/v2/country/XXX?format=json` | 200 | **200 + error payload** id 120; cf EXPIRED (0.22 s) | `samples/errors/invalid_country_xxx_metadata.json` |
| 4 | `https://api.worldbank.org/v2/country/BRA/indicator/NOT.A.CODE?format=json&date=2018:2024` | 200 | **200 + error payload** id 120 (identical bytes to invalid-country case) (0.20 s) | `samples/errors/invalid_indicator_data.json` |
| 5 | `https://api.worldbank.org/v2/indicator/NOT.A.CODE?format=json` | 200 | **200 + error payload** id 120 (0.19 s) | `samples/errors/invalid_indicator_metadata.json` |
| 6 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=abc` | 200 | **200 + error payload** id 120 (0.20 s) | `samples/errors/invalid_date_abc.json` |
| 7 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024:2018` | 400 | **400 text/html** WCF "Request Error" page, BOM-prefixed, 4.1 s, cf BYPASS (4.11 s). **Not reproducible**: at 01:28:06Z the same URL returned 200 with 7 rows (see Verification). | `samples/errors/reversed_date_range_2024_2018.html` |
| 8 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=0` | 200 | **200 + error payload** id 120 (0.17 s) | `samples/errors/per_page_zero.json` |
| 9 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=-1` | 200 | **200 + error payload** id 120 (0.16 s) | `samples/errors/per_page_negative_one.json` |
| 10 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=abc` | 200 | **200 + error payload** id 120 (0.20 s) | `samples/errors/per_page_abc.json` |
| 11 | `https://api.worldbank.org/v2/notanendpoint?format=json` | 404 | **404 text/html** "Endpoint not found." (docs: code 140 / 400) (0.16 s) | `samples/errors/unknown_endpoint_path.html` |
| 12 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?date=2023:2024` | 200 | Default format is XML (`text/xml`, `<wb:data ...>`) (0.30 s) | `samples/errors/missing_format_param_default_xml.xml` |
| 13 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=yaml&date=2018:2024` | 200 | **200 + XML error** id 111 "Invalid format" (docs: 404) (0.22 s) | `samples/errors/bad_format_value_yaml.xml` |
| 14 | `https://api.worldbank.org/v2/country/XXX/indicator/SP.POP.TOTL?date=2018:2024` | 200 | **200 + XML error** `<wb:error><wb:message id="120" key="Invalid value">` (0.18 s) | `samples/errors/invalid_country_xxx_xml_error.xml` |
| 15 | `https://api.worldbank.org/v2/country/BRA;XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | **200 + error payload** id 120 - whole batch fails, no BRA rows (0.19 s) | `samples/errors/mixed_valid_invalid_countries_bra_xxx.json` |
| 16 | `https://api.worldbank.org/v2/country/bra/indicator/sp.pop.totl?format=json&date=2018:2024` | 200 | Accepted; byte-identical to baseline (0.31 s) | `samples/errors/lowercase_codes_bra_sp_pop_totl.json` |
| 17 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL/?format=json&date=2018:2024` | 200 | Accepted; byte-identical; Content-Type `application/json` (no charset) (0.17 s) | `samples/errors/trailing_slash_data.json` |
| 18 | `http://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 302 | **302** (Cloudflare) -> same URL on https (0.03 s) | `samples/errors/http_scheme_no_tls.html` |
| 19 | HEAD `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | HEAD 200, headers only, cf HIT age 70, no rate-limit headers (0.11 s) | `samples/errors/head_request_valid.meta.json` (+ headers) |
| 20 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&page=999` | 200 | 200, meta page 999 / pages 1, rows `[]`; 11.2 s (11.24 s) | `samples/errors/page_beyond_last_page_999.json` |
| 21 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=1800:1801` | 200 | 200, **date filter ignored**: full 1960-2025 series (total 66) (0.33 s) | `samples/errors/date_range_1800_1801_filter_ignored.json` |
| 22 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2030` | 200 | 200 no-data envelope: total 0, sourceid null, rows `null` (0.20 s) | `samples/errors/future_year_2030.json` |
| 23 | `https://api.worldbank.org/v2/xx/country/BRA?format=json` | 200 | **200 + error payload** id 150 (docs: 400) (0.16 s) | `samples/errors/unsupported_language_prefix_xx.json` |
| 24 | `https://api.worldbank.org/v9/country/BRA?format=json` | 404 | **404 JSON object** `{statusCode:404, message:"Resource not found"}` (0.18 s) | `samples/errors/invalid_api_version_v9.json` |
| 25 | `https://api.worldbank.org/countries/BRA?format=json` | 301 | **301** -> /v2/countries/BRA, text body "The v1 was discontinued..." (0.10 s) | `samples/errors/retired_v1_endpoint_no_version.txt` |
| 26 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2023:2024` | 200 | **200 + error payload** id 120 (multi-indicator needs `source=`) (0.15 s) | `samples/errors/multi_indicator_without_source.json` |
| 27 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL;NOT.A.CODE?source=2&format=json&date=2023:2024` | 200 | **200 + error payload** id 120 - whole request fails (0.24 s) | `samples/errors/multi_indicator_valid_plus_invalid_source2.json` |
| 28 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?source=9999&format=json&date=2023:2024` | 200 | **200 + error payload** with TWO message entries (id 120 x2) (0.28 s) | `samples/errors/invalid_source_9999.json` |
| 29 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?source=2&format=json&date=2023:2024` | 200 | 200, 4 rows; meta.sourceid `null`; 11.0 s (11.04 s) | `samples/errors/multi_indicator_valid_source2_control.json` |
| 30 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=JSON&date=2023:2024` | 200 | Accepted (JSON) (0.28 s) | `samples/errors/format_uppercase_JSON.json` |
| 31 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=jsonp&date=2023:2024` | 200 | Plain JSON returned; Content-Type without charset (0.17 s) | `samples/errors/format_jsonp_without_prefix.json` |
| 32 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF;CHN;FRA;ITA;AUS;KOR;ARG/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=200` | 200 | 200, 15 codes -> total 105, 1 page; 4.8 s; no URL issue (4.85 s) | `samples/errors/long_country_list_15_codes.json` |
| 33 | `https://api.worldbank.org/v2/country/BRA;BRA;…(BRA×400, 1,599 chars)/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | 400 | **400 text/html** "Bad Request - Invalid URL" (1,599-char segment) (0.15 s) | `samples/errors/path_segment_over_1500_chars.html` |
| 34 | `https://api.worldbank.org/v2/country/BRA;BRA;…(BRA×75, 299 chars)/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | none (timeout) | **Client timeout** at 30.0 s, 0 bytes (curl exit 28) (30.01 s) | `samples/errors/path_segment_299_chars_client_timeout.meta.json` (no body) |
| 35 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` | 200 | Burst call 1/10: MISS 0.17 s, 63 rows (0.17 s) | `samples/errors/burst_01_nine_countries_pop.json` |
| 36 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` (x9) | 200 (x9) | Burst calls 2-10: all HIT, 0.06-0.16 s, byte-identical, no rate-limit headers | — (headers logged only) |
| 37 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=100` | 200 | `Accept-Encoding: gzip` honored (`content-encoding: gzip`, 1,223 bytes); 4.1 s (4.08 s) | — (not saved; logged only) |
| 38 | `https://api.worldbank.org/v2/country/BRA;;USA/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | **200 + error payload** id 120 (`BRA;;USA`) (0.28 s) | `samples/errors/empty_segment_in_country_list.json` |
| 39 | `https://api.worldbank.org/v2/country/XX/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | 200 **no-data envelope** (not an error!) for unknown ISO2 `XX` (0.30 s) | `samples/errors/invalid_iso2_country_xx_data_empty_envelope.json` |
| 40 | `https://api.worldbank.org/v2/country/XX?format=json` | 200 | **200 + error payload** id 120; cf HIT age 64145 s (errors cached) (0.11 s) | `samples/errors/invalid_iso2_country_xx_metadata.json` |
| 41 | `https://api.worldbank.org/v2/country/QQQ/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | **200 + error payload** id 120 (0.19 s) | `samples/errors/invalid_iso3_country_qqq_data.json` |
| 42 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100000` | 400 | **400 text/html** WCF Request Error; 6.0 s (5.95 s) | `samples/errors/per_page_100000.html` |
| 43 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:abc` | 200 | **200 + error payload** id 120 (0.29 s) | `samples/errors/invalid_date_end_2018_abc.json` |
| 44 | `https://api.worldbank.org/v2/country?incomeLevel=ZZZ&format=json` | 200 | **200 + error payload** id 120 (0.19 s) | `samples/errors/invalid_income_level_filter_zzz.json` |
| 45 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024&per_page=32768` | 400 | **400 text/html** WCF Request Error; 10.6 s (10.63 s) | `samples/errors/per_page_32768.html` |
| 46 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024&per_page=32767` | 200 | 200 accepted (max per_page = 32767); 6.4 s (6.45 s) | `samples/errors/per_page_32767.json` |
| 47 | `https://api.worldbank.org/v2/country/BRA;BRA;…(BRA×75, 299 chars)/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | 200 | Retry: 200 in 0.12 s, cf **HIT age 90** - CDN cached the result after our client timed out (0.12 s) | `samples/errors/path_segment_299_chars_retry_cdn_hit.json` |
| 48 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018%3A2024` | 200 | `%3A` accepted; byte-identical to baseline (0.28 s) | `samples/errors/percent_encoded_colon_in_date.json` |
| 49 | `https://api.worldbank.org/v2/country/BRA%3BUSA/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | `%3B` accepted; 4 rows (0.27 s) | `samples/errors/percent_encoded_semicolon_in_country_list.json` |
| 50 | `https://api.worldbank.org/v2/country/BRA;XXX?format=json` | 200 | **200 + error payload** id 120 - whole request fails (0.09 s) | `samples/errors/mixed_valid_invalid_country_metadata_bra_xxx.json` |
| 51 | `https://api.worldbank.org/v2/country/BRA/indicator/IC.BUS.EASE.XQ?format=json&date=2018:2024` | 200 | **200 + error payload** id **175** (undocumented) "may have been deleted or archived" (0.21 s) | `samples/errors/archived_indicator_doing_business.json` |
| 52 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?date=2023:2024` | 200 | `Accept: application/json` ignored -> XML (cf HIT) (0.13 s) | `samples/errors/accept_json_header_without_format_param.xml` |
| 53 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` | 304 | `If-Modified-Since` -> **304** from Cloudflare (HIT) (0.07 s) | — (not saved; logged only) |
| 54 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=5&page=0` | 200 | **200 + error payload** id 120 (0.16 s) | `samples/errors/page_zero.json` |
| 55 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=5&page=abc` | 200 | **200 + error payload** id 120 (0.19 s) | `samples/errors/page_abc.json` |
| 56 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&foo=bar` | 200 | `foo=bar` ignored; byte-identical to baseline (0.18 s) | `samples/errors/unknown_query_param_ignored.json` |
| 57 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=100` | 200 | Pipeline call, MISS 0.23 s, 63 rows (0.23 s) | — (not saved; logged only) |
| 58 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=100` | 200 | Pipeline call, MISS 0.27 s, 63 rows (0.27 s) | — (not saved; logged only) |
| 59 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=100` | 200 | Pipeline call, MISS 0.45 s, 9 countries (0.45 s) | — (not saved; logged only) |

Totals: 67 HTTP requests (58 rows above; row 36 stands for 9). Statuses seen: 200, 301, 302, 304, 400, 404, and one client timeout. No 5xx and no 429.

## Open questions

1. **5xx/429 behavior is unknown.** We saw no 5xx, 429 or Cloudflare 52x responses, so we don't know the real shape of errors 105/199 (HTTP status? JSON envelope? HTML?) or what Cloudflare origin errors look like. The classifier treats them all as transient.
2. **Rate limits:** none are documented and none were observed in a 10-call burst. The actual throttling threshold, and whether Cloudflare bot management (`__cf_bm` cookie) ever challenges scripted clients with 403, is unknown.
3. **Date filter ignored:** *partly answered by verification.* Ranges entirely outside the available years (`1800:1801`, `1900:1959`, `2030:2031`) are ignored and return the full series. Partly-overlapping ranges (`1950:1965`, `2024:2026`) are clipped correctly. Single out-of-range years (`1800`, `2030`) return the no-data envelope. Still unknown: whether "available years" means per series or global (only `SP.POP.TOTL` for BRA was tested). Our 2018:2024 range behaved correctly.
4. **ISO2 `XX` returns a no-data envelope while ISO3 `XXX` returns error 120.** *Partly answered:* other unknown ISO2 codes (`QZ`, `ZZ`) return error 120, so the quirk is specific to `XX`. The reason is unknown, and aggregate codes were not tested. Mitigation: use ISO3 only, plus the pre-flight.
5. **Slow cache misses:** the cause of the 4–11 s, 21–24 s (verification pass) and one ~32 s responses is unknown, as is whether these latencies are typical at other times of day.
6. **Cache freshness:** the exact Cloudflare edge TTL is unknown (an entry was served at `age` 64145 s, past `max-age` 60849). We also don't know how soon a World Bank data release (a new `lastupdated`) becomes visible through both cache layers. *Critic update:* the R0 watermark itself was a ~19.7 h old edge copy at 01:59Z, while a cache-busted R0 was fresh in 0.17 s (area 03 C1/C2). There is also a third, inner cache path whose responses look fresh in the headers (area 04 Caching, item 3).
7. **Content-Type charset variation** (`application/json` vs `application/json;charset=utf-8`): the cause is unknown (possibly different origin instances or code paths). It is harmless if we match on the media type.
8. **Can HTTP 400 "Request Error" (WCF) be transient?** *Answered by verification: yes, at least for the reversed date range.* `date=2024:2018` gave the 400 at 01:12Z and HTTP 200 with data at 01:28Z (a cf MISS, so the origin computed it). `per_page=32768` still gave 400 on re-check. Unknown: what made the first reversed-date call fail, and whether the 400 can recur for valid requests. The policy now allows one retry for WCF "Request Error" pages.
9. **URL limits:** the documented 4,000-char total URL limit was not tested on its own. The exact per-segment limit lies somewhere between 299 (accepted) and 1,599 (rejected) characters; docs say 1,500.
10. **Untested error ids:** 112, 115 and 160 (filtering on an indicator value without a date range) were not triggered. Our pipeline does not use the features that raise them.

## Verification

Adversarial re-check on **2026-10-14 UTC** (live calls 01:27:13Z–01:30:31Z): **47 HTTP requests** with `curl -q -sS -m 60`, about 1 s apart, no credentials. Docs were re-read on the same date. New evidence is saved as `samples/errors/verify_*.json`, each with a `.meta.json` and a redacted `.headers.txt`. Offline checks: all 52 original sample bodies exist, every `.json` parses, every body has a `.meta.json` with `url`, `http_status`, `content_type`, `fetched_at_utc` and `notes`, and the `classify()` snippet above, run over every fixture, gives the expected class for each.

| claim | result | URL or doc used |
|---|---|---|
| Reversed date range `2024:2018` → deterministic HTTP 400 WCF "Request Error" | **corrected**: deterministic 400 → 400 once (01:12Z), then **HTTP 200 with the 7 rows for 2018–2024** (cf MISS, 3.37 s, byte-identical to baseline sha256 `24c384c0…`). A fresh `2022:2019` for USA also gave 200 (total 4). The API normalizes reversed ranges, and WCF 400s can be transient. Policy changed to one retry. | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024:2018`, `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL?format=json&date=2022:2019` |
| An unknown ISO2 code returns the no-data envelope on the data endpoint | **corrected**: "any unknown ISO2" → only `XX` (reconfirmed). `QZ` and `ZZ` return 200 + id 120 (102 bytes, sha256 `f230e5b6…`). | `https://api.worldbank.org/v2/country/XX/indicator/SP.POP.TOTL?format=json&date=2023:2024`, `.../country/QZ/...`, `.../country/ZZ/...` |
| Indicator pre-flight `/indicator/{code}` detects id 175 (archived) | **corrected**: detects 120/175 → detects 120 only. `/v2/indicator/IC.BUS.EASE.XQ` returns 200 with metadata (`source.id` `"57"`, "WDI Database Archives"). Id 175 appears only on the data endpoint (reconfirmed). | `https://api.worldbank.org/v2/indicator/IC.BUS.EASE.XQ?format=json`, `https://api.worldbank.org/v2/country/BRA/indicator/IC.BUS.EASE.XQ?format=json&date=2018:2024` |
| Validation: `meta` always has `page, pages, per_page, total, sourceid, lastupdated` with ints for the first four | **corrected**: true for the data endpoint only. On `/country` (the pipeline's call #1) and `/indicator`, `meta` = `{page, pages, per_page:"100" (string), total}` with no `sourceid`/`lastupdated`. | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=100` |
| Cache misses take 4–11 s (one ~32 s) | **corrected (extended)**: two small cold misses took **23.7 s** and **21.3 s**. The 60 s read timeout still holds. | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024:2026`, `https://api.worldbank.org/v2/country/BRA;BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024` |
| `date=1800:1801` → filter silently ignored, total 66 | confirmed (cf HIT). **Extended**: `1900:1959` and `2030:2031` are also ignored (total 66), `1950:1965` is clipped (total 6), `2024:2026` is clipped (total 2), and single years `1800` and `2030` give the no-data envelope. | `.../SP.POP.TOTL?format=json&date=1800:1801`, `date=1900:1959`, `date=2030:2031`, `date=1950:1965`, `date=1800`, `date=2030` |
| Invalid ISO3 `XXX` → HTTP 200 + id `"120"` (quoted string), 102 identical bytes for every cause | confirmed (live, plus 10 fixtures with identical sha256 `f230e5b6…`) | `https://api.worldbank.org/v2/country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` |
| No partial success: `BRA;XXX` fails the whole request | confirmed | `https://api.worldbank.org/v2/country/BRA;XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` |
| `per_page` 32767 accepted, 32768 → 400 text/html WCF (BOM, BYPASS) | confirmed (32768: 4.5 s, body starts `EF BB BF`) | `...?format=json&date=2023:2024&per_page=32767`, `...&per_page=32768` |
| `page=999` → 200, `meta.page` 999, rows `[]` | confirmed | `...?format=json&date=2018:2024&page=999` |
| `per_page=0`, `page=0`, `incomeLevel=ZZZ` → 200 + id 120 | confirmed | `...&per_page=0`, `...&per_page=5&page=0`, `https://api.worldbank.org/v2/country?incomeLevel=ZZZ&format=json` |
| Archived `IC.BUS.EASE.XQ` data → 200 + id `"175"`, key "Invalid format" | confirmed | `https://api.worldbank.org/v2/country/BRA/indicator/IC.BUS.EASE.XQ?format=json&date=2018:2024` |
| `format=yaml` → 200 `text/xml` `<wb:error>` id 111 | confirmed | `...?format=yaml&date=2018:2024` |
| `Accept: application/json` without `format` → XML | confirmed (`text/xml`, `<wb:data …>`) | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?date=2022:2024` |
| `/v9/...` → 404 JSON object `{statusCode, message}`, cf DYNAMIC | confirmed | `https://api.worldbank.org/v9/country/BRA?format=json` |
| Unversioned `/countries/...` → 301 to `/v2/countries/...`, plain-text "v1 was discontinued" body | confirmed | `https://api.worldbank.org/countries/BRA?format=json` |
| `http://` → 302 (Cloudflare) to https | confirmed | `http://api.worldbank.org/v2/country/BRA?format=json` |
| Unknown path → 404 `text/html; charset=UTF-8` WCF page | confirmed | `https://api.worldbank.org/v2/notanendpoint2?format=json` |
| Language `xx` → 200 + id 150 | confirmed | `https://api.worldbank.org/v2/xx/country/BRA?format=json` |
| `source=9999` → two id-120 messages | confirmed | `...?source=9999&format=json&date=2023:2024` |
| Multi-indicator without `source` → id 120; with `source=2` → `meta.sourceid` null | confirmed | `.../indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2023:2024` (with and without `source=2`) |
| Pipeline calls 3–6 → `total` 63, `pages` 1 | confirmed for **all 4 indicators at the exact `per_page=1000` URLs** (first pass used `per_page=100`). Row order: country asc, date desc. 0 null values today. | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/{SP.POP.TOTL,NY.GDP.MKTP.CD,NY.GDP.PCAP.CD,SP.DYN.LE00.IN}?format=json&date=2018:2024&per_page=1000` |
| Country call returns exactly the 9 ids | confirmed (BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF) | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=100` |
| Cloudflare serves entries past `max-age`; errors are cached | confirmed (`/country/XX` HIT at `age: 65024`, `max-age=60849`) | `https://api.worldbank.org/v2/country/XX?format=json` |
| Origin `max-age = 86400 − (now − last-modified)` | confirmed (baseline arithmetic 86400 − 21697 = 64703; a fresh origin miss had `last-modified` = `date` and `max-age=86400`) | baseline headers; `.../country/USA/...date=2022:2019` headers |
| `If-Modified-Since` → 304 from Cloudflare | confirmed | baseline URL with `If-Modified-Since: Tue, 13 Oct 2026 19:10:06 GMT` |
| Duplicate codes de-duplicated | confirmed (`BRA;BRA`, 2023:2024 → total 2) | `https://api.worldbank.org/v2/country/BRA;BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024` |
| Lower case, trailing slash, `%3A`, unknown param → byte-identical to baseline | confirmed (lower case and trailing slash live; all 4 fixtures sha256 `24c384c0…`) | `.../country/bra/indicator/sp.pop.totl?...`, `.../SP.POP.TOTL/?...` |
| Content-Type varies (`application/json` vs `;charset=utf-8`) | confirmed (trailing slash and `format=jsonp` → `application/json`; others → `;charset=utf-8`) | as above, `...?format=jsonp&date=2023:2024` |
| `format=jsonp` without `prefix` → plain JSON | confirmed | `...?format=jsonp&date=2023:2024` |
| 1,599-char path segment → 400 HTTP.sys "Invalid URL" (`us-ascii`, no BOM) | confirmed | `https://api.worldbank.org/v2/country/BRA;…(×400)/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` |
| gzip honored | confirmed (`content-encoding: gzip`, 1,223 bytes, magic `1f8b`) | NY.GDP.MKTP.CD pipeline URL with `Accept-Encoding: gzip` |
| Error-code table (105/503, 110–120/404, 140–160/400, 199/500); 175 undocumented | confirmed | https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes |
| Default XML; formats xml/json/jsonP(+prefix)/jsonstat; per_page default 50, no max documented; 60 indicators, 1,500 chars/segment, 4,000 chars/URL; no sorting | confirmed | https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures |
| "not guaranteed to have 100% uptime"; caching recommended; no rate limits documented | confirmed (902064 and 1886674 mention no rate limits) | https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices, https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api |
| v1 discontinued June 19, 2020; old endpoints documented as "Resource not found" | confirmed (doc). Live behavior (301) differs, as the doc already states. | https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation |
| All 4 WCF HTML error fixtures start with a UTF-8 BOM | confirmed (`xxd`), and live for `per_page=32768` | local fixtures |
| 299-char segment → 30 s client timeout, then CDN HIT on retry | unverified: not re-run, to avoid a 30 s+ origin call. The fixture shows `total: 1` (de-duplication). | `path_segment_299_chars_*` fixtures |
| `per_page=100000` → 400; HEAD → 200; 10-call burst shows no throttling; two Cloudflare IPs | unverified: not re-run (fixtures and headers exist for the first two) | — |
| Errors 105/112/115/160/199, 5xx/429 shapes, 4,000-char URL limit | unverified: not triggered by either pass (still open questions 1, 9, 10) | — |

## Critic additions

The completeness critic made no new calls with an error outcome in this area. Its evidence for the retry rule above is in area 02 (C1–C7: a wrong body on HTTP 200, a same-URL retry served by Cloudflare with the same bytes, and a cache-busted retry that recovered, plus one that did not) and area 03 (C1/C2: R0 freshness). Cross-file corrections made here: F11 and validation step 4 now fail on out-of-range rows (README #15); the read timeout points to the README's 90 s (README #1; max observed now 45.2 s); the `classify()` snippet is marked as superseded by README §2 (README #7); the body-hash idempotency note is qualified (README #13); "Calls our pipeline will make" points to the canonical README URLs; the fixture map gains the wrong-body retry fixtures.

Still unobserved after the critic pass: any 5xx, 429, error 105/199, Cloudflare 52x or bot challenge; whether Cloudflare caches a 5xx body here; the 4,000-character URL limit.
