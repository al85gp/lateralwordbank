# World Bank Indicators API — call map

This folder maps the World Bank Indicators API (v2) for the Lateral take-home pipeline (Python + dlt + dbt + DuckDB, to be built). It answers three questions: **which calls one pipeline run makes**, **how the client must behave** (paging, validation, errors, retries, lineage), and **which saved responses become offline test fixtures**.

It summarises five area docs. Each one has the full parameter tables, field dictionaries, evidence logs and an adversarial verification section:

| Doc | Area |
|---|---|
| [01_countries_and_classifications.md](01_countries_and_classifications.md) | Countries, regions, income levels, lending types, aggregates |
| [02_indicator_observations.md](02_indicator_observations.md) | The core observation call, missing data, numeric types |
| [03_indicator_and_source_metadata.md](03_indicator_and_source_metadata.md) | Indicator and source metadata, freshness signals, licence |
| [04_pagination_and_envelope.md](04_pagination_and_envelope.md) | Response envelope, paging, caching |
| [05_errors_and_resilience.md](05_errors_and_resilience.md) | Error shapes, failure modes, retry policy |

Other files: [`requests.http`](requests.http) holds every canonical call in REST Client format. `samples/` holds the raw responses, saved byte-for-byte, each with a `.meta.json` sidecar.

**Tags used below:** **[observed]** = a live call on 2026-10-14 UTC (evidence in the linked area doc). **[documented]** = stated in World Bank docs. **[synthesis]** = checked while writing this README, including 5 extra live calls (see [Contradictions](#contradictions-between-area-docs-and-how-they-were-resolved)). **[observed by critic]** = a live call by the completeness-critic pass (2026-10-14 01:59–02:03 UTC, 20 requests; see [Completeness critic pass](#completeness-critic-pass)). Where docs and live behaviour disagree, live behaviour drives the design.

---

## Scope

| | |
|---|---|
| Countries (9) | BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF |
| Indicators (4) | SP.POP.TOTL, NY.GDP.MKTP.CD, NY.GDP.PCAP.CD, SP.DYN.LE00.IN |
| Years | 2018–2024 inclusive (7 years). Expected grid: 9 × 4 × 7 = **252 cells** |
| Samples captured | **2026-10-14 between 01:11 and 02:03 UTC** (local date 2026-10-13). Area probing ran 01:11–01:22, verification 01:22–01:35, synthesis checks 01:42–01:43, and the completeness critic 01:59–02:03. About 470 live requests in total, all unauthenticated. |
| Data version seen | WDI `lastupdated` = **`2026-10-08`** on every observation envelope and on `source/2` |

### The 9 countries as the API returns them today [observed, 01]

| ISO3 (`id`) | ISO2 (`iso2Code`) | Name | Region (`region.id`) | **Current** income group | Lending type |
|---|---|---|---|---|---|
| BRA | BR | Brazil | LCN `"Latin America & Caribbean "` | UMC | IBD |
| CAN | CA | Canada | NAC | HIC | LNX |
| DEU | DE | Germany | ECS | HIC | LNX |
| GBR | GB | United Kingdom | ECS | HIC | LNX |
| IND | IN | India | SAS | LMC | IBD |
| JPN | JP | Japan | EAS | HIC | LNX |
| MEX | MX | Mexico | LCN `"Latin America & Caribbean "` | UMC | IBD |
| USA | US | United States | NAC | HIC | LNX |
| ZAF | ZA | South Africa | SSF `"Sub-Saharan Africa "` | UMC | IBD |

Income-group coverage: **HIC 5, UMC 3, LMC 1 (India only), LIC 0**. An "across income groups" comparison therefore has three groups, and one of them has a single member.

### The 4 indicators [observed, 03]

| Indicator | Name (`indicator.value`) | Unit (Metadata API `Unitofmeasure`) | WB aggregation method | `decimal` | JSON `value` tokens in scope | Reference period |
|---|---|---|---|---|---|---|
| SP.POP.TOTL | Population, total | `Unit` | Sum | 0 | all ints | 1960-2025 |
| NY.GDP.MKTP.CD | GDP (current US$) | `Current US$` | Gap-filled total | 0 | 56 floats + 7 ints (all USA, e.g. `29298013000000`) | 1960-2025 |
| NY.GDP.PCAP.CD | GDP per capita (current US$) | `Current US$` | Weighted average | 1 | floats | 1960-2025 |
| SP.DYN.LE00.IN | Life expectancy at birth, total (years) | `Years` | Weighted average | 0 | floats (2–13 decimals) | **1960-2024** |

`/indicator/{code}.unit` is `""` for all four. The real unit exists only in the Metadata API.

---

## Quick reference

- **Base URL:** `https://api.worldbank.org/v2/`. Use HTTPS only: `http://` gives a Cloudflare 302, and an unversioned path gives a 301 to `/v2/` [observed, 05].
- **Auth:** none. No key, no account, no rate-limit headers. CORS `*`. Never send credentials.
- **Always send `format=json`.** The default is XML [documented + observed]. An `Accept: application/json` header is ignored [observed, 04/05]. `format=JSON` (upper case) also works.
- **Infrastructure:** Cloudflare (`cf-cache-status`, `cf-ray`) in front of an ASP.NET/WCF origin that has its own output cache. Both layers cache successes **and errors** for about 24 h [observed, 04/05].
- **Licence:** CC BY 4.0 (`License_Type: "CC BY-4.0"` on every series) [observed, 03]. Attribute "World Bank, World Development Indicators" and say that values were transformed.
- **Limits:** `per_page` default 50, maximum **32767**. 32768 and above give an HTTP 400 HTML page [observed, 04/05]. No maximum is documented. Docs also limit a call to 60 indicators, 1,500 characters between slashes and 4,000 characters per URL [documented, 898581]. Our 9-country list is 35 characters.
- **Latency:** 0.06 s on a cache hit, up to **45.2 s** on a cold origin miss [observed by critic, 01 C1; previously 39.5 s, 02 V9]. 30 s client timeouts happened twice [observed, 03 V3, 05 #34].

### Response envelope shapes (every one of them can arrive with HTTP 200)

| Shape | Looks like | Meaning | Fixture |
|---|---|---|---|
| Success | `[ {meta}, [ {row}, … ] ]` | Rows at `$[1]`, page count at `$[0].pages`, 1-based pages | `samples/observations/SP.POP.TOTL__9_countries__2018_2024.json` |
| No data | `[ {"page":0,"pages":0,"per_page":0,"total":0,"sourceid":null,"lastupdated":null}, null ]` | A valid query that matched nothing. **Rows are `null`, not `[]`.** | `samples/pagination/zero_match_date_1900_single_year.json` |
| Past the last page | `[ {"page":999,"pages":7,…}, [] ]` | `page > pages`, which is not an error | `samples/pagination/page_beyond_range_page_999.json` |
| **API error** | `[ {"message":[ {"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"} ]} ]` | A 1-element array. The `id` is a **string**, and there can be more than one message | `samples/errors/invalid_country_xxx_data.json` |
| XML error | `<wb:error><wb:message id="111" …>` as `text/xml` | Bad `format`, a missing `format`, or a Metadata API error (id 160), **even with `format=json`** | `samples/errors/bad_format_value_yaml.xml` |
| Gateway error | `{"statusCode":404,"message":"Resource not found"}` with HTTP 404 | Wrong API version path | `samples/errors/invalid_api_version_v9.json` |
| HTML error | WCF "Request Error" (HTTP 400, UTF-8 BOM) / "Endpoint not found." (404) / HTTP.sys "Invalid URL" (400) | `per_page` ≥ 32768, unknown path, overlong segment | `samples/errors/per_page_32768.html` |
| Metadata API success | A JSON **object** `{page,pages,per_page:"5000",total,source:[…]}` | `/sources/2/…/metadata`, not an array | `samples/metadata/country_series_metadata_9_countries_4_indicators.json` |

### Meta field JSON types by endpoint [observed, 01/03/04]

| Endpoint | `page` | `pages` | `per_page` | `total` | `sourceid` | `lastupdated` |
|---|---|---|---|---|---|---|
| Observations, one indicator | number | number | number | number | `"2"` | `"2026-10-08"` |
| Observations, multi-indicator | number | number | number | number | **`null`** | string |
| Observations, zero-match | `0` | `0` | `0` | `0` | `null` | `null` |
| `/country…`, `/indicator…` | number | number | **string** | number | absent | absent |
| `/incomeLevel`, `/region`, `/lendingType`, `/source`, `/sources` | **string** | **string** | **string** | **string** | absent | absent |
| Metadata API (object) | number | number | **string** (`"5000"` default) | number (counts **metatype entries**) | absent | absent |

Cast every meta value with `int()`.

---

## Endpoint inventory

| Endpoint | URL template | Used by pipeline? | Doc |
|---|---|---|---|
| Source (release watermark) | `https://api.worldbank.org/v2/source/{id}?format=json` (also `/sources/{id}`) | **Yes** (R0) | [03](03_indicator_and_source_metadata.md) |
| Income levels | `https://api.worldbank.org/v2/incomeLevel?format=json` | **Yes** (R1) | [01](01_countries_and_classifications.md), [04](04_pagination_and_envelope.md) |
| Regions | `https://api.worldbank.org/v2/region?format=json&per_page=100` | Optional (R1b). The labels are already embedded in R2 | [01](01_countries_and_classifications.md) |
| Lending types | `https://api.worldbank.org/v2/lendingType?format=json` | Optional (R1c) | [01](01_countries_and_classifications.md) |
| Country, selected codes | `https://api.worldbank.org/v2/country/{ISO3;…}?format=json&per_page={n}` | **Yes** (R2), and it doubles as the code pre-flight | [01](01_countries_and_classifications.md), [04](04_pagination_and_envelope.md), [05](05_errors_and_resilience.md) |
| Country, full list | `https://api.worldbank.org/v2/country?format=json&per_page={n}&page={p}` | No (296 rows = 217 economies + 79 aggregates) | [01](01_countries_and_classifications.md) |
| Country members of a group | `https://api.worldbank.org/v2/{incomeLevel\|region\|lendingType}/{id}/country?format=json` or `country?incomeLevel={id}` | No. Current classification only, and **the lending-type filter duplicates rows** | [01](01_countries_and_classifications.md) |
| WDI country metadata | `https://api.worldbank.org/v2/sources/2/country/{iso3}/metadata?format=json` | No (`IncomeGroup`, current only) | [01](01_countries_and_classifications.md) |
| Indicator metadata | `https://api.worldbank.org/v2/indicator/{code}?format=json` | **Yes** (R3–R6). One code per call: a `;` list returns an empty 200 | [03](03_indicator_and_source_metadata.md), [05](05_errors_and_resilience.md) |
| Indicator list | `https://api.worldbank.org/v2/indicator?format=json&per_page={n}` | No (29,544 indicators) | [04](04_pagination_and_envelope.md) |
| Metadata API, country × series | `https://api.worldbank.org/v2/sources/2/country/{iso3;…}/series/{code;…}/metadata?format=json` | **Yes** (R7). **Never send `per_page`** | [03](03_indicator_and_source_metadata.md) |
| Metadata API, series only | `https://api.worldbank.org/v2/sources/2/series/{code;…}/metadata?format=json` | No (R7 is a superset) | [03](03_indicator_and_source_metadata.md) |
| Metadata API, IncomeGroup metatype | `https://api.worldbank.org/v2/sources/2/country/{iso3;…}/metatypes/incomegroup/metadata?format=json` | Optional cross-check | [03](03_indicator_and_source_metadata.md) |
| Sources list / concepts | `https://api.worldbank.org/v2/sources?format=json&per_page=100`, `…/sources/{id}/concepts?format=json` | No | [03](03_indicator_and_source_metadata.md) |
| Footnote metadata (undocumented) | `https://api.worldbank.org/v2/sources/2/footnote/{ISO3}~{SERIES}~YR{yyyy}/metadata?format=json` | No (support status unknown) | [03](03_indicator_and_source_metadata.md) |
| **Observations, one indicator** | `https://api.worldbank.org/v2/country/{ISO3;…}/indicator/{ID}?source=2&format=json&date={Y1}:{Y2}&per_page={n}&page={p}` | **Yes** (R8–R11) | [02](02_indicator_observations.md), [04](04_pagination_and_envelope.md), [05](05_errors_and_resilience.md) |
| Observations, multi-indicator | `…/country/{ISO3;…}/indicator/{ID1;ID2;…}?source=2&format=json&…` | No. `sourceid` is null, one bad code fails all indicators, and it needs `source=` | [02](02_indicator_observations.md) |
| Income-group aggregates as "countries" | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/{ID}?format=json&date=2018:2024&per_page=100` | Optional benchmark (R12–R15). Uses **current** membership | [01](01_countries_and_classifications.md) |
| `country/all` observations | `…/country/all/indicator/{ID}?format=json&…` | No (aggregates mixed in, 375 KB) | [01](01_countries_and_classifications.md), [04](04_pagination_and_envelope.md) |
| Advanced Data API (beta) | `https://api.worldbank.org/v2/sources/{src}/country/{c}/series/{id}/time/{t}/data?format=json` | No | [02](02_indicator_observations.md), [03](03_indicator_and_source_metadata.md) |
| Bulk download | `…?downloadformat=csv\|xml\|excel` | No (ZIP files, not probed) | [02](02_indicator_observations.md) |
| `format=jsonstat` / `jsonP` | `…?format=jsonstat` | No (no paging meta) | [04](04_pagination_and_envelope.md) |
| Language prefix | `https://api.worldbank.org/v2/{lang}/…` | **Never.** It translates labels and even `capitalCity` | [01](01_countries_and_classifications.md) |
| Data360 API (successor platform) | `https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_{CODE}&REF_AREA={iso3}` | No (different schema; `OBS_VALUE` is a string) | [03](03_indicator_and_source_metadata.md) |
| Historical income classification (OGHIST) | Data Catalog Excel `https://ddh-openapi.worldbank.org/resources/DR0095334/download` | **Decision D1.** Not a REST endpoint and not downloaded | [01](01_countries_and_classifications.md) |

---

## The pipeline call plan

One full run makes these calls **in this order**, sequentially, about 1 s apart. The order matters:
1. Fetch the release watermark first, so a WDI release that lands mid-run can be detected.
2. Fetch reference and dimension calls next. R2 is also the pre-flight check of the configured codes, and since one bad code fails a whole batch with an anonymous error 120, the run fails fast before any fact call.
3. Fetch the observations last.

All URLs are exact. Every recommended URL has a fixture that is **byte-identical** to what the live API returned on 2026-10-14.

| # | Phase | `GET` URL | Purpose | Pages | Rows (2026-10-14) | Raw table | Landing file | Fixture |
|---|---|---|---|---|---|---|---|---|
| **R0** | Watermark | `https://api.worldbank.org/v2/source/2?format=json` | WDI release date (`lastupdated` `"2026-10-08"`), checked against every observation envelope | 1 | 1 | `raw_wb__source` | `source/source_2.json` | `samples/metadata/source_2_wdi.json` |
| **R1** | Reference | `https://api.worldbank.org/v2/incomeLevel?format=json` | Income-level labels. HIC/UMC/LMC/LIC are atomic; LMY and MIC are composites; INX means "not classified" | 1 | 7 | `raw_wb__income_level` | `reference/income_level.json` | `samples/countries/incomeLevel.json` |
| **R2** | Countries | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=50` | Country dimension: ISO3/ISO2, region, **current** income level, lending type. Also the code pre-flight | 1 | 9 | `raw_wb__country` | `country/country__9.json` | `samples/countries/country_9_selected.json` |
| **R3** | Indicator metadata | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` | Name, `source.id` (assert `"2"`), `sourceNote`, `sourceOrganization`, topics | 1 | 1 | `raw_wb__indicator`, `raw_wb__indicator_topic` | `indicator/SP.POP.TOTL.json` | `samples/metadata/indicator_sp_pop_totl.json` |
| **R4** | Indicator metadata | `https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.CD?format=json` | as R3 | 1 | 1 | as R3 | `indicator/NY.GDP.MKTP.CD.json` | `samples/metadata/indicator_ny_gdp_mktp_cd.json` |
| **R5** | Indicator metadata | `https://api.worldbank.org/v2/indicator/NY.GDP.PCAP.CD?format=json` | as R3 | 1 | 1 | as R3 | `indicator/NY.GDP.PCAP.CD.json` | `samples/metadata/indicator_ny_gdp_pcap_cd.json` |
| **R6** | Indicator metadata | `https://api.worldbank.org/v2/indicator/SP.DYN.LE00.IN?format=json` | as R3 | 1 | 1 | as R3 | `indicator/SP.DYN.LE00.IN.json` | `samples/metadata/indicator_sp_dyn_le00_in.json` |
| **R7** | Indicator metadata | `https://api.worldbank.org/v2/sources/2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json` | Unit of measure, aggregation method, periodicity, reference period, licence (Series); IncomeGroup and Region cross-check (Country); per-country source notes (Country-Series). **Do not send `per_page`.** | 1 | 300 metatype entries (4 Series + 9 Country + 18 Country-Series variables) | `raw_wb__metadata_kv` (long: concept, variable_id, metatype, value) | `metadata/country_series__9x4.json` | `samples/metadata/country_series_metadata_9_countries_4_indicators.json` |
| **R8** | Observations | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?source=2&format=json&date=2018:2024&per_page=1000` | Facts | 1 | 63 | `raw_wb__observation` | `observation/SP.POP.TOTL__p1.json` | `samples/observations/SP.POP.TOTL__9_countries__2018_2024.json` |
| **R9** | Observations | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000` | Facts | 1 | 63 | `raw_wb__observation` | `observation/NY.GDP.MKTP.CD__p1.json` | `samples/observations/NY.GDP.MKTP.CD__9_countries__2018_2024.json` |
| **R10** | Observations | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000` | Facts | 1 | 63 | `raw_wb__observation` | `observation/NY.GDP.PCAP.CD__p1.json` | `samples/observations/NY.GDP.PCAP.CD__9_countries__2018_2024.json` |
| **R11** | Observations | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | Facts | 1 | 63 | `raw_wb__observation` | `observation/SP.DYN.LE00.IN__p1.json` | `samples/observations/SP.DYN.LE00.IN__9_countries__2018_2024.json` |

**Optional calls** (decisions D3 and D5):

| # | `GET` URL | Purpose | Rows | Raw table | Fixture |
|---|---|---|---|---|---|
| R1b | `https://api.worldbank.org/v2/region?format=json&per_page=100` | Region labels. The record shape is different: `code`/`name` instead of `id`/`value` | 44 | `raw_wb__region` | `samples/countries/region.json` |
| R1c | `https://api.worldbank.org/v2/lendingType?format=json` | Lending-type labels | 4 | `raw_wb__lending_type` | `samples/countries/lendingType.json` |
| R12–R15 | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/{ID}?format=json&date=2018:2024&per_page=100` (one per indicator) | WB-computed income-group aggregates, **benchmark only** (current membership; aggregated by Sum, Gap-filled total or Weighted average) | 28 each | `raw_wb__indicator_aggregate` | `samples/countries/income_aggregates_{SP.POP.TOTL,NY.GDP.MKTP.CD,NY.GDP.PCAP.CD,SP.DYN.LE00.IN}.json` (all four; the last three added by the critic) |
| — | OGHIST Excel (not REST, not fetched at run time) | As-of-year income classification, if decision D1 adopts it | 63 kept | dbt seed `seed_income_class_history.csv` | — (not downloaded; needs approval) |

Notes on the plan:
- **Landing files.** Paths are relative to `raw/worldbank/run_id=<run_id>/`. Each landing file is the response body byte-for-byte, with a `.meta.json` sidecar in the same format as `samples/`. This makes fixtures and production landings interchangeable.
- **`&page=1`.** A generic paginator (dlt `PageNumberPaginator`) adds `&page=1` to the first request. The body is byte-identical to the fixture; area 04 V01–V05 checked this live. Match fixtures on path plus the semantic params, not on the raw query string.
- **`source=2` on R8–R11 is a no-op** (see Contradictions #5). It is kept so the URLs equal the golden fixture URLs. The real guard is the assertion `meta.sourceid == "2"`.
- **Minimum viable run:** R2 + R8–R11 (5 requests). R2 already embeds the income-level, region and lending-type labels.
- **Never** use `mrv`, `mrnev`, `gapfill` (it fabricates values), `scale` (lossy), `frequency`, a language prefix, `country/all`, or the multi-indicator form [observed, 02].

## Request budget for one full run

| Profile | Calls | Requests (no retries) | Rows | Bytes (decoded) | Worst case with 5 attempts each |
|---|---|---|---|---|---|
| Minimum | R2, R8–R11 | 5 | 9 + 252 | ≈ 57 KB | 25 |
| **Recommended** | R0–R11 | **12** | 1 + 7 + 9 + 4 + 300 metatype entries + **252 observations** | ≈ 100 KB | 60 |
| Everything optional | R0–R15 + R1b + R1c | 18 | + 44 + 4 + 112 | ≈ 127 KB | 90 |

Wall-clock: a run of cached calls takes about 15 s (1 s spacing plus 0.1–0.5 s per call). A cold run can take several minutes, because individual misses take 9–45 s [observed, 02/04/05; critic 01 C1]. Size the job timeout at 10 minutes or more. The run is far below any plausible rate limit: about 470 calls at roughly 1 req/s on 2026-10-14 produced no 429, no 5xx and no rate-limit headers [observed, all areas].

---

## Client behaviour contract

### 1. Transport
- Use HTTPS, base `https://api.worldbank.org/v2/`, with **redirects disabled** (`allow_redirects=False`). Any 3xx is a fatal configuration error [observed, 05].
- **Timeouts: connect 10 s, read 90 s.** The maximum observed was 39.5 s, and about 33 s origin times broke 30 s clients (Contradictions #1).
- **Retries: at most 5 attempts in total.** Use full-jitter exponential backoff, `sleep = uniform(0, min(30, 2**(attempt-1)))` seconds. Honour `Retry-After` (capped at 120 s) if it ever appears; it never has.
- Make requests sequentially with about 1 s spacing. No authentication headers. `Accept-Encoding: gzip` is fine; hash the **decoded** bytes.
- HTTP libraries may percent-encode `;` in paths and `:` in `date`. Both forms are accepted and return byte-identical bodies [observed, 05].
- Don't add cache-busting parameters on **first** attempts. They usually force a fresh origin render, but not always, and `lastupdated` is the right version signal anyway (Contradictions #3).
- **Do** add a fresh token (`&cb=<run_id>-<attempt>`) when **retrying anything that arrived as HTTP 200** (a validation failure, a `lastupdated` mismatch with R0, API ids 105/199). Cloudflare holds the exact URL's bytes for up to ~24 h, so a same-URL retry returns the same body [observed by critic, 02 C3]; the token got the correct body in 02 C4 but not in 02 C7, so validate every attempt (Contradictions #18).

### 2. Classify every response before parsing rows

```python
def classify(resp, endpoint_family):
    mt = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()   # charset varies: match media type only
    if 300 <= resp.status_code < 400:  return "redirect"            # fatal (config)
    if resp.status_code != 200:        return f"http_{resp.status_code}"  # see policy table
    if mt != "application/json":       return "non_json"            # XML default / XML error 111,160 / HTML: fatal
    body = json.loads(resp.content.decode("utf-8-sig"))             # ValueError -> "truncated" (retry)
    if isinstance(body, dict):
        if "statusCode" in body:       return "gateway_error"       # {"statusCode":404,"message":"Resource not found"}: fatal
        if endpoint_family == "metadata_api" and "source" in body: return "ok"   # Metadata API success is an OBJECT
        return "bad_envelope"                                       # fatal
    if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict) and "message" in body[0]:
        ids = [m["id"] for m in body[0]["message"]]                  # ids are STRINGS, e.g. "120"
        return "api_error_retryable" if set(ids) <= {"105", "199"} else "api_error_fatal"
    if not (isinstance(body, list) and len(body) == 2 and isinstance(body[0], dict)):
        return "bad_envelope"                                       # fatal
    return "no_data" if body[1] is None else "ok"                   # [meta0, null] is a legitimate empty result
```

Area 05's original `classify()` treats every top-level JSON object as a gateway error. That would reject R7, whose successful response is an object. The version above fixes this (Contradictions #7).

### 3. Retry versus fail-fast policy

| Outcome | Examples (fixtures) | Action |
|---|---|---|
| Connection error, timeout, truncated JSON | `errors/path_segment_299_chars_client_timeout.meta.json` then `…_retry_cdn_hit.json` | **Retry.** The CDN usually holds the result the origin finished after the client gave up |
| HTTP 408, 425, 429, 500, 502, 503, 504, 520–524, 530; API ids `105`, `199` | documented only, never observed | **Retry** |
| HTTP 400 with a WCF "Request Error" HTML body | `errors/reversed_date_range_2024_2018.html` (it was transient) | **One** retry, then fatal |
| HTTP 400 (HTTP.sys "Invalid URL"), 401, 403, 404, 405 | `errors/path_segment_over_1500_chars.html`, `errors/unknown_endpoint_path.html` | Fatal |
| Any 3xx | `errors/http_scheme_no_tls.html`, `errors/retired_v1_endpoint_no_version.txt` | Fatal (configuration) |
| `non_json`, `gateway_error`, `bad_envelope` | `errors/missing_format_param_default_xml.xml`, `errors/invalid_api_version_v9.json` | Fatal |
| API ids `110 111 112 115 120 140 150 160 175` or any unknown id | `errors/invalid_country_xxx_data.json` (120), `errors/archived_indicator_doing_business.json` (175, undocumented) | Fatal, no retry. Error 120 never names the bad parameter, so on R2 probe `/country/{code}` once per code to say which one is bad |
| Validation failure (next section) | `observations/anomaly__wrong_body__*.json`; retry sequence `observations/critic__inner_cache_{B,C,D}_*.json` | Retry once after backoff **with a fresh cache-buster token** (a same-URL retry is a Cloudflare HIT with the same bytes, 02 C3), then fatal. Never load a partial result |

### 4. Pagination algorithm (applies to every array endpoint; the Metadata API is handled separately)

```text
page = 1; first = None; rows_total = 0
loop:
    resp = GET(url, params | {page})                      # transport rules above
    outcome = classify(resp); land raw bytes + .meta.json  # land BEFORE validating, for forensics
    if outcome == "no_data": record "no data"; break       # pages == 0
    meta, rows = body[0], body[1] or []
    pages, total = int(meta["pages"]), int(meta["total"])   # int(): several endpoints send strings
    if first is None: first = (pages, total, meta.get("lastupdated"))
    elif (pages, total, meta.get("lastupdated")) != first: restart from page 1 once, then fail   # release landed mid-fetch
    assert int(meta["page"]) == page
    rows_total += len(rows); yield rows tagged with lineage
    if page >= pages: break                                 # stop on meta.pages; an empty page means stop too
    page += 1
assert rows_total == first.total
```

Metadata API (R7): send no `per_page` and assert `pages == 1`. If paging is ever needed, merge on `(concept, variable_id)`, because one variable's metatypes can be split across pages [observed, 03 V4/V5].

### 5. Response validation

**Observations (R8–R11), all pages together.** These checks also catch the observed "wrong body for the request" anomaly [observed, 02 #25, V17–V19]:
1. `int(meta.per_page) == 1000`, `meta.sourceid == "2"`, and `meta.lastupdated` matches `^\d{4}-\d{2}-\d{2}$` and equals R0's `lastupdated` (on a mismatch, warn and refetch once **with a cache-buster token**, because a plain refetch returns the same edge copy; CDN skew can last about 25 h, and R0 itself was a ~19.7 h old edge copy at 01:59Z [observed by critic, 03 C1]).
2. Every row: `indicator.id` equals the requested code (case-insensitive), `countryiso3code` is in the 9 configured codes, and `date` is a 4-digit string with `int(date)` in [2018, 2024]. Out-of-range rows mean the date filter was ignored or a wrong body arrived: **fail**, don't silently drop [observed, 04/05].
3. Required keys are `indicator`, `country`, `countryiso3code`, `date` and `value`. **Every other key is optional**: `unit` can be missing, and `scale` and `footnote` can appear uninvited [observed, 02 V9/V17; synthesis].
4. `value` is a JSON number or `null`. Never a string. Land it as DOUBLE.
5. `Σ rows == meta.total`, `meta.total <= 63`, and no duplicate `(countryiso3code, indicator.id, date)`.
6. Completeness: left-join the rows onto the expected 9 × 7 grid and label every cell `present`, `reported_null` or `not_returned`. **Never coerce to 0.** This is a warning, not an error.

**Country (R2):** exactly 9 rows, the id set equals the config, and no row has `region.id == "NA"` (an aggregate). **Indicator (R3–R6):** exactly 1 row, `id` equals the requested code, and **`source.id == "2"`**. An archived indicator still returns valid-looking metadata from source `"57"` [observed, 05 verify]. **Income levels (R1):** contains HIC, UMC, LMC and LIC. **Metadata API (R7):** an object with `pages == 1`, and Series variables for all 4 codes and Country variables for all 9 present. **Source (R0):** 1 row with `id == "2"`.

### 6. Lineage to capture

**Per HTTP attempt** (a `_raw_requests` log table):
- Request: `run_id`, `request_key` (e.g. `observation__SP.POP.TOTL`), `attempt`, `url` (exactly as sent), `fetched_at_utc`, `duration_ms`.
- Response: `http_status`, `content_type`, `content_encoding`, `bytes`, `body_sha256` (decoded bytes), `landing_path`, `outcome`, `retry_reason`.
- Envelope: `meta_page`, `meta_pages`, `meta_per_page`, `meta_total`, `meta_sourceid`, `meta_lastupdated`.
- Response headers: `date`, `cf-ray`, `cf-cache-status`, `age`, `last-modified` and `cache-control`. `last-modified` and a `max-age` below 86400 are what reveal an old cached copy, even on a Cloudflare MISS [observed, 04; synthesis T3/T4]. **They do not reveal the inner cache path:** responses served as plain `application/json` (no charset) have `last-modified` = `date` and `max-age=86400`, exactly like a fresh render, yet can carry another query's body [observed by critic, 02 C2; offline re-analysis of all 13 such headers]. Keep the raw `content_type` per attempt; a missing charset is the only marker (Contradictions #17).

**Per landed row** (every raw table): `_run_id`, `_source_url`, `_source_page`, `_row_index` (position in `$[1]`), `_response_sha256`, `_fetched_at_utc`, `_source_id`, `_source_last_updated`. With these, any output value can be traced to one stored response.

**Versioning and idempotency:**
- Natural key is `(indicator_id, country_iso3, year)`. Merge on it.
- Version snapshots by `source_last_updated`. Because `lastupdated` is WDI-wide [observed, 03], one release = one version.
- Do **not** use `body_sha256`, `Last-Modified`, `ETag` (absent) or a 304 as change detectors. The same URL can return different bytes for the same data (the `footnote` key can appear or disappear, see Contradictions #4), and `Last-Modified` is only the time the cache copy was rendered. Compare parsed values instead.
- **Change detection for production** [inference from observed by critic, 03 C1/C2]: the cheapest release probe is one cache-busted `source/2?format=json&cb=<token>` request (≈0.2 s, 236 bytes). The plain R0 URL can lag a release by up to ~24 h at the edge. When the probed `lastupdated` is newer than the last loaded version, run the pipeline; if any observation envelope still shows the old date after its cache-busted retry, defer the load instead of mixing releases. Whether `lastupdated` moves on every value revision is still unknown (only one release observed).

### 7. dlt `rest_api` notes (verified offline with dlt 1.31.0, area 04)
- `data_selector: "$[1]"`, `paginator: {type: page_number, base_page: 1, page_param: page, total_path: "$[0].pages"}`. A string `pages` value is fine, because dlt calls `int()` on it.
- **An HTTP-200 error body is silently loaded as an empty page** under `stop_after_empty_page=True`. You need a response hook or a custom resource that runs `classify()` and raises.
- Pin `value` with `{data_type: double}`, otherwise the int/float token mix creates a variant column [inference].
- R7 is an object, not `[meta, rows]`. Give it its own resource with no paginator.

---

## Data-model gotchas

- **ISO2 vs ISO3.** In observation rows, `country.id` is **ISO2** (`"BR"`) and `countryiso3code` is **ISO3** (`"BRA"`). In country records, `id` is ISO3 and `iso2Code` is ISO2. **Join on ISO3** (`countryiso3code` = country `id`). → [01](01_countries_and_classifications.md), [02](02_indicator_observations.md)
- **Null rows vs absent rows.** An unpublished year inside the series time axis comes back as a row with `"value": null`; for example, life expectancy 2025 is null for all 9 countries, and Gini has 25 of 63 cells null. A year beyond the time axis (2026) is **absent**, and `total` counts null rows but not absent ones. Model three cell states (`present`, `reported_null`, `not_returned`) on a generated grid. → [02](02_indicator_observations.md)
- **String-typed fields.** `date` is a string (`"2024"`). Country `longitude` and `latitude` are strings (`"-47.9292"`) or `""`. Error `id` values are strings. `source.id` and `topics[].id` are quoted numbers. Every Metadata API value is a string, years included. Envelope numbers are strings on several endpoints (see the type table above). → [01](01_countries_and_classifications.md), [03](03_indicator_and_source_metadata.md), [04](04_pagination_and_envelope.md)
- **`value` mixes int and float tokens within one indicator.** USA GDP comes back as int tokens (`29298013000000`, which exceeds int32); other countries' GDP comes back as floats. Gini IND 2023 is the int `23`. Values have at most 15 significant digits, so DOUBLE is exact. `decimal` is a display hint, **not** the precision of `value`: life expectancy has `decimal: 0` but values such as `82.1080487804878`. → [02](02_indicator_observations.md)
- **Empty strings, not null.** `unit` and `obs_status` are always `""` in scope. `adminregion.*` is `""` for all 86 HIC economies. `capitalCity`, `longitude` and `latitude` are `""` for aggregates and a few economies. `lendingType.id` is `""` for aggregates. Apply `NULLIF(x,'')` in staging, and never read `""` as missing data. → [01](01_countries_and_classifications.md), [02](02_indicator_observations.md)
- **Optional keys.** Only `indicator`, `country`, `countryiso3code`, `date` and `value` are guaranteed. `unit` is absent in `mrnev` bodies. `"scale": ""` appears on null rows of multi-indicator calls. `footnote` appears or disappears depending on cache state, regardless of the `footnote` parameter (Contradictions #4). → [02](02_indicator_observations.md), [03](03_indicator_and_source_metadata.md)
- **Income classification is current-only.** `/country` has no year dimension, and `?date=` is silently ignored (the body is byte-identical). WDI `IncomeGroup` metadata is also current-only. The current classification is the FY2027 one (released 1 July 2026, based on 2025 GNI) [documented, 01]; that the API holds exactly FY27 is an inference. WB's own income aggregates apply **current** membership to every year [observed, 01: the 2018 LIC/LMC/UMC sums match exactly]. Historical, as-of-year groups exist only in the OGHIST Excel file. See decision D1. → [01](01_countries_and_classifications.md), [03](03_indicator_and_source_metadata.md)
- **Aggregates are mixed into country lists.** The full `/country` list is 296 rows: 217 economies plus 79 aggregates. Use `region.id == "NA"` as the single aggregate test; `lendingType.id` uses `""` instead. Group ids (HIC, UMC, LCN, IBD…) are also valid "country" codes. LMY and MIC are composite income levels; the atomic ones are LIC < LMC < UMC < HIC, and you have to add your own `income_rank` because the API has no ordering. Aggregate observation rows have `countryiso3code: ""`, so map them via `incomeLevel.iso2code` (XD/XT/XN/XM). The API lists 86 HIC economies, while the WB page says 87; the missing one is very likely Taiwan, China. → [01](01_countries_and_classifications.md), [04](04_pagination_and_envelope.md)
- **Inconsistent key names.** Top-level `iso2Code` versus nested `iso2code`. `/region` records use `code`/`name` (and `id` is a numeric string or `""`), while nested objects use `id`/`value`. Map fields explicitly for each endpoint. → [01](01_countries_and_classifications.md)
- **Trailing spaces.** `"Latin America & Caribbean "` (BRA, MEX), `"Sub-Saharan Africa "` (ZAF), topic `"Health "`. Keep the raw values and trim in staging. → [01](01_countries_and_classifications.md), [03](03_indicator_and_source_metadata.md)
- **Use the Metadata API for units.** `/indicator.unit` is `""`, so take `unit_of_measure` from Metadata API `Unitofmeasure`. Metatype keys are sparse (e.g. NY.GDP.PCAP.CD has no `Limitationsandexceptions`), so land them long and pivot in dbt. The Metadata API `total` counts metatype entries, not series. → [03](03_indicator_and_source_metadata.md)
- **`lastupdated` is a WDI-wide release date.** It is the same for all four indicators and for `source/2`. There is no per-observation revision timestamp, and no ETag. → [03](03_indicator_and_source_metadata.md)
- **`sourceid` is `null`** on multi-indicator calls and on zero-match responses, so the column must be nullable text. → [02](02_indicator_observations.md), [04](04_pagination_and_envelope.md)
- **2025 is already published** for POP, GDP and GDP per capita. The `date=2018:2024` filter does real work, and `mrv` would leak 2025 into the result. → [02](02_indicator_observations.md), [04](04_pagination_and_envelope.md)
- **The date filter can be silently ignored.** If the whole requested range lies outside the available years (`1900:1901`, `2030:2031`), the API returns **all** years. A partly overlapping range is clipped. A single out-of-range year gives the no-data envelope. → [04](04_pagination_and_envelope.md), [05](05_errors_and_resilience.md)
- **"No data" can hide a wrong code.** ISO2 `XX` on the data endpoint returns the no-data envelope rather than an error (other unknown codes return 120). Use ISO3 and run the pre-flight. → [05](05_errors_and_resilience.md)
- **Row order** is ISO3 ascending, then `date` descending, regardless of request order. Sorting is documented as unsupported, so never rely on it. → [02](02_indicator_observations.md), [04](04_pagination_and_envelope.md)
- **The lending-type member filter returns every economy twice** (IBD 134 rows for 67 economies). It is not used here, but anyone who uses it must dedupe. → [01](01_countries_and_classifications.md)

---

## Coverage snapshot (2026-10-14, WDI `lastupdated` 2026-10-08)

Source: the 4 golden fixtures (R8–R11). The counts were recomputed for this README with a left join onto the 9 × 7 grid for each indicator. Each cell below is **present / reported_null / not_returned** out of 7 years.

| Indicator | BRA | CAN | DEU | GBR | IND | JPN | MEX | USA | ZAF | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| SP.POP.TOTL | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 63/0/0 |
| NY.GDP.MKTP.CD | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 63/0/0 |
| NY.GDP.PCAP.CD | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 63/0/0 |
| SP.DYN.LE00.IN | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 7/0/0 | 63/0/0 |
| **All** | | | | | | | | | | **252 / 0 / 0** |

**Null cells in scope: none. Absent cells in scope: none.** Every golden fixture has `total: 63`, `pages: 1` and no duplicate keys [observed, 02; confirmed by the verifier from origin].

Because nothing is missing today, the missing-data code paths have to be tested with these fixtures:
- **Reported null:** `samples/observations/date_range_beyond_latest__SP.DYN.LE00.IN__9_countries__2023_2026.json` (LE 2025 is `null` for all 9 countries), `samples/observations/verify__null_rows_single_indicator_source2__SP.DYN.LE00.IN__BRA_USA__2024_2026.json` (the recommended call shape), and `samples/observations/missing_data_example__SI.POV.GINI__9_countries__2018_2024.json` (25 in-range nulls).
- **Not returned:** 2026 rows are absent in the same LE fixture (27 rows instead of 36).
- **Adjacent facts outside the 252-cell scope:** two values carry a footnote, DEU SP.POP.TOTL 2022 and 2023 with `"Break in series."` (`samples/metadata/data_4_indicators_9_countries_2018_2024_footnote_y_source2.json`). 2025 is present for POP and both GDP series and null for LE.

---

## Contradictions between area docs and how they were resolved

None of these is hidden in the plan above. Where the evidence still disagrees, it says so.

| # | Topic | What the docs say | Resolution |
|---|---|---|---|
| 1 | Read timeout | 01: ≥ 30 s. 05: read 60 s plus a 90 s deadline. 02/03: 60–90 s. 04: ≥ 90 s | **90 s read.** Maximum observed latency was 39.5 s (02 V9). Origin times of about 32–33 s made 30 s clients time out (03 V3, 05 #34). 60 s covers every observation so far, but with little margin. |
| 2 | Reversed date range (`2024:2018`) | 02: normalised, byte-identical to `2018:2024` (#40, V15). 05: HTTP 400 WCF page at 01:12Z | **Both were observed.** 05's own re-check at 01:28Z got 200 with the normalised rows. Treat it as normalised, but allow one retry on a WCF 400. The pipeline never sends a reversed range anyway (validate start ≤ end locally). |
| 3 | Unknown or cache-busting params | 02 V24: an unknown param was served a cached copy, so it is "not a cache-buster". 03 V6/V7: `cb=` forced a fresh origin render. 04: untested. 05: ignored, new CDN key, don't use | **Synthesis check T5** (`samples/synthesis/obs_NY.GDP.MKTP.CD__golden_url_plus_cachebuster.json`): `cb=` on the golden GDP URL gave a Cloudflare MISS with `max-age=86400`, `last-modified` = `date` **and a charset** in `Content-Type`, i.e. a fresh render; the body was byte-identical to the fixture. So it **usually** bypasses both cache layers, and 02 V24 shows it **does not always** do so (that response took the plain-`application/json` path). **Critic:** one more of each (02 C4 fresh and correct; 02 C7 inner path) and a fresh cache-busted R0 (03 C2). Not used on first attempts; used on retries of HTTP-200 bodies (#18). |
| 4 | Does the `footnote` param control the `footnote` key? | 02: the key appears only with `footnote=y`. 03: the key is absent without it, so always send `footnote=y` for a stable schema. 03 verifier: `footnote=n` still returned footnotes | **New finding [synthesis, offline]:** `samples/errors/burst_01_nine_countries_pop.json` was requested **without** `footnote` at 01:15:20. It is byte-identical (sha256 `0e4577f4…`) to the `footnote=y` body that area 03 fetched at 01:12:35. It was served as plain `application/json` with no charset, the same signature as the wrong-body anomalies. Live checks T1 and T3 (`samples/synthesis/`) showed the parameter honoured in that particular instance. **Critic live repro [observed by critic, 02 C1–C4]** on a fresh URL in the recommended call shape: the URL without `footnote` returned the `footnote=y` body 1 s after it was fetched (plain `application/json`), and an exact retry was a Cloudflare HIT with the same body. **Conclusion:** neither "stable schema" claim holds, because whether `footnote` is present depends on cache state. Treat the key as optional whatever you send (decision D4). |
| 5 | `source=2` on single-indicator calls | 02: send it to pin WDI | 04 #50/V27: `source=11` is **ignored** on single-indicator calls (`sourceid` stays `"2"`). Synthesis T4: the body without `source` is byte-identical. So the parameter pins nothing. Keep it so URLs match the fixtures, and rely on asserting `sourceid == "2"`. |
| 6 | How stale can a cached response be? | 04: up to about 24 h, on a HIT or a MISS. 05: Cloudflare served an entry past `max-age`, about 25 h old | Assume **about 25 h or more**. Version the data by `lastupdated` and record the cache headers. |
| 7 | `classify()` in 05 treats every top-level JSON object as a gateway error | 01/03: Metadata API and `/sources/2/concepts` success bodies **are** objects | Classify objects by their keys: `statusCode` means a gateway error; `source` means a Metadata API success (contract §2). |
| 8 | `per_page` on the country call | 01: 50. 04: 1000 with `page=1`. 05: 100 | **50**, because `samples/countries/country_9_selected.json` is that exact URL. The bodies differ only in the echoed `"per_page"` string. |
| 9 | Envelope keys always include `sourceid`/`lastupdated` | 05 (original) | Corrected by 05's verifier: true only for observation responses. `/country` and `/indicator` responses have neither key. |
| 10 | The indicator pre-flight catches archived indicators | 05 (original) | Corrected by 05's verifier: `/indicator/IC.BUS.EASE.XQ` returns normal metadata with `source.id "57"`. Error 175 appears only on the data endpoint. Hence the `source.id == "2"` assertion. |
| 11 | A future missing cell: null row or absent row? | 04 open question 5: unknown | **Partly answered by 02.** In-range gaps arrive as null rows (Gini 25/63, LE 2025), and years past the time axis are absent. Whether an in-range year could ever be absent is still unknown, so build the grid either way. |
| 12 | Metadata API `per_page` is "5000 regardless" | 03 (original) | Corrected by 03's verifier: 5000 is only the default, `per_page` is honoured, and it splits variables across pages. Never send it. |
| 13 | `_response_sha256` as a "nothing changed" check | 03 (re-renders were byte-identical within a release) | **Not reliable** given #4: the same data can arrive as different bytes. Use the hash for lineage and raw-file dedupe. Detect change from `lastupdated` plus parsed values. |
| 14 | Latency "0.07–1.5 s" | 01 (original) | Corrected by 01's verifier and every other area: a 10.7–39.5 s cold miss is normal. |
| 15 | What to do with rows outside the requested years | 05: drop them and count them in the run log. 04: fail the run | **Fail.** With a fixed 2018:2024 request, an out-of-range row can only come from an ignored date filter or a substituted body. Either one makes the whole response untrustworthy, and dropping rows would hide it. |
| 16 | Docs vs live behaviour (recorded rather than resolved) | Docs map error 120→404, 111→404, 150→400, 140→400, 110→"API Version not found", and v1 paths→"Resource not found". The docs say `footnote=n` removes footnotes, and they imply (but never say) that `source` is required for multi-indicator calls | Live: 120/111/150/175 return **HTTP 200** with an error body. An unknown path returns 404 HTML. A wrong version returns a 404 JSON object. v1 paths return a 301. `footnote=n` returned footnotes. A multi-indicator call without `source` returns error 120. **The design follows the live behaviour.** |
| 17 | Do `last-modified` / `max-age` show whether a body is a cached copy? | 04 and this README (§6): `last-modified` ≠ `date` or `max-age` < 86400 marks an old copy | **Partly** [observed by critic]. They mark the Cloudflare and origin output-cache copies. The inner path (plain `application/json`, no charset, sub-second) shows `last-modified` = `date` and `max-age=86400` like a fresh render, yet served wrong bodies (02 C2, V18, V19; 05 `burst_01`). The missing charset is the only marker. |
| 18 | How to retry after a validation failure or a `lastupdated` mismatch | 02: "retry with backoff". 04: restart from page 1. 05/README: retry once | **Retry with a fresh cache-buster token per attempt** [observed by critic, 02 C1–C4]: the same-URL retry was a Cloudflare HIT with the identical wrong body; the cache-busted retry returned the correct body. Not guaranteed (02 C7 came from the inner cache), so validate each attempt and fail after the budget. |
| 19 | Out-of-range rows in the sample index and in 05 | 05 F11 and earlier index text: "drop/flag" | Aligned with #15: **fail**. The index's "Suggested test use" and 05 were updated by the critic. |
| 20 | Small cross-file facts | 01: `per_page` max unknown, timeout ≥ 30 s, 39 verification calls. 02: no non-empty footnote, `footnote` only with `footnote=y`, `lastupdated` never null, `per_page` max not probed. 03: "~2 data calls", "always send `footnote=y`", body hash as "nothing changed". 04: cache-busting untested, country URL `per_page=1000`. 05: `read=60 s`, country `per_page=100` | Corrected in place by the critic with pointers: max `per_page` 32767; timeout 90 s; 41 verification calls; 2 non-empty footnotes in scope (DEU POP 2022/2023); `footnote` key optional; `lastupdated`/`sourceid`/rows null in the zero-match envelope; 4 data calls; #4/#13 cross-references; canonical URLs are R0–R11 here. |

---

## Sample index

Every body file under `samples/` is listed below. Every file has a `<name>.meta.json` sidecar (`url`, `http_status`, `content_type`, `fetched_at_utc`, `notes`), and every file in `errors/` and `synthesis/`, plus every sample added by the completeness critic (names starting `critic__`, the three new `income_aggregates_*` files and the three new `*__golden_url_plus_footnote_y` files), also has a `<name>.headers.txt` with the raw response headers (cookie values redacted). Two `errors/` entries are sidecars only, with no body. **Golden** marks a fixture that equals a call-plan URL byte-for-byte, for an end-to-end offline replay of one run. The "What it shows" text is condensed from each sidecar's `notes`.

`samples/countries/country_9_selected_es.json` holds 3 countries (BRA, DEU, ZAF), despite its name.

#### `samples/countries/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `country_9_selected.json` | `/country/{9c}?format=json&per_page=50` | 200 | Our 9 target countries by ISO3, semicolon-separated, uppercase. | **Golden R2**: country dimension, 9 rows |
| `country_9_selected_per_page_50_page_1.json` | `/country/{9c}?format=json&per_page=50&page=1` | 200 | The pipeline's canonical country URL (explicit `page=1`). Recorded 2026-10-14; byte-identical to `country_9_selected.json`. | Offline fixture for the pipeline's country call |
| `country_9_selected_es.json` | `/es/country/BRA;DEU;ZAF?format=json` | 200 | Spanish language prefix /es/ - translated names/labels; ids unchanged. | Negative: never use a language prefix (labels and capitalCity change). Holds 3 countries despite the name |
| `country_BRA_with_date_param.json` | `/country/BRA?format=json&date=2018` | 200 | Country endpoint with date=2018: tests whether a year can select a historical income classification. | Assert `/country` ignores `date` (no historical classification) |
| `country_aggregates_WLD_HIC_EUU.json` | `/country/WLD;HIC;EUU?format=json` | 200 | Aggregates requested via country endpoint: shows the region/incomeLevel 'Aggregates' markers and empty-string fields. | Aggregate filter: `region.id == "NA"`; empty-string sentinels |
| `country_all_page1.json` | `/country?format=json&per_page=10&page=1` | 200 | Full country list, page 1 of N with per_page=10. Shows aggregates mixed in with economies (e.g. AFE, AFW, ARB). | Aggregates mixed into the country list |
| `country_all_per_page_100_page1.json` | `/country?format=json&per_page=100&page=1` | 200 | Full country list paged at per_page=100 (page 1 of 3). Pages 1-3 together form a complete multi-page fixture: 296 records = 217 economies + 79 aggregates. | Paginator: pages concatenate to the single-page result |
| `country_all_per_page_100_page2.json` | `/country?format=json&per_page=100&page=2` | 200 | Full country list paged at per_page=100 (page 2 of 3). Pages 1-3 together form a complete multi-page fixture: 296 records = 217 economies + 79 aggregates. | Paginator: pages concatenate to the single-page result |
| `country_all_per_page_100_page3.json` | `/country?format=json&per_page=100&page=3` | 200 | Full country list paged at per_page=100 (page 3 of 3). Pages 1-3 together form a complete multi-page fixture: 296 records = 217 economies + 79 aggregates. | Paginator: pages concatenate to the single-page result |
| `country_invalid_code_XXX.json` | `/country/XXX?format=json` | 200 | Unknown country code; documents the error envelope for invalid country. | classify() -> api_error 120 (fatal, no retry) |
| `country_mixed_iso2_iso3_reordered.json` | `/country/zaf;US;de;BRA?format=json` | 200 | Mixed ISO2/ISO3 and mixed case, deliberately non-alphabetical request order, to test acceptance and ordering. | Ordering: response sorted by id, not request order |
| `country_no_format_xml_default.xml` | `/country/BRA` | 200 | No format param: API defaults to XML (content-type text/xml). Body saved byte-for-byte as .xml. | classify() -> non_json (fatal) |
| `country_page_beyond_end.json` | `/country?format=json&per_page=10&page=31` | 200 | Page beyond the last page (pages=30 at per_page=10). | Paginator stop: `page > pages` returns `[]` |
| `country_per_page_invalid.json` | `/country?format=json&per_page=abc` | 200 | Non-numeric per_page value. | classify() -> api_error 120 (fatal, no retry) |
| `country_single_BRA.json` | `/country/BRA?format=json` | 200 | Single country by ISO3 uppercase. | Single-country parse |
| `country_single_br_iso2_lower.json` | `/country/br?format=json` | 200 | Single country by ISO2 lowercase (as in official docs example). | ISO2/lower-case accepted (byte-identical to ISO3) |
| `country_valid_plus_invalid.json` | `/country/BRA;XXX?format=json` | 200 | One valid + one invalid code in the same request. | classify() -> api_error 120 (fatal, no retry) |
| `error_unknown_endpoint.html` | `/countryz?format=json` | 404 | Unknown endpoint path: real HTTP 404 with an HTML (IIS-style) body starting with a UTF-8 BOM, NOT the JSON error envelope. Saved byte-for-byte as .html. | 404 HTML -> fatal; BOM-tolerant decode |
| `error_unsupported_language.json` | `/xx/country/BRA?format=json` | 200 | Unsupported language prefix 'xx'. | classify() -> api_error 150 (fatal) |
| `incomeLevel.json` | `/incomeLevel?format=json` | 200 | Reference list of income levels (camelCase path). | **Golden R1**: income-level reference (string envelope) |
| `incomeLevel_HIC_country.json` | `/incomeLevel/HIC/country?format=json&per_page=100` | 200 | Countries currently classified High income (URL-based query). Current classification only. | Reference only (current HIC membership, 86 economies) |
| `incomeLevel_UMC_single.json` | `/incomeLevel/UMC?format=json` | 200 | Single income level definition. | Reference only |
| `income_aggregates_SP.POP.TOTL.json` | `/country/HIC;UMC;LMC;LIC/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` | 200 | Income-group aggregates (HIC, UMC, LMC, LIC) queried as countries in the indicator endpoint, 2018-2024. Optional benchmark for income-group comparisons. NOTE: aggregates are computed by WB on CURRENT group membership. | **Golden R12** (optional benchmark): aggregate rows with `countryiso3code ""` |
| `indicator_BRA_join_key_demo.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024` | 200 | Join-key demo: indicator row country.id is ISO2 (BR) and countryiso3code is ISO3 (BRA) = country.id in country endpoint. | Join-key test: `country.id` ISO2 vs `countryiso3code` ISO3 |
| `indicator_all_SP.POP.TOTL_2018_membership_check.json` | `/country/all/indicator/SP.POP.TOTL?format=json&date=2018&per_page=400` | 200 | country/all for SP.POP.TOTL 2018. | Evidence that WB aggregates use current membership; aggregate filtering |
| `lendingType.json` | `/lendingType?format=json` | 200 | Reference list of lending types (singular camelCase path). | **Golden R1c** (optional): lending-type labels |
| `lendingType_IBD_country_duplicates.json` | `/lendingType/IBD/country?format=json&per_page=200` | 200 | Verifier sample. Lending-type filter returns every IBRD economy TWICE (identical records, adjacent): envelope total=134 but only 67 unique ids. | Negative: lending-type filter duplicates rows (dedupe by id) |
| `region.json` | `/region?format=json&per_page=100` | 200 | Reference list of regions (all region-type aggregates). | **Golden R1b** (optional): region labels; `code`/`name` shape |
| `region_LCN_single.json` | `/region/LCN?format=json` | 200 | Single region definition by code. | Trailing-space trim in staging |
| `sources2_country_BRA_metadata.json` | `/sources/2/country/BRA/metadata?format=json` | 200 | WDI (source 2) country-level metadata for BRA; checked for an income group field and any year dimension. | Object-shaped envelope parse; IncomeGroup has no year |
| `income_aggregates_NY.GDP.MKTP.CD.json` | `/country/HIC;UMC;LMC;LIC/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=100` | 200 | Critic. 28 rows, no nulls, 26 float + 2 int tokens; cold MISS took 45.2 s (slowest seen). | **Golden R13** (optional benchmark) |
| `income_aggregates_NY.GDP.PCAP.CD.json` | `/country/HIC;UMC;LMC;LIC/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=100` | 200 | Critic. 28 rows, no nulls, all floats, `decimal` 1. | **Golden R14** (optional benchmark) |
| `income_aggregates_SP.DYN.LE00.IN.json` | `/country/HIC;UMC;LMC;LIC/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=100` | 200 | Critic. 28 rows, no nulls, all floats. | **Golden R15** (optional benchmark) |
| `critic__source_57_wdi_archives_concepts.json` | `/sources/57/concepts?format=json` | 200 | Critic. WDI Database Archives concepts include `Version`. | Reference only (D1 research) |
| `critic__source_57_country_BRA_metadata.json` | `/sources/57/country/BRA/metadata?format=json` | 200 | Critic. Archive country metadata has `IncomeGroup`, no version attribute. | Reference only (D1 research) |
| `critic__source_57_version_list.json` | `/sources/57/version/data?format=json&per_page=1000` | 200 | Critic. 142 archived WDI versions, 198904 … 202607. | Reference only (D1 research) |
| `critic__source_57_country_VEN_version_202307_metadata.json` | `/sources/57/country/VEN/version/202307/metadata?format=json` | 200 | Critic. Returns the current `IncomeGroup`; byte-identical to the no-version call, so `/version/` is ignored. | Evidence for D1: no API route to as-of-year income groups |
| `critic__source_57_country_VEN_metadata_no_version.json` | `/sources/57/country/VEN/metadata?format=json` | 200 | Critic. Control for the row above (same sha256). | Evidence for D1 |

#### `samples/errors/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `accept_json_header_without_format_param.xml` | `/country/BRA/indicator/SP.POP.TOTL?date=2023:2024` | 200 | Accept: application/json header without format param -> still XML (text/xml). Accept header is ignored; the CDN served the cached XML (HIT). Always send format=json. | classify() -> non_json (fatal) |
| `archived_indicator_doing_business.json` | `/country/BRA/indicator/IC.BUS.EASE.XQ?format=json&date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. Archived Doing Business indicator IC.BUS.EASE.XQ -> id '175', key 'Invalid format', value 'The indicator was not found. | classify() -> api_error 175 (fatal, clear message) |
| `bad_format_value_yaml.xml` | `/country/BRA/indicator/SP.POP.TOTL?format=yaml&date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. format=yaml -> text/xml <wb:error><wb:message id="111" key="Invalid format">The requested response format was not found.</wb:message></wb:error>. Docs say response code 404; live is 200. | classify() -> non_json (fatal) |
| `baseline_valid_bra_pop_2018_2024.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | Positive control. HTTP 200, 2-element array [meta, rows]; 7 rows (2018-2024). time_total 0.13s, cf-cache-status MISS but cache-control max-age=64703 and last-modified 2026-10-13T19:10:06Z, i.e. | classify() -> ok (positive control) |
| `burst_01_nine_countries_pop.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` | 200 | Exact pipeline-style call (9 countries, SP.POP.TOTL, 2018:2024, per_page=100) -> 63 rows, 1 page. First of a 10-call sequential burst: call 1 MISS (0.17s), calls 2-10 HIT (0.06-0.16s), all byte-identical (sha256 0e4577f4...). | **Body substitution**: no `footnote` param sent, yet body carries `footnote` keys (byte-identical to metadata/data_sp_pop_totl_9_countries_2018_2024_footnote_y.json). Loader must treat `footnote` as optional |
| `date_range_1800_1801_filter_ignored.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=1800:1801` | 200 | SURPRISE: date=1800:1801 (years before any data) -> HTTP 200 with the FULL series (total 66, pages 2, years 2025..1960); the date filter was silently ignored. Only page 1 (50 rows) saved. | Validator: rows outside requested years -> reject the response (filter silently ignored; Contradictions #15) |
| `empty_segment_in_country_list.json` | `/country/BRA;;USA/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. BRA;;USA (empty list item) -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `format_jsonp_without_prefix.json` | `/country/BRA/indicator/SP.POP.TOTL?format=jsonp&date=2023:2024` | 200 | format=jsonp without prefix -> plain JSON returned (no wrapper), Content-Type application/json without charset. | Reference only |
| `format_uppercase_JSON.json` | `/country/BRA/indicator/SP.POP.TOTL?format=JSON&date=2023:2024` | 200 | format=JSON (upper case) accepted -> JSON body. | Reference only |
| `future_year_2030.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2030` | 200 | date=2030 -> HTTP 200 'no data' envelope: [{page:0,pages:0,per_page:0,total:0,sourceid:null,lastupdated:null}, null]. Second element is JSON null (not []), meta numbers are 0, sourceid/lastupdated null. | classify() -> no_data (`[meta, null]`) |
| `http_scheme_no_tls.html` | `http://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 302 | http:// -> HTTP/1.1 302 Found from Cloudflare, Location: same URL on https://. Body is a Cloudflare HTML stub. Clients that follow redirects will work but should use https directly. | Redirect -> fatal (allow_redirects=False) |
| `invalid_api_version_v9.json` | `https://api.worldbank.org/v9/country/BRA?format=json` | 404 | /v9/... -> HTTP 404, Content-Type application/json, body is a JSON OBJECT {"statusCode": 404, "message": "Resource not found"} - a different shape from the wb message envelope (likely a gateway layer). cf-cache-status DYNAMIC. | classify() -> gateway_error (JSON object, fatal) |
| `invalid_country_xxx_data.json` | `/country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. Invalid ISO3 country XXX on the data endpoint -> [{"message":[{id:"120",key:"Invalid value",...}]}]. Error id is a quoted string. Does not say WHICH parameter was invalid. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_country_xxx_metadata.json` | `/country/XXX?format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /country/XXX -> message id 120. cf-cache-status EXPIRED (Cloudflare had a stale copy). | classify() -> api_error 120 (fatal, no retry) |
| `invalid_country_xxx_xml_error.xml` | `/country/XXX/indicator/SP.POP.TOTL?date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200 in XML (no format param): <wb:error xmlns:wb=...><wb:message id="120" key="Invalid value">...</wb:message></wb:error>. | classify() -> non_json (fatal) |
| `invalid_date_abc.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=abc` | 200 | ERROR PAYLOAD WITH HTTP 200. date=abc -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_date_end_2018_abc.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:abc` | 200 | ERROR PAYLOAD WITH HTTP 200. date=2018:abc -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_income_level_filter_zzz.json` | `/country?incomeLevel=ZZZ&format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /country?incomeLevel=ZZZ -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_indicator_data.json` | `/country/BRA/indicator/NOT.A.CODE?format=json&date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. Unknown indicator NOT.A.CODE on data endpoint -> id 120 'Invalid value' (byte-identical to the invalid-country body; the two cases cannot be told apart from the body). | classify() -> api_error 120 (fatal, no retry) |
| `invalid_indicator_metadata.json` | `/indicator/NOT.A.CODE?format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /indicator/NOT.A.CODE -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_iso2_country_xx_data_empty_envelope.json` | `/country/XX/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | SURPRISE: unknown ISO2 code XX on the DATA endpoint does NOT error; it returns the 'no data' envelope [{page:0,...,total:0,sourceid:null,lastupdated:null}, null] (byte-identical to future_year_2030). | classify() -> no_data, although the code is wrong: pre-validate codes |
| `invalid_iso2_country_xx_metadata.json` | `/country/XX?format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /country/XX -> id 120. Served from Cloudflare cache (HIT, age 64145s, last-modified 2026-10-13T00:20:51Z) - error responses are cached at the edge for ~a day. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_iso3_country_qqq_data.json` | `/country/QQQ/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. Second invalid ISO3 (QQQ) on data endpoint -> id 120, confirms XXX behavior. | classify() -> api_error 120 (fatal, no retry) |
| `invalid_source_9999.json` | `/country/BRA/indicator/SP.POP.TOTL?source=9999&format=json&date=2023:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. source=9999 -> message array with TWO id-120 entries; message is a list and can hold more than one entry. | classify() -> api_error with 2 messages |
| `long_country_list_15_codes.json` | `/country/{9c};CHN;FRA;ITA;AUS;KOR;ARG/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=200` | 200 | 15 ISO3 codes in one path segment (our 9 + CHN;FRA;ITA;AUS;KOR;ARG), one indicator, 2018:2024, per_page=200 -> HTTP 200, total 105 (=15x7), 1 page. No URL-length issue. 4.8s (cache MISS). | Parse a larger success envelope (105 rows) |
| `lowercase_codes_bra_sp_pop_totl.json` | `/country/bra/indicator/sp.pop.totl?format=json&date=2018:2024` | 200 | Lower-case country and indicator codes accepted; body byte-identical to the upper-case baseline (sha256 24c384c0...). Separate CDN cache entry (MISS). | Case-insensitive codes (byte-identical to baseline) |
| `missing_format_param_default_xml.xml` | `/country/BRA/indicator/SP.POP.TOTL?date=2023:2024` | 200 | No format param -> HTTP 200 text/xml (XML is the default). Root <wb:data page=... total=...> with attributes; empty elements serialized as <wb:unit />. | classify() -> non_json (fatal) |
| `mixed_valid_invalid_countries_bra_xxx.json` | `/country/BRA;XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. BRA;XXX -> whole request fails with id 120 (no partial success: BRA rows are NOT returned). One bad code poisons a batch. | classify() -> api_error 120 (fatal, no retry) |
| `mixed_valid_invalid_country_metadata_bra_xxx.json` | `/country/BRA;XXX?format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /country/BRA;XXX metadata -> id 120, whole request fails (no partial). Served from CDN cache (HIT). | classify() -> api_error 120 (fatal, no retry) |
| `multi_indicator_valid_plus_invalid_source2.json` | `/country/BRA/indicator/SP.POP.TOTL;NOT.A.CODE?source=2&format=json&date=2023:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. SP.POP.TOTL;NOT.A.CODE with source=2 -> id 120 (whole request fails; no partial result). | classify() -> api_error 120 (fatal, no retry) |
| `multi_indicator_valid_source2_control.json` | `/country/BRA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?source=2&format=json&date=2023:2024` | 200 | Positive control: SP.POP.TOTL;NY.GDP.MKTP.CD with source=2 -> HTTP 200, 4 rows. Note meta.sourceid is null here (string '2' on single-indicator calls). Took 11.0s (cache MISS). | Multi-indicator: `sourceid` null |
| `multi_indicator_without_source.json` | `/country/BRA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2023:2024` | 200 | ERROR PAYLOAD WITH HTTP 200. Two indicators separated by ';' WITHOUT source= -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `page_abc.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=5&page=abc` | 200 | ERROR PAYLOAD WITH HTTP 200. page=abc -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `page_beyond_last_page_999.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&page=999` | 200 | page=999 (only 1 page exists) -> HTTP 200 meta {page:999,pages:1,total:7} with an EMPTY rows list []. Not an error. Took 11.2s (cache MISS). | Paginator stop: `page > pages` returns `[]` |
| `page_zero.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=5&page=0` | 200 | ERROR PAYLOAD WITH HTTP 200. page=0 -> id 120 (pages are 1-based). | classify() -> api_error 120 (fatal, no retry) |
| `path_segment_299_chars_retry_cdn_hit.json` | `/country/BRA×75/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | 200 | Retry of the timed-out 299-char segment request (BRA x75) -> HTTP 200 in 0.12s, cf-cache-status HIT age 90: the CDN cached the response the origin produced after our client gave up. Duplicate codes are de-duplicated (total 1). | Retry-after-timeout succeeds (pair with the client_timeout meta) |
| `path_segment_over_1500_chars.html` | `/country/BRA×400/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | 400 | Path segment of 1,599 chars (BRA repeated 400x) -> HTTP 400 text/html; charset=us-ascii 'Bad Request - Invalid URL' (IIS HTTP.sys style page). Docs state a 1,500 character limit between slashes; consistent. cf-cache-status BYPASS. | HTTP.sys 400 -> fatal |
| `per_page_100000.html` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100000` | 400 | per_page=100000 -> HTTP 400 text/html WCF 'Request Error' page (same body as reversed date range). 6.0s. Not cached (BYPASS). | WCF 400 page -> fatal |
| `per_page_32767.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024&per_page=32767` | 200 | per_page=32767 accepted -> HTTP 200, meta.per_page 32767. 6.4s (cache MISS). | per_page upper bound accepted (config validation) |
| `per_page_32768.html` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2023:2024&per_page=32768` | 400 | per_page=32768 -> HTTP 400 text/html WCF 'Request Error' page. 10.6s. Upper bound of per_page is 32767 (Int16 max). | WCF 400 page -> fatal (deterministic); BOM-tolerant |
| `per_page_abc.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=abc` | 200 | ERROR PAYLOAD WITH HTTP 200. per_page=abc -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `per_page_negative_one.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=-1` | 200 | ERROR PAYLOAD WITH HTTP 200. per_page=-1 -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `per_page_zero.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=0` | 200 | ERROR PAYLOAD WITH HTTP 200. per_page=0 -> id 120. | classify() -> api_error 120 (fatal, no retry) |
| `percent_encoded_colon_in_date.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018%3A2024` | 200 | date=2018%3A2024 (percent-encoded colon, as an HTTP library would produce via params=) accepted; body byte-identical to baseline. | HTTP-library encoding of `:` accepted |
| `percent_encoded_semicolon_in_country_list.json` | `/country/BRA%3BUSA/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | BRA%3BUSA (percent-encoded semicolon in path) accepted -> 4 rows for BRA and USA. | HTTP-library encoding of `;` accepted |
| `retired_v1_endpoint_no_version.txt` | `https://api.worldbank.org/countries/BRA?format=json` | 301 | Unversioned (v1-style) path /countries/BRA -> HTTP 301 to /v2/countries/BRA, no Content-Type header, plain-text body explaining v1 was discontinued on June 19, 2020. | Redirect -> fatal (allow_redirects=False) |
| `reversed_date_range_2024_2018.html` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024:2018` | 400 | HTTP 400, Content-Type text/html, WCF 'Request Error' XHTML page starting with a UTF-8 BOM (EF BB BF). Took 4.1s. cf-cache-status BYPASS (not cached). | WCF 400 page: one bounded retry, then fatal (was transient) |
| `trailing_slash_data.json` | `/country/BRA/indicator/SP.POP.TOTL/?format=json&date=2018:2024` | 200 | Trailing slash after indicator code accepted; body byte-identical to baseline. Note Content-Type here was 'application/json' (no charset) whereas most responses say 'application/json;charset=utf-8'. | Content-Type `application/json` without charset must be accepted |
| `unknown_endpoint_path.html` | `/notanendpoint?format=json` | 404 | HTTP 404, text/html; charset=UTF-8, WCF XHTML page (BOM-prefixed) with text 'Endpoint not found.' Docs list code 140 as response code 400 with a wb error message; live behavior is a 404 HTML page, no wb error envelope. | 404 HTML -> fatal |
| `unknown_query_param_ignored.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024&foo=bar` | 200 | Unknown query param foo=bar silently ignored; body byte-identical to baseline. Content-Type 'application/json' without charset on this call. | Unknown params are silently ignored |
| `unsupported_language_prefix_xx.json` | `/xx/country/BRA?format=json` | 200 | ERROR PAYLOAD WITH HTTP 200. /v2/xx/country/BRA -> id 150 'Language is not yet supported in the API'. Matches docs (code 150) except docs list response code 400. | classify() -> api_error 150 (fatal) |
| `verify_archived_indicator_metadata_returns_200.json` | `/indicator/IC.BUS.EASE.XQ?format=json` | 200 | Indicator METADATA for the archived IC.BUS.EASE.XQ returns HTTP 200 with a normal record (source id "57" 'WDI Database Archives'), not error 175. The /indicator pre-flight cannot detect archived indicators. | Pre-flight must assert `source.id == "2"` (archived indicator looks valid here) |
| `verify_country_metadata_nine_codes.json` | `/country/{9c}?format=json&per_page=100` | 200 | The exact pipeline country call: 9 rows, ids BRA..ZAF. meta is {page:1, pages:1, per_page:"100" (STRING), total:9} with NO sourceid/lastupdated keys, unlike the data endpoint meta. | Metadata envelope: `per_page` string, no `sourceid`/`lastupdated` |
| `verify_date_range_1900_1959_filter_ignored.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=1900:1959` | 200 | Range entirely before the first available year (1960): filter silently ignored, full series returned (total 66, pages 2, years 2025..1960). Only page 1 (50 rows) saved. (curl http_code/time_total/bytes: 200 0.395257 9891). | Validator: rows outside requested years -> reject the response (filter silently ignored; Contradictions #15) |
| `verify_date_range_1950_1965_partially_out_of_range.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=1950:1965` | 200 | Partially out-of-range range: correctly clipped to 1960..1965 (total 6). The filter is only ignored when the WHOLE range is outside the available years. (curl http_code/time_total/bytes: 200 0.287190 1260). | Partial overlap is clipped (no rows dropped) |
| `verify_date_range_2030_2031_filter_ignored.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2030:2031` | 200 | Range entirely after the last available year: filter silently ignored, full series returned (total 66). Contrast: single year date=2030 returns the no-data envelope. Only page 1 saved. | Validator: rows outside requested years -> reject the response (filter silently ignored; Contradictions #15) |
| `verify_date_single_year_1800_no_data.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=1800` | 200 | Single out-of-range year: HTTP 200 no-data envelope [meta0, null] (filter NOT ignored), unlike the range 1800:1801. (curl http_code/time_total/bytes: 200 0.287141 85). Saved by the adversarial verifier. | classify() -> no_data |
| `verify_invalid_iso2_country_qz_data_error_120.json` | `/country/QZ/indicator/SP.POP.TOTL?format=json&date=2023:2024` | 200 | Another unknown ISO2 code (QZ) on the DATA endpoint returns error id 120 (102 bytes, same sha256 f230e5b6...), NOT the no-data envelope that XX returns. ZZ behaved the same (id 120). The XX no-data behavior is specific to XX. | classify() -> api_error 120 (fatal, no retry) |
| `verify_reversed_date_range_2022_2019_usa_200.json` | `/country/USA/indicator/SP.POP.TOTL?format=json&date=2022:2019` | 200 | Fresh reversed range on another country (never requested before, cf MISS, 5.46 s): HTTP 200, total 4, rows 2022..2019. The API normalizes a reversed range rather than erroring. (curl http_code/time_total/bytes: 200 5.461366 902). | Reversed range normalised (HTTP 200) |
| `verify_reversed_date_range_2024_2018_now_200.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024:2018` | 200 | Re-check of evidence row 7 (which got HTTP 400 WCF 'Request Error' at 01:12Z). | Reversed range normalised (HTTP 200) |
| `head_request_valid.meta.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | HEAD request (curl -I) -> HTTP 200 with headers only (no body file saved). Served from Cloudflare cache: cf-cache-status HIT, age 70. No rate-limit headers. | Sidecar only (HEAD request; headers in head_request_valid.headers.txt) |
| `path_segment_299_chars_client_timeout.meta.json` | `/country/BRA×75/indicator/SP.POP.TOTL?format=json&date=2024&per_page=5` | n/a | CLIENT TIMEOUT: curl -m 30 hit 30.0s with 0 bytes received (curl exit 28), no HTTP status (http_status null) and no body. | Timeout simulation: ReadTimeout -> retry (no body was received) |

#### `samples/metadata/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `advanced_data_deu_sp_pop_totl_2023.json` | `/sources/2/country/DEU/series/SP.POP.TOTL/time/yr2023/data?format=json` | 200 | Advanced Data API (BETA): /sources/2/country/DEU/series/SP.POP.TOTL/time/yr2023/data. Object envelope with lastupdated '2026-10-08'; value is a JSON number; Time variable id 'YR2023'. | Reference only (beta API) |
| `country_incomegroup_metatype_9_countries.json` | `/sources/2/country/{9c}/metatypes/incomegroup/metadata?format=json` | 200 | Metadata API metatype filter: /sources/2/country/{iso3s}/metatypes/incomegroup/metadata. Returns IncomeGroup only (current classification, not historical). | Optional: IncomeGroup cross-check (current only) |
| `country_series_metadata_9_countries_4_indicators.json` | `/sources/2/country/{9c}/series/{4ind}/metadata?format=json` | 200 | Metadata API: /sources/2/country/{9 iso3}/series/{4 codes}/metadata. Returns three concepts: Country (9 variables incl. | **Golden R7**: Metadata API, long key/value landing (300 entries) |
| `country_series_metadata_9_countries_4_indicators_per_page_50_page_1.json` | `/sources/2/country/{9c}/series/{4ind}/metadata?format=json&per_page=50` | 200 | Verifier. The pipeline call #6 with per_page=50 returns pages 6, total 300; page 1 holds only Country BRA and part of CAN. Do not send per_page on this call (default 5000 gives 1 page). | Negative: per_page splits the R7 call into 6 pages |
| `country_series_metadata_usa_jpn_sp_pop_totl.json` | `/sources/2/country/USA;JPN/series/SP.POP.TOTL/metadata?format=json` | 200 | Documented example pattern (country usa;jpn x series SP.POP.TOTL). Country-Series variable ids are '{ISO3}~{SERIES}'. | Metadata API Country-Series concept |
| `data360_wdi_sp_pop_totl_deu_top10.json` | `https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_SP_POP_TOTL&REF_AREA=DEU&...` | 200 | Successor platform Data360 API (different host data360api.worldbank.org). Pretty-printed JSON {count, value[]}; OBS_VALUE is a quoted STRING; rows are not ordered by TIME_PERIOD; top=10 limited rows while count=66. | Reference only (successor API, different schema) |
| `data_4_indicators_9_countries_2018_2024_footnote_y_source2.json` | `/country/{9c}/indicator/{4ind}?format=json&source=2&date=2018:2024&footnote=y&per_page=300` | 200 | Full metadata-relevant scope in one data call (4 indicators x 9 countries x 2018-2024 = 252 rows, 1 page at per_page=300). | Cross-check: multi-indicator rows equal the 4 golden fixtures |
| `data_4_indicators_usa_2025_source2.json` | `/country/USA/indicator/{4ind}?format=json&source=2&date=2025` | 200 | Verifier. 2025 values exist for SP.POP.TOTL, NY.GDP.MKTP.CD (integer literal 30769700000000), NY.GDP.PCAP.CD; SP.DYN.LE00.IN 2025 row is present with value null. sourceid null (multi-indicator). | Out of scope: 2025 published for 3/4 indicators; LE 2025 null |
| `data_multi_indicator_without_source_error_120.json` | `/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2024` | 200 | Verifier. Multi-indicator data call WITHOUT source param: HTTP 200 with JSON message id 120 "Invalid value" (docs map id 120 to 404). | classify() -> api_error 120 |
| `data_sp_pop_totl_9_countries_2018_2024_footnote_y.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 200 | Observation call with footnote=y: envelope has sourceid '2' and lastupdated '2026-10-08' (same as source/2). Rows carry footnote ('' or text); only DEU 2022 and 2023 have 'Break in series.'. per_page is a JSON number here. | Footnote parsing (`Break in series.` for DEU 2022/2023) |
| `data_sp_pop_totl_deu_2022_2023_footnote_n.json` | `/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023&footnote=n` | 200 | Verifier. footnote=n still returns the footnote key with "Break in series." (V2 features doc says n removes it). Content-Type was application/json without charset, unlike other data calls. cf MISS. | **Body substitution**: footnote=n still returns footnotes |
| `data_sp_pop_totl_deu_2022_2023_no_footnote_param.json` | `/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023` | 200 | Same data without footnote param: rows have NO footnote key at all (schema differs by parameter). This cache MISS took ~9.8 s. | Rows without `footnote` key parse |
| `footnote_metadata_deu_sp_pop_totl_2022_2023.json` | `/sources/2/footnote/DEU~SP.POP.TOTL~YR2022;DEU~SP.POP.TOTL~YR2023/metadata?format=json` | 200 | Footnote metadata: /sources/2/footnote/{ISO3}~{SERIES}~YR{yyyy}/metadata; semicolon-joined keys work. Value 'Break in series.' matches the footnote field of the data call with footnote=y. | Reference only (undocumented endpoint) |
| `footnote_metadata_deu_sp_pop_totl_2023.json` | `/sources/2/footnote/DEU~SP.POP.TOTL~YR2023/metadata?format=json` | 200 | Footnote metadata single key DEU~SP.POP.TOTL~YR2023. | Reference only (undocumented endpoint) |
| `footnote_metadata_usa_sp_pop_totl_2020_not_found.xml` | `/sources/2/footnote/USA~SP.POP.TOTL~YR2020/metadata?format=json` | 200 | No footnote exists for this key: HTTP 200 with Content-Type text/xml and an XML <wb:error> message id 160 'Data not found.' even though format=json was requested. The loader must check Content-Type/body, not just HTTP status. | classify() -> non_json (XML error 160) |
| `headers_cache_busted_indicator_miss.txt` | `/indicator/SP.POP.TOTL?format=json&cb=verify20261014a` | 200 | Verifier. Unknown query param cb accepted; forces cf-cache-status MISS; last-modified equals date (render time); body byte-identical to indicator_sp_pop_totl.json. set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_conditional_if_modified_since_data_304.txt` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 304 | Conditional request with If-Modified-Since equal to the cached Last-Modified -> HTTP 304, empty body, served by Cloudflare (HIT). set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_conditional_if_modified_since_indicator_304.txt` | `/indicator/SP.POP.TOTL?format=json` | 304 | Conditional request on indicator metadata with If-Modified-Since equal to its Last-Modified -> HTTP 304 (cf HIT, age 78391, max-age 35524). set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_conditional_if_none_match_star_200.txt` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 200 | If-None-Match: * -> HTTP 200 full body (no ETag support observed). set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_data_call_cache_hit.txt` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 200 | Same URL 17 s later: cf-cache-status HIT, age 17, identical body bytes. set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_data_call_cache_miss.txt` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 200 | Response headers of a data call on a Cloudflare cache MISS: Cache-Control public, max-age=86400; Last-Modified equals the response generation time (not a data-change time); no ETag. set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_indicator_metadata_cache_hit.txt` | `/indicator/SP.POP.TOTL?format=json` | 200 | Indicator metadata headers: cf HIT, age 78280, cache-control max-age=35524, Last-Modified Mon, 12 Oct 2026 13:18:37 GMT, no ETag. set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `headers_unversioned_redirect_301.txt` | `https://api.worldbank.org/indicator/SP.POP.TOTL?format=json` | 301 | 301 redirect headers for an unversioned path; Location points at /v2/. set-cookie values redacted. | Lineage: header capture (cf-cache-status, age, last-modified, max-age) |
| `indicator_invalid_code_error.json` | `/indicator/NOT.A.CODE?format=json` | 200 | Invalid indicator code on /indicator: HTTP 200, JSON array with a single message object (id '120', key 'Invalid value'). Docs say 120 maps to HTTP 404. | classify() -> api_error 120 |
| `indicator_multi_semicolon_returns_empty.json` | `/indicator/{4ind}?format=json` | 200 | Semicolon-joined indicator codes on /indicator/{codes} are NOT supported for metadata: HTTP 200 with total 0, pages 0 and an empty list (no error). Same result with &source=2 and with /source/2/indicator/{codes}. | Assert 1 row per requested indicator code (empty 200 is not success) |
| `indicator_ny_gdp_mktp_cd.json` | `/indicator/NY.GDP.MKTP.CD?format=json` | 200 | Indicator metadata for GDP (current US$). unit is empty string even though the series metadata Unitofmeasure is 'Current US$'. | **Golden R4**: indicator metadata |
| `indicator_ny_gdp_pcap_cd.json` | `/indicator/NY.GDP.PCAP.CD?format=json` | 200 | Indicator metadata for GDP per capita (current US$). unit empty string; sourceNote has a missing space ('estimate.This'). | **Golden R5**: indicator metadata |
| `indicator_sp_dyn_le00_in.json` | `/indicator/SP.DYN.LE00.IN?format=json` | 200 | Indicator metadata for life expectancy at birth, total (years). unit empty string. | **Golden R6**: indicator metadata |
| `indicator_sp_pop_totl.json` | `/indicator/SP.POP.TOTL?format=json` | 200 | Indicator metadata (v2 /indicator/{code}). Envelope: page/pages/total are JSON numbers but per_page is a quoted string "50". unit is empty string. topics[].value 'Health ' has a trailing space. | **Golden R3**: indicator metadata |
| `indicator_sp_pop_totl_default_format.xml` | `/indicator/SP.POP.TOTL` | 200 | No format parameter: default response is XML (text/xml), envelope attributes page/pages/per_page/total. | classify() -> non_json |
| `series_metadata_4_indicators.json` | `/sources/2/series/{4ind}/metadata?format=json` | 200 | Metadata API: /sources/2/series/{codes}/metadata accepts semicolon-joined series codes (one call for all 4). Object envelope; per_page is quoted '5000'; total counts metatype entries (60), not series. | Metadata API (series only) long landing |
| `series_metadata_4_indicators_per_page_10_page_1.json` | `/sources/2/series/{4ind}/metadata?format=json&per_page=10` | 200 | Verifier. Metadata API honours per_page: per_page "10" (string), pages 6, total 60. Paging slices the flattened metatype entries: page 1 holds only the first 10 metatypes of NY.GDP.MKTP.CD. | Negative: never send per_page to the Metadata API (variables split across pages) |
| `series_metadata_4_indicators_per_page_10_page_2.json` | `/sources/2/series/{4ind}/metadata?format=json&per_page=10&page=2` | 200 | Verifier. Page 2 of 6: the remaining 5 metatypes of NY.GDP.MKTP.CD plus the first 5 of NY.GDP.PCAP.CD. One variable spans pages; merge by (concept, variable id). | As page 1: merge by (concept, variable_id) if paging is ever needed |
| `series_metadata_invalid_code_xml_error.xml` | `/sources/2/series/NOT.A.CODE/metadata?format=json` | 200 | Invalid series code on the Metadata API: HTTP 200, text/xml, error id 160 (despite format=json). Docs say id 160 maps to HTTP 400 with a different message. | classify() -> non_json (XML error 160 despite format=json) |
| `series_metadata_sp_pop_totl.json` | `/sources/2/series/SP.POP.TOTL/metadata?format=json` | 200 | Metadata API for a single series (SP.POP.TOTL); 16 metatypes. | Metadata API (single series) |
| `source_2_concepts.json` | `/sources/2/concepts?format=json` | 200 | Concepts for source 2 (Country, Series, Time). Different envelope shape: a JSON object (not a 2-element array) and per_page is a JSON number 50 here. | Reference only |
| `source_2_wdi.json` | `/source/2?format=json` | 200 | Single source 2 (World Development Indicators). lastupdated '2026-10-08' (YYYY-MM-DD string) equals the data-call envelope lastupdated observed the same day. Envelope numbers are quoted strings. | **Golden R0**: release watermark (`lastupdated`) |
| `sources_2_wdi_plural_path.json` | `/sources/2?format=json` | 200 | /sources/2 (plural) returns the same row as /source/2. | Reference only (same row as /source/2) |
| `sources_list_per_page_100.json` | `/sources?format=json&per_page=100` | 200 | All sources (71 at fetch time) with per_page=100 so it fits in one page. Envelope page/pages/per_page/total are ALL quoted strings here (unlike indicator/data envelopes). | Reference only |
| `unversioned_path_redirect_301.txt` | `https://api.worldbank.org/indicator/SP.POP.TOTL?format=json` | 301 | Unversioned (v1-style) path: HTTP 301 with Location https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json and a plain-text body. Docs say old endpoints return 'Resource not found'. | Redirect -> fatal |
| `critic__source_2_recheck.json` | `/source/2?format=json` | 200 | Critic. Exact R0 URL at 01:59Z: cf HIT, copy rendered 13 Oct 06:20Z (~19.7 h old); byte-identical to the golden R0. | Lineage: R0 itself is edge-cached |
| `critic__source_2_cachebuster.json` | `/source/2?format=json&cb=critic20261014a` | 200 | Critic. Fresh render (MISS, charset, `last-modified` = `date`, 0.17 s); same body. | Production release probe (cache-busted R0) |

#### `samples/observations/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `NY.GDP.MKTP.CD__9_countries__2018_2024.json` | `/country/{9c}/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | GOLDEN FIXTURE (exact recommended production URL): NY.GDP.MKTP.CD for 9 countries (ISO3, semicolon list) x 2018:2024 inclusive, source=2 (WDI), per_page=1000 -> 1 page, 63 rows. | **Golden R9**: observations (int/float token mix: pin DOUBLE) |
| `NY.GDP.PCAP.CD__9_countries__2018_2024.json` | `/country/{9c}/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000` | 200 | GOLDEN FIXTURE (exact recommended production URL): NY.GDP.PCAP.CD for 9 countries (ISO3, semicolon list) x 2018:2024 inclusive, source=2 (WDI), per_page=1000 -> 1 page, 63 rows. | **Golden R10**: observations (`decimal:1`) |
| `SP.DYN.LE00.IN__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2018:2024&per_page=1000` | 200 | GOLDEN FIXTURE (exact recommended production URL): SP.DYN.LE00.IN for 9 countries (ISO3, semicolon list) x 2018:2024 inclusive, source=2 (WDI), per_page=1000 -> 1 page, 63 rows. | **Golden R11**: observations |
| `SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?source=2&format=json&date=2018:2024&per_page=1000` | 200 | GOLDEN FIXTURE (exact recommended production URL): SP.POP.TOTL for 9 countries (ISO3, semicolon list) x 2018:2024 inclusive, source=2 (WDI), per_page=1000 -> 1 page, 63 rows. | **Golden R8**: observations (int tokens) |
| `anomaly__suspected_cached_body__frequency_Y_url_returned_plain_mrv_body__SP.DYN.LE00.IN__BRA_USA.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&frequency=Y` | 200 | SUSPECT-BUT-PROBABLY-CORRECT. URL asks mrv=3&frequency=Y; body byte-identical to plain mrv=3 response and served with Content-Type 'application/json' without charset (the pattern shared by confirmed wrong-body responses). | Benign: `frequency=Y` is a no-op, so the body is correct despite the cached-body signature |
| `anomaly__suspected_cached_body__multi_indicator_no_source_returned_source2_body.json` | `/country/{9c}/indicator/{4ind}?format=json&date=2018:2024&per_page=1000` | 200 | ANOMALY. | Cache can mask an error: this request should have failed with 120 (see the error__multi_indicator_without_source fixture) |
| `anomaly__wrong_body__gapfill_url_returned_plain_mrv_body__SP.DYN.LE00.IN__BRA_USA.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&gapfill=Y` | 200 | ANOMALY (confirmed wrong body). URL asks mrv=3&gapfill=Y but body is byte-identical to the plain mrv=3 response fetched 3s earlier (years 2022-2024). | Validator must reject: body does not match the request (wrong-body) |
| `anomaly__wrong_body__mrnev_2_url_returned_gapfill_body__SI.POV.GINI__MEX_JPN.json` | `/country/MEX;JPN/indicator/SI.POV.GINI?format=json&mrnev=2` | 200 | ANOMALY (confirmed wrong body). URL asks mrnev=2 but body is byte-identical to the mrv=4&gapfill=Y response fetched 1s earlier (8 rows incl. 2025, gap-filled). | Validator must reject: body does not match the request (wrong-body) |
| `codes__iso2_lowercase__sp.pop.totl__2024.json` | `/country/br;ca;de;gb;in;jp;mx;us;za/indicator/sp.pop.totl?format=json&date=2024&per_page=1000` | 200 | Country path given as lowercase ISO2 codes and indicator in lowercase. Checks code case/ISO2 acceptance and what ids are echoed back. | Canonical ids echoed back |
| `date_range_beyond_latest__SP.DYN.LE00.IN__9_countries__2023_2026.json` | `/country/{9c}/indicator/SP.DYN.LE00.IN?format=json&date=2023:2026&per_page=1000` | 200 | Date range extending past the latest published year (2025, 2026). Shows whether not-yet-published years come back as value:null rows or are omitted. | **Missing-data test**: 2025 rows `value:null` (reported_null), 2026 absent (not_returned) |
| `date_range_reversed__USA__SP.POP.TOTL__2024_2018.json` | `/country/USA/indicator/SP.POP.TOTL?format=json&date=2024:2018` | 200 | Reversed date range (start > end). | Reversed range normalised |
| `error__bad_date_format__2018-2024.json` | `/country/USA/indicator/SP.POP.TOTL?format=json&date=2018-2024` | 200 | Malformed date param (hyphen instead of colon). | classify() -> api_error 120 (fatal, no retry) |
| `error__invalid_country_code__XXX.json` | `/country/XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | Invalid (non-existent) country code on the observations endpoint. | classify() -> api_error 120 (fatal, no retry) |
| `error__invalid_indicator_code__SP.POP.TOTLX.json` | `/country/USA/indicator/SP.POP.TOTLX?format=json&date=2018:2024` | 200 | Invalid (non-existent) indicator code on the observations endpoint. | classify() -> api_error 120 (fatal, no retry) |
| `error__mixed_valid_and_invalid_country__USA_XXX.json` | `/country/USA;XXX/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | One valid + one invalid country in the semicolon list: partial result or whole-request error? | classify() -> api_error 120 (fatal, no retry) |
| `error__multi_indicator_without_source__http200_error_120.json` | `/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.DYN.LE00.IN;NY.GDP.PCAP.CD;NY.GDP.MKTP.CD;SP.POP....` | 200 | Fresh multi-indicator URL WITHOUT source= (indicator and country order reversed so no cache could match). HTTP 200 with JSON error body: message id '120' Invalid value. Confirms docs: multi-indicator requests need source=. | classify() -> api_error 120 (fatal, no retry) |
| `format_default_xml__USA__SP.POP.TOTL__2024.xml` | `/country/USA/indicator/SP.POP.TOTL?date=2024` | 200 | No format= param: shows the default (documented XML) response for the same observation. | classify() -> non_json |
| `missing_data_example__SI.POV.GINI__9_countries__2018_2024.json` | `/country/{9c}/indicator/SI.POV.GINI?format=json&date=2018:2024&per_page=1000` | 200 | OUT-OF-SCOPE indicator (Gini index) chosen because it is sparse; demonstrates how in-range missing observations are represented. Useful as a null-handling test fixture. | **Missing-data test**: 25 of 63 in-range cells are null rows; never coerce to 0 |
| `multi_indicator__4_indicators__9_countries__2018_2024__source2.json` | `/country/{9c}/indicator/{4ind}?source=2&format=json&date=2018:2024&per_page=1000` | 200 | Multi-indicator call (4 indicators semicolon-separated) with source=2, 9 countries, 2018:2024, per_page=1000. Compare to per-indicator golden fixtures. | Cross-check: equals union of the 4 golden fixtures; `sourceid` null |
| `obs_status_example__CHN__NYGDPMKTPKDZ__mrv3.json` | `/country/CHN/indicator/NYGDPMKTPKDZ?format=json&mrv=3` | 200 | OUT-OF-SCOPE: documented obs_status example indicator (forecast flag 'F'). Attempt to see a non-empty obs_status. | Reference only (non-empty `obs_status`) |
| `optional_param__date_and_mrv_combined__SP.DYN.LE00.IN__BRA_USA.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&mrv=3` | 200 | date=2018:2024 plus mrv=3: only 3 years per country returned (2022-2024), so mrv overrides/narrows the date range. Byte-identical to mrv=3 without date. | Doc only: forbidden/unused param behaviour |
| `optional_param__footnote_y__CHN__SL.AGR.EMPL.ZS__2018_2020.json` | `/country/CHN/indicator/SL.AGR.EMPL.ZS?format=json&date=2018:2020&footnote=y` | 200 | OUT-OF-SCOPE: documented footnote example (CHN agriculture employment) narrowed to 2018:2020, to see a populated footnote. | Reference only (documented footnote example; still `""`) |
| `optional_param__footnote_y__USA__SP.POP.TOTL__2018_2024.json` | `/country/USA/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y` | 200 | footnote=y on an in-scope series. Shows where/how footnotes appear (or not) for our data. | Footnote key parsing (`""` values) |
| `optional_param__mrnev_2__SI.POV.GINI__JPN_MEX.json` | `/country/JPN;MEX/indicator/SI.POV.GINI?format=json&mrnev=2` | 200 | OUT-OF-SCOPE indicator. Correct mrnev=2 answer (fresh URL): 2 most recent NON-NULL values per country, regardless of year (JPN 2020,2019; MEX 2024,2022). Hides gaps; sourceid comes back null (JSON null) for mrnev. | Doc only: forbidden/unused param behaviour |
| `optional_param__mrnev_3__SP.DYN.LE00.IN__BRA_USA.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrnev=3` | 200 | mrnev=3: same rows as mrv=3 here (no gaps), but envelope sourceid is JSON null instead of "2". | Doc only: forbidden/unused param behaviour |
| `optional_param__mrv_3__SP.DYN.LE00.IN__BRA_USA.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?format=json&mrv=3` | 200 | mrv=3 without date: returns 2024, 2023, 2022 - the unpublished 2025 (null) year is skipped because no requested country has data for it. | Doc only: forbidden/unused param behaviour |
| `optional_param__mrv_3_frequency_Y__SP.DYN.LE00.IN__USA_BRA_fresh.json` | `/country/USA;BRA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&frequency=Y` | 200 | Fresh URL retest of mrv=3&frequency=Y (country order swapped vs the suspect cached one). | Doc only: forbidden/unused param behaviour |
| `optional_param__mrv_3_gapfill_Y__SP.DYN.LE00.IN__USA_BRA_fresh.json` | `/country/USA;BRA/indicator/SP.DYN.LE00.IN?format=json&mrv=3&gapfill=Y` | 200 | In-scope indicator, fresh URL. mrv=3&gapfill=Y returns 2025 (unpublished, null in plain calls) filled with the 2024 value for BRA and USA, obs_status empty. Proves gapfill fabricates values. Pipeline must NOT use gapfill. | Doc only: forbidden/unused param behaviour |
| `optional_param__mrv_4__SI.POV.GINI__MEX_JPN.json` | `/country/MEX;JPN/indicator/SI.POV.GINI?format=json&mrv=4` | 200 | OUT-OF-SCOPE indicator (sparse). | Doc only: forbidden/unused param behaviour |
| `optional_param__mrv_4_gapfill_Y__SI.POV.GINI__MEX_JPN.json` | `/country/MEX;JPN/indicator/SI.POV.GINI?format=json&mrv=4&gapfill=Y` | 200 | OUT-OF-SCOPE indicator. mrv=4&gapfill=Y returns calendar years 2025-2022 with nulls replaced by carried-forward older values (e.g. JPN 2022-2024 = 32.3, which is really the 2020 value; MEX 2025 = 42.6 copied from 2024). | Doc only: forbidden/unused param behaviour |
| `optional_param__scale_y__USA__NY.GDP.MKTP.CD__2024.json` | `/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024&scale=y` | 200 | scale=y on GDP. Documented to scale values to thousands/millions/billions; we must NOT use it (changes the stored magnitude). | Doc only: forbidden/unused param behaviour |
| `ordering__countries_reversed__SP.POP.TOTL__2024.json` | `/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.POP.TOTL?format=json&date=2024&per_page=1000` | 200 | Ordering test: countries listed in reverse alphabetical order, single year date=2024. Does response follow request order or a fixed sort? | Order independence (do not rely on order) |
| `ordering__multi_indicator_reversed__source2__9_countries_reversed__2024.json` | `/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/SP.DYN.LE00.IN;NY.GDP.PCAP.CD;NY.GDP.MKTP.CD;SP.POP....` | 200 | Ordering test for multi-indicator: indicators AND countries listed in reverse order, single year 2024, source=2. Is indicator grouping in request order or fixed? | Reference only |
| `out_of_scope_years__4_indicators__9_countries__2025_2026__source2.json` | `/country/{9c}/indicator/{4ind}?source=2&format=json&date=2025:2026&per_page=1000` | 200 | Years just past our scope (2025, 2026) for all 4 indicators: shows which are published yet, how unpublished years look (null row vs absent), and that 2026 is outside the source time axis. | Validator rejects years outside 2018-2024 (Contradictions #15); `scale:""` on null rows |
| `paging__default_per_page__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | No per_page given: shows default page size (documented 50) -> 63 rows split over 2 pages; only page 1 returned. | Default per_page=50 -> 2 pages (forgotten per_page) |
| `paging__per_page_20_page_1__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=20&page=1` | 200 | Paging: per_page=20, page=1 of 4. Served with Content-Type 'application/json' (no charset) like the anomalous cached responses, but content verified correct (pages 1-4 concatenated == golden single-page fixture). | Paginator: pages concatenate to the single-page result |
| `paging__per_page_20_page_2__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=20&page=2` | 200 | Paging: per_page=20, page=2 of 4. Served with Content-Type 'application/json' (no charset) like the anomalous cached responses, but content verified correct (pages 1-4 concatenated == golden single-page fixture). | Paginator: pages concatenate to the single-page result |
| `paging__per_page_20_page_3__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=20&page=3` | 200 | Paging: per_page=20, page=3 of 4. | Paginator: pages concatenate to the single-page result |
| `paging__per_page_20_page_4__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=20&page=4` | 200 | Paging: per_page=20, page=4 of 4 (last, partial page of 3 rows). | Paginator: pages concatenate to the single-page result |
| `paging__per_page_20_page_5_beyond_last__SP.POP.TOTL__9_countries__2018_2024.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=20&page=5` | 200 | Paging: page=5 requested when pages=4 (beyond last page). Shows the stop-condition shape. | Paginator stop: `page > pages` returns `[]` |
| `single_country_single_indicator__USA__SP.POP.TOTL__2018_2024.json` | `/country/USA/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=50` | 200 | Single country (ISO3 USA) + single indicator, date range 2018:2024, per_page=50. Baseline response shape. | Small success envelope for unit tests |
| `single_indicator_with_source2__NY.GDP.MKTP.CD__9_countries_reversed__2018_2024.json` | `/country/ZAF;USA;MEX;JPN;IND;GBR;DEU;CAN;BRA/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&p...` | 200 | Single indicator WITH explicit source=2 (fresh URL, countries reversed). Compare to golden fixture fetched without source=. | Order independence: byte-identical to golden R9 |
| `verify__anomaly__mrv_2_gapfill_Y_url_returned_mrnev_2_body__SP.DYN.LE00.IN__CAN_DEU.json` | `/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrv=2&gapfill=Y` | 200 | VERIFIER fixture, ANOMALY reproduced. URL asks mrv=2&gapfill=Y but body is byte-identical to the mrnev=2 response (no fabricated 2025, sourceid:null, no 'unit' key). | Validator must reject: body does not match the request (wrong-body) |
| `verify__anomaly__mrv_2_url_returned_mrnev_2_body__SP.DYN.LE00.IN__CAN_DEU.json` | `/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrv=2` | 200 | VERIFIER fixture, ANOMALY reproduced. URL asks mrv=2 but body is byte-identical to the mrnev=2 response for the same countries fetched 2 s earlier (envelope sourceid:null and rows without the 'unit' key, both mrnev traits). | Validator must reject: body does not match the request (wrong-body) |
| `verify__gapfill_fabricates_2025__SP.DYN.LE00.IN__IND_ZAF.json` | `/country/IND;ZAF/indicator/SP.DYN.LE00.IN?format=json&mrv=2&gapfill=Y` | 200 | VERIFIER fixture. Fresh mrv=2&gapfill=Y: 2025 (unpublished, null in plain calls) is filled with the 2024 value for IND (72.235) and ZAF (66.312); obs_status stays "". | Doc only: why `gapfill` is banned |
| `verify__mrnev_2_rows_have_no_unit_key__SP.DYN.LE00.IN__CAN_DEU.json` | `/country/CAN;DEU/indicator/SP.DYN.LE00.IN?format=json&mrnev=2` | 200 | VERIFIER fixture. Genuine mrnev=2 response (charset=utf-8, first request for this URL): envelope sourceid is JSON null and every row OMITS the 'unit' key entirely (not "", not null). | Tolerate missing optional keys (`unit`) |
| `verify__mrv_overrides_date__SP.POP.TOTL__GBR_IND__date_2018_2024_mrv_2.json` | `/country/GBR;IND/indicator/SP.POP.TOTL?format=json&date=2018:2024&mrv=2` | 200 | VERIFIER fixture. date=2018:2024&mrv=2 returns 2025 and 2024 - a year OUTSIDE the requested date range - proving mrv overrides date. | Validator: year outside range -> reject (why `mrv` is banned) |
| `verify__multi_indicator_null_rows_have_scale_empty__CAN_MEX__2024_2025__source2.json` | `/country/CAN;MEX/indicator/SP.DYN.LE00.IN;SP.POP.TOTL?source=2&format=json&date=2024:2025&per_page=100` | 200 | VERIFIER fixture. Multi-indicator call with source=2: the null-value rows (SP.DYN.LE00.IN 2025) carry an extra key "scale":"" that non-null rows and single-indicator null rows do not have. sourceid is JSON null. | Tolerate stray optional keys (`scale:""`) |
| `verify__null_rows_single_indicator_source2__SP.DYN.LE00.IN__BRA_USA__2024_2026.json` | `/country/BRA;USA/indicator/SP.DYN.LE00.IN?source=2&format=json&date=2024:2026&per_page=1000` | 200 | VERIFIER fixture. | **Missing-data test** in the recommended call shape |
| `verify__out_of_scope_source15_monthly__IND__DPANUSSPF__mrv2_freqM.json` | `/country/IND/indicator/DPANUSSPF?format=json&mrv=2&frequency=M` | 200 | VERIFIER fixture, OUT OF SCOPE (source 15, Global Economic Monitor). Monthly date strings ('2026M08'). | Year cast must fail loudly on `2026M08`; `countryiso3code ""` |
| `verify__sort_key_is_iso3__SP.POP.TOTL__AUT_AUS__2024.json` | `/country/AUT;AUS/indicator/SP.POP.TOTL?format=json&date=2024` | 200 | VERIFIER fixture. Request lists AUT;AUS; response returns AUS (ISO2 AU, Australia) before AUT (ISO2 AT, Austria). | Reference only (sort order) |
| `critic__inner_cache_A_footnote_y__MEX_ZAF__NY.GDP.PCAP.CD.json` | `/country/MEX;ZAF/indicator/NY.GDP.PCAP.CD?source=2&format=json&date=2018:2024&per_page=1000&footnote=y` | 200 | Critic. Fresh render; 14 rows with `footnote` keys. Step A of the retry test. | Setup for the wrong-body retry test |
| `critic__inner_cache_B_no_footnote__MEX_ZAF__NY.GDP.PCAP.CD.json` | same, **without** `footnote` | 200 | Critic. **Wrong body**: byte-identical to step A; plain `application/json`, `last-modified` = `date`, `max-age=86400`. Passes §5 validation (values identical; `footnote` is an optional key), so this substitution is benign and undetectable; shows the inner path's fresh-looking headers |
| `critic__inner_cache_C_no_footnote_retry_same_url__MEX_ZAF__NY.GDP.PCAP.CD.json` | step-B URL again | 200 | Critic. Cloudflare HIT with the same wrong body. | Same-URL retry returns the same bytes |
| `critic__inner_cache_D_no_footnote_cachebuster__MEX_ZAF__NY.GDP.PCAP.CD.json` | step-B URL + `&cb=critic20261014b` | 200 | Critic. Fresh render, correct body (no `footnote` keys). | Cache-busted retry recovers |
| `critic__inner_cache_F_mrnev_2__ESP_PRT__SP.DYN.LE00.IN.json` | `/country/ESP;PRT/indicator/SP.DYN.LE00.IN?format=json&mrnev=2` | 200 | Critic. Genuine `mrnev` body (`sourceid` null, no `unit`). | Doc only |
| `critic__inner_cache_G_mrv_2__ESP_PRT__SP.DYN.LE00.IN.json` | same pair, `mrv=2` | 200 | Critic. Correct `mrv` body this time: substitution is intermittent. | Doc only |
| `critic__inner_cache_H_mrv_2_cachebuster__ESP_PRT__SP.DYN.LE00.IN.json` | step-G URL + `&cb=critic20261014c` | 200 | Critic. Inner-cache path (plain `application/json`) with step G's body: the token escaped Cloudflare only. | Cache-buster is not a guarantee |

#### `samples/pagination/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `country_list_page_beyond_range_page_999.json` | `/country?format=json&per_page=2&page=999` | 200 | Country endpoint page beyond range -> rows [] (empty array). per_page stays a string. | Paginator stop: `page > pages` returns `[]` |
| `date_1900_1901_out_of_range_filter_ignored.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=1900:1901` | 200 | GOTCHA: a date RANGE lying entirely outside the data (1900:1901) is silently IGNORED. The API returns ALL years (1960-2025), total 594, instead of zero rows. Only page 1 is saved. | Validator: rows outside requested years -> reject the response (filter silently ignored; Contradictions #15) |
| `default_format_no_format_param.xml` | `/country/BRA/indicator/SP.POP.TOTL?date=2018:2024` | 200 | No format param -> HTTP 200 text/xml. XML is the default; paging meta are XML attributes on <wb:data> (page/pages/per_page/total/sourceid/lastupdated). Byte-identical to explicit format=xml response. | classify() -> non_json |
| `envelope_country_9_selected.json` | `/country/{9c}?format=json` | 200 | Our 9 countries in one call (semicolon list). Meta per_page is a STRING ("50"); page/pages/total numbers. 1 page, 9 rows. | Byte-identical to golden R2 body |
| `envelope_country_list_per_page_2.json` | `/country?format=json&per_page=2` | 200 | Country list meta: page/pages/total are numbers but per_page is a STRING ("2"). No sourceid/lastupdated keys. | Envelope typing (`per_page` string) |
| `envelope_incomelevel_list.json` | `/incomeLevel?format=json` | 200 | incomeLevel list meta: ALL four of page/pages/per_page/total are STRINGS ("1","1","50","7"). | Byte-identical to golden R1; all-string envelope |
| `envelope_indicator_list_per_page_2.json` | `/indicator?format=json&per_page=2` | 200 | Indicator list meta: per_page STRING ("2"); total 29544 indicators on the day fetched. | Envelope typing (`per_page` string) |
| `envelope_indicator_metadata_sp_pop_totl.json` | `/indicator/SP.POP.TOTL?format=json` | 200 | Indicator metadata (single code) meta: per_page is a STRING ("50"); page/pages/total numbers. | Byte-identical to golden R3 |
| `format_json_bra_sp_pop_totl.json` | `/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=json` | 200 | format=json; top-level array [meta, rows]. Observation meta: page/pages/per_page/total are JSON numbers; sourceid and lastupdated are strings. | Small success envelope |
| `format_jsonstat_bra_sp_pop_totl.json` | `/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=jsonstat` | 200 | format=jsonstat; JSON-stat object keyed by source ('WDI'), no page/pages/per_page meta at all. Not used by the pipeline. | Reference only (no paging meta) |
| `format_xml_explicit_bra_sp_pop_totl.xml` | `/country/BRA/indicator/SP.POP.TOTL?date=2018:2024&format=xml` | 200 | format=xml; byte-identical to the no-format response. | classify() -> non_json |
| `incomelevel_list_page_beyond_range_page_2.json` | `/incomeLevel?format=json&page=2` | 200 | incomeLevel page beyond range -> rows []; all meta values are strings, including page "2". | Paginator stop: `page > pages` returns `[]` |
| `invalid_page_0_error_120.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=0` | 200 | page=0 -> HTTP 200 with a ONE-element array [{message:[{id:'120',key:'Invalid value',...}]}]. page=-1, per_page=0, per_page=-1 and per_page=abc return the same body. | classify() -> api_error 120 (fatal, no retry) |
| `obs_NY.GDP.MKTP.CD_9c_per_page_1000.json` | `/country/{9c}/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=1000` | 200 | GDP (current US$): 63 rows; 56 values are JSON floats, but the 7 USA values are integer literals (e.g. 29298013000000). Mixed int/float in one column. decimal=0. | Byte-identical to golden R9 |
| `obs_NY.GDP.PCAP.CD_9c_per_page_1000.json` | `/country/{9c}/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=1000` | 200 | GDP per capita: 63 rows, all floats, decimal=1. | Byte-identical to golden R10 |
| `obs_SP.DYN.LE00.IN_9c_per_page_1000.json` | `/country/{9c}/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=1000` | 200 | Life expectancy: 63 rows, all floats, no nulls on the day fetched, decimal=0. | Byte-identical to golden R11 |
| `obs_SP.POP.TOTL_9c_default_per_page.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024` | 200 | No per_page -> per_page 50 (number), pages 2, total 63; 50 rows on page 1. This uncached request took ~15 s. | Default per_page=50 -> 2 pages (forgotten per_page) |
| `obs_SP.POP.TOTL_9c_per_page_1000.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1000` | 200 | per_page=1000 -> 1 page, 63 rows. Reference for the pagination-stability comparison: the union of the 7 per_page=10 pages is the same row sequence. | Paginator reference (single page); byte-identical to golden R8 body |
| `obs_SP.POP.TOTL_9c_per_page_10_page_1.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=1` | 200 | per_page=10, page 1 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_2.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=2` | 200 | per_page=10, page 2 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_3.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=3` | 200 | per_page=10, page 3 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_4.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=4` | 200 | per_page=10, page 4 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_5.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=5` | 200 | per_page=10, page 5 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_6.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=6` | 200 | per_page=10, page 6 of 7, 10 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_10_page_7.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=7` | 200 | per_page=10, page 7 of 7, 3 rows. Fetch run 1. Run 2 (same URLs, CDN HIT) and run 3 (params reordered so the CDN missed, pages fetched 7->1) were byte-identical. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_1_page_1.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=1` | 200 | per_page=1 -> pages 63, 1 row. | Paginator: pages = ceil(total/per_page) |
| `obs_SP.POP.TOTL_9c_per_page_50000_http_400.html` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=50000` | 400 | per_page=50000 -> HTTP 400 text/html 'Request Error' page with a UTF-8 BOM; NOT JSON. per_page=32767 works, 32768 fails the same way (looks like a signed 16-bit limit). | WCF 400 -> fatal; config validation per_page <= 32767 |
| `obs_SP.POP.TOTL_9c_per_page_50_page_1.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=50&page=1` | 200 | per_page=50 page 1 of 2 -> 50 rows. Byte-identical to the default per_page response. | Paginator: pages concatenate to the single-page result |
| `obs_SP.POP.TOTL_9c_per_page_50_page_2.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=50&page=2` | 200 | per_page=50 page 2 of 2 (last, partial page) -> 13 rows. | Paginator: pages concatenate to the single-page result |
| `obs_NY.GDP.MKTP.CD_9c_per_page_50_page_1.json` | `/country/{9c}/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=50&page=1` | 200 | per_page=50 page 1 of 2 -> 50 rows. Recorded 2026-10-14 for the pipeline's per_page=50 config; validated against the request. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_NY.GDP.MKTP.CD_9c_per_page_50_page_2.json` | `/country/{9c}/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=50&page=2` | 200 | per_page=50 page 2 of 2 (last, partial page) -> 13 rows. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_NY.GDP.PCAP.CD_9c_per_page_50_page_1.json` | `/country/{9c}/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=50&page=1` | 200 | per_page=50 page 1 of 2 -> 50 rows. Recorded 2026-10-14 for the pipeline's per_page=50 config; validated against the request. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_NY.GDP.PCAP.CD_9c_per_page_50_page_2.json` | `/country/{9c}/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=50&page=2` | 200 | per_page=50 page 2 of 2 (last, partial page) -> 13 rows. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_SP.DYN.LE00.IN_9c_per_page_50_page_1.json` | `/country/{9c}/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=50&page=1` | 200 | per_page=50 page 1 of 2 -> 50 rows. Recorded 2026-10-14 for the pipeline's per_page=50 config; validated against the request. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_SP.DYN.LE00.IN_9c_per_page_50_page_2.json` | `/country/{9c}/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=50&page=2` | 200 | per_page=50 page 2 of 2 (last, partial page) -> 13 rows. | Offline paging fixture: pages 1+2 match the golden `observations/` fixture value for value |
| `obs_multi_indicator_4ind_9c_source_2_per_page_1000.json` | `/country/{9c}/indicator/{4ind}?source=2&format=json&date=2018:2024&per_page=1000` | 200 | All 4 indicators x 9 countries in one call (source=2 required for multi-indicator). total 252, 1 page. Meta sourceid is JSON null here (it is the string "2" in single-indicator calls). | Reference only (same bytes as the observations multi-indicator fixture) |
| `page_beyond_range_page_8_of_7.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=8` | 200 | page=pages+1 -> HTTP 200, rows [] (empty array). | Paginator stop: `page > pages` returns `[]` |
| `page_beyond_range_page_999.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=10&page=999` | 200 | page=999 with pages=7 -> HTTP 200, meta echoes page 999, rows is an EMPTY ARRAY [] (not null, no error message). | Paginator stop: `page > pages` returns `[]` |
| `zero_match_date_1900_single_year.json` | `/country/BRA/indicator/SP.POP.TOTL?format=json&date=1900` | 200 | True zero-match (single year 1900): meta {page:0,pages:0,per_page:0,total:0,sourceid:null,lastupdated:null}, rows is JSON null. | classify() -> no_data (`[meta, null]`) |

#### `samples/synthesis/`

| File | Request (abridged) | HTTP | What it shows | Suggested test use |
|---|---|---|---|---|
| `obs_NY.GDP.MKTP.CD__golden_url_plus_cachebuster.json` | `/country/{9c}/indicator/NY.GDP.MKTP.CD?source=2&format=json&date=2018:2024&per_page=1000&cb=synth20261014a` | 200 | Synthesis live check. Golden NY.GDP.MKTP.CD URL + unknown param cb=synth20261014a. Cloudflare MISS, max-age=86400, last-modified = date (fresh origin render, 1.6 s). Body byte-identical to the golden fixture. | Cache-buster forced a fresh render here (not guaranteed; see area 02 V24) |
| `obs_SP.POP.TOTL__golden_params_reordered__origin_cache_copy.json` | `/country/{9c}/indicator/SP.POP.TOTL?format=json&per_page=1000&date=2018:2024&source=2` | 200 | Synthesis live check. Golden SP.POP.TOTL request with query params reordered (format, per_page, date, source). | Lineage: Cloudflare MISS can still be an old origin copy (`last-modified` shows age) |
| `obs_SP.POP.TOTL__golden_url_plus_footnote_y.json` | `/country/{9c}/indicator/SP.POP.TOTL?source=2&format=json&date=2018:2024&per_page=1000&footnote=y` | 200 | Synthesis live check. Golden SP.POP.TOTL URL + footnote=y. Fresh origin render (cf MISS, max-age=86400, last-modified = date, 15.4 s). Every row gains a 'footnote' key ('' for 61 rows, 'Break in series.' for DEU 2023 and 2022). | Golden R8 variant if `footnote=y` is adopted (decision D4) |
| `obs_SP.POP.TOTL__no_source_reordered__origin_cache_copy.json` | `/country/{9c}/indicator/SP.POP.TOTL?per_page=1000&format=json&date=2018:2024` | 200 | Synthesis live check. SP.POP.TOTL 9 countries 2018:2024 per_page=1000 without source=2, params reordered. Cloudflare MISS, origin-cache copy from 01:11:55 (max-age=84534). | `source=2` is a no-op on single-indicator calls |
| `obs_NY.GDP.MKTP.CD__golden_url_plus_footnote_y.json` | golden R9 URL + `&footnote=y` | 200 | Critic. Fresh render; every row has `footnote` `""`; otherwise identical to golden R9. | Golden R9 variant if D4 adopts `footnote=y` |
| `obs_NY.GDP.PCAP.CD__golden_url_plus_footnote_y.json` | golden R10 URL + `&footnote=y` | 200 | Critic. Fresh render (36.9 s cold); every row has `footnote` `""`. | Golden R10 variant (D4) |
| `obs_SP.DYN.LE00.IN__golden_url_plus_footnote_y.json` | golden R11 URL + `&footnote=y` | 200 | Critic. Fresh render; every row has `footnote` `""`. With the R8 variant: 2 non-empty footnotes in scope. | Golden R11 variant (D4) |

---

## Open questions and decisions for the project owner

**Decisions (each blocks part of the model or the plan):**

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | How to "compare indicators across income groups **by year**" when the API only has the **current** classification | (a) Use the current group for every year: simple, fully API-sourced, the same convention WB uses for its own aggregates. (b) Use the as-of-year group from OGHIST as a committed dbt seed. (c) Both, with a `classification_basis` column | Start with **(a)**, labelled clearly. Move to (c) if approved. A secondary source (OWID, **unverified**) says all 9 countries kept the same group from 2017 to 2025, in which case (a) and (b) give identical results. The critic found no API route to (b): the WDI archive source 57 ignores a `/version/` segment on the Metadata API and returns the current group [observed by critic, 01 C4–C8]. |
| D2 | May we download `OGHIST_2026_10_08.xlsx` (about 113 KB, `ddh-openapi.worldbank.org/resources/DR0095334/download`)? If so, which fiscal-year→data-year rule applies? (FY*N* is released 1 July of *N*−1 using GNI of *N*−2.) | Approve or decline. Rule: the group in effect during year Y, or the group computed from Y's GNI | It needs your approval: it is a file download, and it is not done yet. |
| D3 | Include the WB income-aggregate benchmark (R12–R15, +4 calls)? | Yes as a labelled benchmark, or no | Optional. It is **not comparable** with statistics over our 9 countries (current membership, imputation, the 87th HIC economy). |
| D4 | Send `footnote=y` on R8–R11? It would capture DEU population 2022/2023 "Break in series." | Yes or no. The loader must treat `footnote` as optional either way (Contradictions #4) | Possible. Nothing breaks either way. Fixtures for both choices now exist: the `footnote=y` variants of all four golden URLs are in `samples/synthesis/` (R9–R11 added by the critic; only DEU POP 2022/2023 are non-empty). |
| D5 | Include R3–R7 (indicator and Metadata API)? They are needed for analyst-facing units, definitions and the licence | Include them, or use names from the observation rows only | **Include them** (+5 calls, about 42 KB). |
| D6 | Make `dim_country` a dbt snapshot (SCD2) so that future 1 July reclassifications are kept? | Yes or no | Nice to have. It adds scope. |
| D7 | Is the scope fixed at 2018–2024, or "the latest 7 years"? 2025 is already published for 3 of the 4 indicators | Fixed or rolling | Fixed, as the assignment states. |
| D8 | Staleness tolerance: is a copy up to about 25 h old acceptable? | Accept and record the headers, or compare `lastupdated` with R0 and refetch on a mismatch | Accept, record the headers, and assert `lastupdated` equals R0's. |
| D9 | Unit labels: raw `Unitofmeasure` (`Unit`) or a curated mapping (people / current US$ / years)? | — | Use a curated seed, and keep the raw value alongside. |
| D10 | Exact attribution text for deliverables (CC BY 4.0) | — | "Source: World Bank, World Development Indicators (lastupdated YYYY-MM-DD), CC BY 4.0; values transformed." |

**Unknowns we could not settle live** (the design tolerates every one of them):
- What real 5xx, 429 or error-105/199 responses look like, and what the actual rate limit or bot-management threshold is. None was seen in about 470 calls.
- How the origin-side cache is keyed. Wrong bodies were reproduced for `mrv`/`mrnev`/`gapfill`, for a missing `source` on multi-indicator calls, and for `footnote` (re-reproduced by the critic in the recommended call shape, 02 C2). The core parameters (`date`, `page`, `per_page`) were always honoured, and response validation is the mitigation. Also unknown: the inner cache's lifetime (substitutions were seen 1 s to ~3 min after the source request) and why it is intermittent (02 C6 did not substitute) — per-server caches behind a load balancer would explain it, but that is a guess.
- Whether a validation failure on one of our four golden URLs can ever happen in practice. Every substitution seen on our call shape differed only in the optional `footnote` key (benign); the harmful ones involved banned params (`mrv`, `mrnev`, `gapfill`) or the multi-indicator form.
- Whether `lastupdated` changes on every revision, how soon a new release shows through both cache layers, and whether row order and byte stability hold across releases. Only one release (2026-10-08) was observed.
- The identity of the 87th HIC economy (very likely Taiwan, China). It matters only for the R12 benchmark.
- When the Data360 migration happens. No v2 sunset has been announced, so keep the extractor behind a source-adapter interface.
- The behaviour of the undocumented footnote-metadata endpoint, and the exact per-segment URL limit (somewhere between 299 and 1,599 characters; the docs say 1,500).

---

## Completeness critic pass

A completeness review on 2026-10-14 (01:54–02:12 UTC) checked every file in this folder against what the pipeline needs: country metadata with income group, observations for the full scope with paging, failure handling, per-response lineage, idempotent re-runs, offline fixtures and change detection. It made **20 live requests** (01:59–02:03 UTC, `curl -sS -m 90`, about 1 s apart, no credentials). Every new sample has a `.meta.json` and a `.headers.txt` and is listed in the index above.

**Offline checks that passed:** all 217 body files and 219 sidecars before the pass (237 and 239 after it) are paired, except the two documented sidecar-only rows; every sidecar has the five required keys; every `.json` parses; no body is over 100 KB; every body appears in this index and every index row exists; no relative link is broken; every golden fixture's sidecar URL equals its call-plan URL character for character, and all golden observation envelopes carry `lastupdated` `"2026-10-08"`.

**Substantive findings (live):**

| Need | Gap found | Evidence | Where it is written up |
|---|---|---|---|
| Failure handling | A same-URL retry of an HTTP-200 body cannot help: Cloudflare returns the same bytes. A cache-busted retry recovered once and failed once | 02 C1–C7 | §1, §3, §5; Contradictions #18; 02 Failure modes; 05 Retries |
| Lineage | The inner cache path looks fresh in `last-modified`/`max-age`; only the missing charset marks it | 02 C2, C7; offline header re-analysis | §6; Contradictions #17; 02, 04 |
| Change detection | The R0 watermark is itself edge-cached (copy ~19.7 h old at 01:59Z); a cache-busted R0 is a 0.2 s fresh probe | 03 C1, C2 | §6 "Change detection for production"; 03 Critic additions |
| Income group by year (D1) | No API route to historical groups: source 57 ignores `/version/` on the Metadata API | 01 C4–C8 | D1; 01 section B |
| Offline fixtures | Optional R13–R15 and D4 `footnote=y` variants of R9–R11 had no fixtures | 01 C1–C3; 02 C8–C10 | Optional-calls table, D4, index |
| Timeouts | New maximum latency 45.2 s (still under the 90 s read timeout) | 01 C1 | Quick reference; 02, 04, 05 |

**Small fixes made in place** are listed in Contradictions #19 and #20 and in each area doc's "Critic additions" section.

**Still open** (none can be settled with live calls today): real 5xx/429/105/199 shapes and the rate limit; whether Cloudflare caches 5xx bodies here; the inner cache's key, lifetime and why it is intermittent; behaviour across a WDI release (does `lastupdated` move on every revision, how long until both cache layers converge, are order and bytes stable); whether an in-range year can ever be absent rather than null; OGHIST contents (needs approval to download, D2); the Data Catalog API (untested); the 87th HIC economy; the 4,000-character URL limit.
