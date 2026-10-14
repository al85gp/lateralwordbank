# 03 — Indicator & Source Metadata, Freshness Signals, Terms

> Area key: `metadata` · Samples: `api_calls/samples/metadata/` · Live calls made 2026-10-14 between 01:11 and 01:17 UTC (2026-10-13 local)
> 34 requests to `api.worldbank.org` plus 1 to `data360api.worldbank.org`, about 1 s apart, with no credentials.
> Tags: **[observed]** means a live call made for this doc (see the Evidence log). **[documented]** means it comes from an official page, linked inline.

## Why the pipeline needs this

- **Analyst-facing definitions.** Analysts need each indicator's name, unit, definition, aggregation method, periodicity and source organisation next to the numbers. The observation payload carries only `indicator.value` (the name) and an empty `unit`.
- **Lineage and traceability.** Every value must trace back to the API response it came from. That needs the WDI release that produced it (the `lastupdated` date), the source ID and name, the request URL, and any per-value footnote (e.g. "Break in series.").
- **Freshness and idempotency.** The pipeline needs a cheap signal that tells it whether WDI published a new release since the last run. A re-run inside the same release should then be a no-op, and a new release should load as a new, identifiable snapshot.
- **Income group.** The assessment compares indicators across income groups by year. The Metadata API exposes `IncomeGroup` per country. This doc records what it returns; the `countries` area owns the country dimension.
- **Compliance.** The license (CC BY 4.0) and attribution obligations must be documented and shipped with outputs.
- **Risk.** The pipeline should know whether the v2 API is being deprecated in favour of Data360, so the extractor can be isolated behind an interface.

## Endpoints & URL templates

Base: `https://api.worldbank.org/v2/`. Always add `format=json`, because the default is XML **[observed]**.

| endpoint | URL template | used by our pipeline? | notes |
|---|---|---|---|
| Indicator (single) | `https://api.worldbank.org/v2/indicator/{code}?format=json` | **Yes** (4 calls) | One row per call. `/v2/indicators/{code}` (plural) returns byte-identical output **[observed]**. Semicolon lists return an empty 200 response (see gotchas). |
| Source (single) | `https://api.worldbank.org/v2/source/{id}?format=json` (also `/sources/{id}`) | **Yes** (1 call, `id=2`) | Release watermark `lastupdated`. Singular and plural paths return the same row **[observed]**. |
| Sources (list) | `https://api.worldbank.org/v2/sources?format=json&per_page=100` | No (reference only) | 71 sources on the fetch date. Default `per_page` is 50, so without the override you get 2 pages. |
| Source concepts | `https://api.worldbank.org/v2/sources/{id}/concepts?format=json` | No | For source 2: Country, Series, Time. |
| Series metadata (Metadata API) | `https://api.worldbank.org/v2/sources/2/series/{code1;code2;...}/metadata?format=json` | **Yes** (or replaced by the next row) | Semicolon lists **work** here. Returns a key/value "metatype" list per series: unit, aggregation, periodicity, license, etc. |
| Country × Series metadata | `https://api.worldbank.org/v2/sources/2/country/{iso3;...}/series/{code;...}/metadata?format=json` | **Yes (recommended single call)** | Returns 3 concepts in one response: `Country` (incl. IncomeGroup, Region), `Country-Series` (per-country source notes) and `Series` (same content as the series call). |
| Country metatype filter | `https://api.worldbank.org/v2/sources/2/country/{iso3;...}/metatypes/incomegroup/metadata?format=json` | Optional | Returns only `IncomeGroup` (current classification, with no year dimension). Documented example `country/usa;jpn/metatypes/incomegroup/metadata` [documented, [Metadata API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886695-metadata-api-queries)]. |
| Footnote metadata | `https://api.worldbank.org/v2/sources/2/footnote/{ISO3}~{SERIES}~YR{yyyy}[;...]/metadata?format=json` | No | Works, but footnotes already arrive inline when the data call uses `footnote=y`. Not documented on the help-desk [Metadata API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886695-metadata-api-queries) page, which has no `/footnote/` path or `~YR` key (it names `FootNote` only as a concept); found by testing. |
| Advanced Data API (BETA) | `https://api.worldbank.org/v2/sources/2/country/{iso3}/series/{code}/time/yr{yyyy}/data?format=json` | No | Also carries `lastupdated`. Labelled BETA in the docs ([Advanced Data API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886686-advanced-data-api-queries)). |
| Observation data (owned by the observations area) | `https://api.worldbank.org/v2/country/{iso3;...}/indicator/{code;...}?format=json&source=2&date=2018:2024&footnote=y&per_page=...` | Yes (other area) | Listed here only for its freshness fields: envelope `lastupdated` and per-row `footnote`. |
| Data360 API (successor platform) | `https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_{CODE_WITH_UNDERSCORES}&REF_AREA={iso3}&top=&skip=` | No | Separate host with a different schema. Comparison only (see Versioning). |

## Parameters

| param | allowed values | default | observed behavior | notes |
|---|---|---|---|---|
| `format` | `json`, `xml`, `jsonP`(+`prefix`), `jsonstat` [documented, [Basic Call Structures](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures)] | `xml` | Without it, `/indicator/SP.POP.TOTL` returns `text/xml` **[observed]**. With `format=json`, the Metadata API error path still returns XML **[observed]**. | Always send `format=json`. Check `Content-Type` too. |
| `per_page` | integer | 50 [documented]; **5000 on the Metadata API** when not sent [observed] | The Metadata API **honours `per_page`** and echoes it as a string: `per_page=10` on the 4-series call gave `pages:6` (total 60), and `per_page=50` on the 9×4 call gave `pages:6` (total 300) **[observed, verifier]**. Pages slice the **flattened metatype entries**, so one variable's metatypes can be **split across pages** (NY.GDP.MKTP.CD: 10 on page 1, 5 on page 2). `/sources` honours `per_page=100` **[observed]**. | Metadata API responses for our scope fit on 1 page **only if `per_page` is not sent (or is ≥ total)**. If paging, merge metatypes by `(concept, variable_id)` across pages. The Metadata API docs show `per_page="5000"` only in example responses and describe no paging parameter ([Metadata API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886695-metadata-api-queries)). |
| `page` | integer ≥ 1 | 1 | Every metadata call in scope returned `pages:1` **[observed]** | Still loop on `pages` defensively. |
| `source` | source ID (WDI = `2`) | none | Has no effect on multi-code `/indicator` metadata (still empty) **[observed]**. On multi-indicator *data* calls the envelope `sourceid` becomes JSON `null` **[observed]**. **Omitting `source` on a multi-indicator data call returns HTTP 200 with JSON message id `"120"` "Invalid value"** **[observed, verifier]**. | Required in practice for multi-indicator data calls (observed). The docs only show multi-indicator queries with `source=2` and do not say explicitly that it is required [documented, [V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api), Basic Call Structures]. |
| `footnote` | `y` / `n` [documented] | omitted, which means no `footnote` key | `footnote=y` adds `"footnote": ""` or text to every row. Without it the key is **absent** **[observed]**. **`footnote=n` does NOT remove it**: the rows still carry `"footnote":"Break in series."`, and the response came back as `Content-Type: application/json` with no charset **[observed, verifier]**. Docs say `footnote=n` removes it ([V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)), so docs and live disagree. **[observed by critic]** A request *without* the parameter can also come back with footnote keys (area 02 C2: the inner cache served the `footnote=y` body). | ~~Always send `footnote=y` so the schema stays stable.~~ **Superseded (README Contradictions #4, decision D4):** sending `footnote=y` does not guarantee a stable schema either way, because the inner cache can ignore the parameter. Treat `footnote` as an optional key. Never rely on `footnote=n`. |
| Semicolon lists in path | `a;b;c` | none | Work for `country/...`, Metadata API `series/...`, `country/...`, `footnote/...`. Do **not** work for `/indicator/{codes}` metadata **[observed]** | Documented limits: 60 indicators per request, 1,500 chars between slashes, 4,000 chars per URL [documented, Basic Call Structures]. |
| Language prefix | `/v2/{lang}/...` e.g. `es`, `fr` | `en` | Not tested | Keep English (no prefix). |
| `downloadformat` | `csv`, `excel`, `xml` | none | Not tested | Returns ZIPs, not used. |

## Response envelope & field dictionary

### A. `/v2/indicator/{code}?format=json`, shape `[envelope, [row]]`

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `[0].page` | number | `1` | no | — | |
| `[0].pages` | number | `1` | no | — | `0` when no match (multi-code call) |
| `[0].per_page` | **string** | `"50"` | no | — | Quoted, unlike `page`/`total` **[observed]** |
| `[0].total` | number | `1` | no | — | `0` when no match |
| `[1][].id` | string | `"SP.POP.TOTL"` | no | `indicator_id` | |
| `[1][].name` | string | `"Population, total"` | no | `indicator_name` | Identical to metadata API `IndicatorName` for all 4 **[observed]** |
| `[1][].unit` | string | `""` | **always empty** for our 4 | `unit_raw` | Do not use for display. Use metadata `Unitofmeasure` **[observed]** |
| `[1][].source.id` | **string** | `"2"` | no | `source_id` | Quoted number |
| `[1][].source.value` | string | `"World Development Indicators"` | no | `source_name` | |
| `[1][].sourceNote` | string | definition text | no | `source_note` | Identical to metadata `Longdefinition` and `Shortdefinition` for all 4 **[observed]**. Contains raw typos (e.g. `estimate.This` in NY.GDP.PCAP.CD). |
| `[1][].sourceOrganization` | string | `"World Population Prospects, United Nations (UN), uri: ...;\nStatistical databases ..."` | no | `source_organization` | Multiple orgs separated by `;\n` (`\u000a` in the raw bytes). Identical to metadata `Source` **[observed]** |
| `[1][].topics[]` | array of objects | `[{"id":"19","value":"Climate Change"},{"id":"8","value":"Health "}]` | can be multi-valued | child table `indicator_topic` | `id` is a string. `value` can have a **trailing space** (`"Health "`) **[observed]** |
| (absent) | — | — | — | — | There is **no `lastupdated`** on this endpoint **[observed]** |

Values for our 4 indicators **[observed]**: all have `source.id="2"` and `unit=""`. Topics: SP.POP.TOTL → `19 Climate Change`, `8 Health `. NY.GDP.MKTP.CD and NY.GDP.PCAP.CD → `3 Economy & Growth`. SP.DYN.LE00.IN → `8 Health `.

### B. `/v2/source/2` and `/v2/sources`, shape `[envelope, [rows]]`

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `[0].page`, `.pages`, `.per_page`, `.total` | **all strings** | `"1"`, `"1"`, `"50"`, `"1"` | no | — | Every envelope number is quoted on this endpoint **[observed]** |
| `[1][].id` | string | `"2"` | no | `source_id` | |
| `[1][].lastupdated` | string `YYYY-MM-DD` | `"2026-10-08"` | no (all 71 populated) | `source_last_updated` (DATE) | Date of the source's most recent update [documented, [V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)]. Equals the observation envelope `lastupdated` **[observed]** |
| `[1][].name` | string | `"World Development Indicators"` | no | `source_name` | |
| `[1][].code` | string | `"WDI"` | no | `source_code` | |
| `[1][].description` | string | `""` | **empty in all 71 rows** | `source_description` | |
| `[1][].url` | string | `""` | **empty in all 71 rows** | `source_url` | |
| `[1][].dataavailability` | string | `"Y"` | `Y`/`N` | `data_available` (bool) | |
| `[1][].metadataavailability` | string | `"Y"` | `Y`/`N`/**`""`** (5 rows) | `metadata_available` | Empty string is the third state |
| `[1][].concepts` | **string** | `"3"` | no | `concept_count` (int) | Quoted number, `"3"` or `"4"` |

### C. Metadata API: `/v2/sources/2/[country/{iso3s}/]series/{codes}/metadata`, a JSON **object**

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `page`, `pages`, `total` | number | `1`, `1`, `60` | no | — | **`total` counts metatype entries, not series or countries** (60 for 4 series; 300 for the country × series call) **[observed]** |
| `per_page` | **string** | `"5000"` | no | — | `"5000"` is the default; a requested value is echoed as a string (e.g. `"10"`, `"50"`) and **is honoured**, with pages slicing metatype entries **[observed, verifier]** |
| `source[].id` | string | `"2"` | no | `source_id` | |
| `source[].name` | string | `"World Development Indicators"` | no | `source_name` | |
| `source[].concept[].id` | string | `"Series"`, `"Country"`, `"Country-Series"`, `"FootNote"` | no | `concept` | |
| `source[].concept[].variable[].id` | string | `"SP.POP.TOTL"`, `"DEU"`, `"USA~SP.POP.TOTL"`, `"DEU~SP.POP.TOTL~YR2023"` | no | `variable_id` | Compound keys are joined with `~` |
| `...variable[].metatype[].id` | string | `"Unitofmeasure"` | no | `metatype` | **Key set varies per variable** (see below) |
| `...variable[].metatype[].value` | string | `"Current US$"` | none empty in our scope **[observed]** | `value` | Always a string, even for years (`"2020"`). Multi-paragraph values contain `\n\n\n\n`. |

**Series metatypes** **[observed]**. Union of keys over our 4 series: `Aggregationmethod, Dataset, Developmentrelevance, IndicatorName, License_Type, License_URL, Limitationsandexceptions, Longdefinition, Othernotes, Periodicity, Referenceperiod, Shortdefinition, Source, Statisticalconceptandmethodology, Topic, Unitofmeasure`. Counts per series: SP.POP.TOTL 16 (only one with `Othernotes`), NY.GDP.MKTP.CD 15, SP.DYN.LE00.IN 15, NY.GDP.PCAP.CD 14 (**no `Limitationsandexceptions`**).

| series | IndicatorName | Unitofmeasure | Aggregationmethod | Periodicity | Referenceperiod | License_Type | Dataset | Topic |
|---|---|---|---|---|---|---|---|---|
| SP.POP.TOTL | Population, total | `Unit` | Sum | Annual | 1960-2025 | CC BY-4.0 | WB_WDI | Health: Population: Structure |
| NY.GDP.MKTP.CD | GDP (current US$) | `Current US$` | Gap-filled total | Annual | 1960-2025 | CC BY-4.0 | WB_WDI | Economic Policy & Debt: National accounts: US$ at current prices: Aggregate indicators |
| NY.GDP.PCAP.CD | GDP per capita (current US$) | `Current US$` | Weighted average | Annual | 1960-2025 | CC BY-4.0 | WB_WDI | (same as above) |
| SP.DYN.LE00.IN | Life expectancy at birth, total (years) | `Years` | Weighted average | Annual | **1960-2024** | CC BY-4.0 | WB_WDI | Health: Mortality |

`License_URL` = `https://datacatalog.worldbank.org/public-licenses#cc-by` for all 4 **[observed]**.

**Country metatypes** (9 countries, all string values) **[observed]**: `2-alphacode, BalanceofPaymentsManualinuse, CurrencyUnit, GovernmentAccountingconcept, IMFdatadisseminationstandard, IncomeGroup, Latestagriculturalcensus, Latestindustrialdata, Latestpopulationcensus, Latesttradedata, LongName, Nationalaccountsbaseyear, PPPsurveyyear, Region, ShortName, SNApricevaluation, SourceofmostrecentIncomeandexpendituredata, SystemofNationalAccounts, Systemoftrade, TableName, WB-2code` appear for all 9. These are sparse: `Nationalaccountsreferenceyear` (6/9), `Vitalregistrationcomplete` (7/9), `Latesthouseholdsurvey` (6/9), `SpecialNotes` (5/9), `ExternaldebtReportingstatus` (4/9), `Lendingcategory` (4/9), `Othergroups` (1/9).

IncomeGroup **[observed]**: BRA, MEX, ZAF = Upper middle income. IND = Lower middle income. CAN, DEU, GBR, JPN, USA = High income. There is no year attribute.

**Country-Series** **[observed]**: 18 variables (`{ISO3}~{SERIES}`), each with a single metatype `Country-Series`. Examples: `USA~SP.POP.TOTL` = "Data source: U.S. Census Bureau", `CAN~SP.DYN.LE00.IN` = "Derived using male and female life expectancy". They exist **only for SP.POP.TOTL and SP.DYN.LE00.IN**; there are none for the GDP series.

**FootNote** **[observed]**: concept `FootNote`, variable `DEU~SP.POP.TOTL~YR2023`, metatype `FootNote` = `"Break in series."`.

### D. Freshness fields on observation responses (cross-reference)

| JSON path | JSON type | example | nullable/empty? | suggested column name | notes |
|---|---|---|---|---|---|
| `[0].lastupdated` (data call) | string `YYYY-MM-DD` | `"2026-10-08"` | not null on success; **`null` in the zero-match envelope** (area 04 #49) | `source_last_updated` | Same value for all 4 indicators and equal to `source/2.lastupdated`, so it is **source-level, not per indicator or per value** **[observed]** |
| `[0].sourceid` (data call) | string or **null** | `"2"` / `null` | **null on multi-indicator calls** | `source_id` | **[observed]** |
| `[0].per_page` (data call) | **number** | `100` | no | — | A number here but a string on `/indicator` and Metadata API envelopes **[observed]** |
| `[1][].footnote` | string | `""` / `"Break in series."` | `""` when none; **key absent** when the param is omitted. **Still present with `footnote=n`** (verifier) | `footnote` | **[observed]** |
| `[1][].value` (cross-ref) | number **or null** | `83287273` / `78.890243902439` / `null` | null when missing (e.g. USA SP.DYN.LE00.IN 2025) | `value` | Integer and float literals mix **within one indicator** (NY.GDP.MKTP.CD in scope: 56 float, 7 integer literals; USA 2025 = `30769700000000`). Land as DOUBLE/DECIMAL explicitly. **[observed, verifier]** |
| `[1][].decimal` (cross-ref) | number | `0` / `1` | no | — | Not the number of decimals in `value`: `0` for SP.DYN.LE00.IN despite 13 decimals; `1` for NY.GDP.PCAP.CD; `0` for POP and GDP **[observed, verifier]** |
| `lastupdated` (Advanced Data API) | string | `"2026-10-08"` | no | — | **[observed]** |

### E. HTTP response headers relevant to freshness [observed]

| header | data call (cache MISS) | data call (HIT, +17 s) | indicator metadata (HIT) | notes |
|---|---|---|---|---|
| `cf-cache-status` | `MISS` | `HIT` | `HIT` | Cloudflare CDN in front of an ASP.NET origin (`x-powered-by: ASP.NET`). Also seen: `EXPIRED`, and `DYNAMIC` on the 301. |
| `Cache-Control` | `public, max-age=86400` | `public, max-age=86400` | `public, max-age=35524` | |
| `Age` | absent | `17` | `78280` | Edge copies can be ~22 h old |
| `Expires` | Date + 86400 s | Date + 86400 s | Date + 35524 s (moves with each request) | |
| `Last-Modified` | **equals the response generation time** (`Wed, 14 Oct 2026 01:12:36 GMT` for a request at 01:12:35) | same as MISS | `Mon, 12 Oct 2026 13:18:37 GMT` | The time the cached copy was rendered, **not** a data-change timestamp. On a HIT it does not always match the fill time implied by `Age`: verifier `source/2` HIT at 01:25:23 had `Age: 21250` (≈ Oct 13 19:31) but `Last-Modified: Tue, 13 Oct 2026 06:20:07 GMT`. All of these postdate `lastupdated` 2026-10-08. |
| `ETag` | **absent** | absent | absent | |
| `Vary` | `accept-encoding` | same | same | |
| `Set-Cookie` | `__cf_bm`, `_cfuvid` (Cloudflare) | same | same | No need to persist. Sample header files have these values redacted. |

## Behavior & gotchas

**Metadata endpoints**
- [observed] `/v2/indicator/{a;b;c;d}` returns **HTTP 200 with `total:0`, `pages:0` and `[]`**. This held for all three variants tried: plain, `&source=2`, and `/v2/source/2/indicator/{a;b;...}`. It does not raise an error, so the extractor must assert one row per requested code. Use 4 single calls, or the Metadata API, which does accept `;`.
- [observed] `/indicator/{code}.unit` is `""` for all four indicators. Real units are only in Metadata API `Unitofmeasure` (`Unit`, `Current US$`, `Years`).
- [observed] `sourceNote` equals both `Longdefinition` and `Shortdefinition`, and `sourceOrganization` equals `Source`. The two metadata paths agree on the fields they share. The Metadata API adds aggregation method, periodicity, reference period, limitations, methodology and license.
- [observed] Metatype keys are **sparse and vary per series and per country**. Loading the Metadata API wide (one column per metatype) gives schema drift. Land it **long** (`concept, variable_id, metatype, value`) and pivot in dbt.
- [observed] Metadata API `total` counts metatype entries (60 for 4 series, 300 for 9×4). It cannot be used as a row-count check for series or countries.
- [observed, verifier] Metadata API **pagination is by metatype entry and honours `per_page`**. Sending `per_page=50` on call #6 gives 6 pages, and page 1 holds only Country `BRA` and part of `CAN`. A variable's metatypes can span two pages. Any REST-client config that sends a fixed `per_page` on every request would silently split variables. Do **not** send `per_page` on Metadata API calls; assert `pages == 1`. If paging is ever needed, union pages and group by `(concept, variable_id)`. Fixtures: `series_metadata_4_indicators_per_page_10_page_{1,2}.json`, `country_series_metadata_9_countries_4_indicators_per_page_50_page_1.json`.
- [observed] `Referenceperiod` is `1960-2025` for POP and both GDP series, but `1960-2024` for life expectancy. 2025 values exist upstream for three of the four indicators: for USA `date=2025`, POP, GDP and GDP per capita have values, while the SP.DYN.LE00.IN 2025 row is **present with `value: null`** [observed, verifier, `data_4_indicators_usa_2025_source2.json`]. Our 2018–2024 window is inside all four ranges.
- [observed] Topic labels differ between endpoints. `/indicator` topics are coarse IDs (`3 Economy & Growth`). The metadata `Topic` is a path string (`Health: Population: Structure`).
- [observed] Text has quirks that must be preserved as-is in raw: trailing spaces (`"Health "`), `\u000a`-encoded newlines, runs of `\n\n\n\n`, and missing spaces (`estimate.This`). Trim and normalise in staging only.
- [observed] `IncomeGroup` comes from the Metadata API and has no time dimension. It is today's classification. [documented] The July 1, 2026 WDI release note says "The World Bank country income classification for FY27 has been announced" ([release note](https://datatopics.worldbank.org/world-development-indicators/release-note/jul-2026.html)), so classifications change once a year. UNVERIFIED: that the API's `IncomeGroup` values are specifically the FY27 classification (inferred from timing; the API carries no fiscal-year attribute). An "income group by year" comparison that uses this field applies the current group to every year. See Open questions.

**Freshness and change detection**
- [documented] `lastupdated` is the date of the source's most recent update ([V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api)).
- [observed] `source/2.lastupdated` = `"2026-10-08"`. That is the same as the observation-envelope `lastupdated` for SP.POP.TOTL, NY.GDP.MKTP.CD and SP.DYN.LE00.IN, the multi-indicator call and the Advanced Data API. The verifier also checked NY.GDP.PCAP.CD (`/country/USA/indicator/NY.GDP.PCAP.CD?format=json&date=2024` → `"lastupdated":"2026-10-08"`). It is a **WDI-wide release date**, not a per-series or per-value revision date. No per-observation revision timestamp exists in v2.
- [documented] The WDI dataset page in the Data Catalog shows "Metadata last updated on Oct 12, 2026" and CSV/Excel files updated Oct 9, 2026, version 131, periodicity Annual ([WDI dataset](https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators)). WDI release notes show a July 1, 2026 refresh with 2025 population and national-accounts data ([jul-2026](https://datatopics.worldbank.org/world-development-indicators/release-note/jul-2026.html)). Search results also list January and April 2026 release notes (e.g. [jan-2026](https://datatopics.worldbank.org/world-development-indicators/release-note/jan-2026.html)); these were not fetched. The `2026-10-08` value means another update landed after July. Cadence is roughly quarterly, with no published forward calendar found.
- [observed, verifier] **Responses are byte-stable within a release.** Cache-busted origin re-renders (forced MISS) of call #6 (37,840 B) and of the 4×9 data call (57,217 B), made ~15 min after the original samples, were **byte-identical** to the saved samples. So `_response_sha256` is a usable "nothing changed" check within a release. Only same-day stability was observed; stability across days within a release is untested. **Superseded (README Contradictions #13):** the same data can arrive as different bytes because the `footnote` key appears or disappears with cache state (area 05 `burst_01`; area 02 C2 [observed by critic]). Use the hash for lineage and raw-file dedupe only; detect change from `lastupdated` plus parsed values.
- [observed] There is **no `ETag`**, and `If-None-Match: *` returns 200 with the full body (re-confirmed by the verifier on `source/2`).
- [observed] `If-Modified-Since` set to the response's `Last-Modified` returned **304** for both a data call and an indicator-metadata call, answered by Cloudflare (`cf-cache-status: HIT`). However, `Last-Modified` is the time the edge or origin rendered the response (MISS at 01:12:35 gave `Last-Modified: 01:12:36`), not when the data changed. A 304 only means "same cached copy"; it does not mean "data unchanged".
- [observed] Edge caching: data responses are sent with `max-age=86400`, and indicator metadata had `Age: 78280` (about 21.7 h). Inference, not directly observed across a release: after a WDI release the API may keep serving pre-release responses until the edge copy expires (up to ~24 h for data calls). Treat `lastupdated` from the response envelope, not wall-clock time, as the version of what was received.
- [observed] Latency: cache HITs take ~0.07 s; MISSes took 0.99 s, 1.14 s and **9.84 s**. The verifier saw MISSes of 12.49 s and 13.07 s, and one MISS (`/country/DEU/indicator/SP.POP.TOTL?...&date=2022:2023&footnote=y`) **exceeded a 30 s client timeout**. Its edge copy appeared with `Last-Modified` 33 s after the request, so the origin took ~33 s. **A 30 s timeout is not enough.** Use a read timeout of ≥ 60–90 s and retry with backoff; the retry was served as an edge HIT in 0.14 s.
- [observed, verifier] **Cache-busting works.** An unknown query parameter (`&cb=<token>`) is accepted (200, byte-identical body) and forces `cf-cache-status: MISS` with `Last-Modified` equal to `Date`. It bypasses the Cloudflare edge copy. Whether the ASP.NET origin has its own output cache behind it is unknown. **Critic update:** there are inner caches behind the edge (area 04 Caching; area 02 C2/C7), and a cache-buster escapes them only sometimes: fresh in V6/V7, synthesis T5, area 02 C4 and the critic's cache-busted `source/2` (`critic__source_2_cachebuster.json`, charset present, `last-modified` = `date`, 0.17 s); served from the inner cache in area 02 V24 and C7. "Works" means "always escapes Cloudflare, usually escapes the origin".

**Versioning and successor (Data360)**
- [documented] V1 was retired in November 2018 and discontinued on June 19, 2020. Calls to old endpoints return "Resource not found". V2 is current, and no key is needed ([About the Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation)).
- [observed] **Docs disagree with live behavior.** An unversioned path (`https://api.worldbank.org/indicator/SP.POP.TOTL?format=json`) returns **HTTP 301** with `Location: https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` and a plain-text body explaining that v1 was discontinued and pointing callers to `/v2`. It does not return "Resource not found". Design: always build URLs with `/v2/` and do not rely on redirects.
- [documented] Data360 About page: "The World Bank's Open Data portal is becoming Data360". It says Data360 "has been merged into ... data.worldbank.org" and is "rolled out in stages", and that legacy features "will be fully incorporated into Data360 over time". The page has **no dates** ([Data360 About](https://data360.worldbank.org/en/about)).
- [documented] The Data360 API runs on a separate host, `https://data360api.worldbank.org`. It is the default base URL in the World Bank's official MCP server ([worldbank/data360-mcp](https://github.com/worldbank/data360-mcp)), and the World Bank's HCI-Plus repo documents `GET /data360/data?DATABASE_ID=&INDICATOR=&REF_AREA=&skip=&top=` with a 1000-row cap and no key ([HCI-Plus API.md](https://github.com/worldbank/HCI-Plus/blob/main/API.md)). The official docs page `https://data360.worldbank.org/en/api` is JS-rendered and could not be read.
- [observed] `GET https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_SP_POP_TOTL&REF_AREA=DEU&top=10` → 200, `{count:66, value:[...]}`. `OBS_VALUE` is a **quoted string**. Rows are not ordered by `TIME_PERIOD`. Columns are SDMX-style (`OBS_STATUS`, `UNIT_MEASURE`, `AGG_METHOD`, `COMMENT_OBS`...). The response is `cf-cache-status: DYNAMIC` with no cache headers. The schema is completely different from v2.
- [documented] No announcement of a v2 retirement date was found as of 2026-10-13. Sources checked: Developer Information overview ([889386](https://datahelpdesk.worldbank.org/knowledgebase/articles/889386-developer-information-overview), which does not mention Data360), Data360 About, and a 2026-07-02 Data360 API guide ([Media Party](https://mediaparty.org/en/2026/07/02/world-bank-data360-api-data-journalism/), which does not mention the v2 API). [observed] v2 is serving current data (`lastupdated` 2026-10-08). Conclusion: v2 is live and current, and Data360 is the strategic successor platform with no published v2 sunset. Keep the extractor behind a source-adapter interface.

**License and terms**
- [observed] Every series' metadata carries `License_Type: "CC BY-4.0"` and `License_URL: "https://datacatalog.worldbank.org/public-licenses#cc-by"`.
- [documented] CC BY 4.0 is the default license for World Bank-produced open datasets. It allows copying, modifying and redistributing, including commercially, and requires appropriate credit and an indication of changes. It also comes with additional dispute-resolution terms: mediation, then arbitration ([Public Licenses](https://datacatalog.worldbank.org/public-licenses)). The WDI dataset page lists the license as CC BY 4.0 ([WDI dataset](https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators)).
- [documented] "The use of the APIs is governed by the Terms and Conditions" ([889386](https://datahelpdesk.worldbank.org/knowledgebase/articles/889386-developer-information-overview)). The WBG Terms of Use (amended July 25, 2025) provide the site, materials and APIs "as is" with no warranty of accuracy ([terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets); the fetched page showed the general WBG terms).
- [documented] "The World Bank API is not guaranteed to have 100% uptime", and callers should cache ([Development Best Practices](https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices)).
- Recommendation (not a legal opinion): outputs should carry the attribution "Source: World Bank, World Development Indicators (source_last_updated = YYYY-MM-DD), CC BY 4.0", plus a note that values were transformed. `sourceOrganization` names the upstream originators (UN WPP, Eurostat, OECD, national statistical offices).

## Failure modes

| scenario | HTTP | body | observed/documented | handling |
|---|---|---|---|---|
| Invalid indicator code on `/indicator/{code}` | **200** | JSON `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]` | [observed]. Docs say id 120 → HTTP 404 ([Error Codes](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes)) | Detect a `message` key in element 0 and raise |
| Invalid series, or no footnote, on the Metadata API | **200** | **`text/xml`** `<wb:error><wb:message id="160" key="Data not found.">` even with `format=json` | [observed]. Docs say id 160 → HTTP 400 with a different message | Check `Content-Type` starts with `application/json` before parsing. For footnotes, treat as "no footnote". |
| Multi-code `/indicator/{a;b}` | 200 | `[{"page":1,"pages":0,...,"total":0},[]]` | [observed] | Assert the expected count |
| Unversioned (v1-style) path | 301 | text/plain notice, `Location` → `/v2/...` | [observed]. Docs say "Resource not found" | Always use `/v2/` |
| Slow edge MISS | 200 (or client timeout) | normal | [observed] 9.84 s, 12.49 s, 13.07 s; one MISS took ~33 s and **timed out a 30 s client** (verifier) | Read timeout ≥ 60–90 s, then retry with backoff (the retry is usually an edge HIT) |
| Multi-indicator data call without `source` | **200** | JSON `[{"message":[{"id":"120","key":"Invalid value",...}]}]` | [observed, verifier] (`data_multi_indicator_without_source_error_120.json`) | Always send `source=2`; detect the `message` key |
| Metadata API called with a small `per_page` | 200 | valid JSON, `pages > 1`, variables split across pages | [observed, verifier] | Do not send `per_page`; assert `pages == 1`, or merge by `(concept, variable_id)` |
| `footnote=n` sent | 200 | footnotes still included; `Content-Type: application/json` without charset | [observed, verifier]; docs say `n` removes them | Send `footnote=y`; match `Content-Type` with a prefix (`application/json`) and not by exact string |
| Service unavailable / unexpected error | 503 (id 105) / 500 (id 199) | not observed | [documented] | Retry with exponential backoff, then fail the run without partial writes |
| Stale CDN copy right after a WDI release | 200 | prior release (inferred) | Inference from `Age`/`max-age` | Record the envelope `lastupdated` per response, and compare it with `source/2.lastupdated` |

## Calls our pipeline will make

Per run (metadata area only). All calls are GET, need no auth, and add the `format=json` query parameter.

| # | purpose | exact URL | requests | pages | rows |
|---|---|---|---|---|---|
| 1 | Release watermark (call first) | `https://api.worldbank.org/v2/source/2?format=json` | 1 | 1 | 1 source row |
| 2–5 | Indicator metadata (one per code; `;` unsupported) | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json`<br>`https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.CD?format=json`<br>`https://api.worldbank.org/v2/indicator/NY.GDP.PCAP.CD?format=json`<br>`https://api.worldbank.org/v2/indicator/SP.DYN.LE00.IN?format=json` | 4 | 1 each | 1 indicator each (+ 1–2 topics) |
| 6 | Series + country + country-series metadata in one call | `https://api.worldbank.org/v2/sources/2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json` | 1 | 1 (default `per_page` 5000; **do not send `per_page`**, because e.g. 50 gives 6 pages) | 300 metatype rows long-format: 9 Country variables, 18 Country-Series, 4 Series; ~38 KB |

Total: **6 requests per run**, under 100 KB. Footnotes need **no extra calls**: request `footnote=y` on the observation calls if README decision D4 adopts it (the README's golden R8–R11 URLs currently omit it). The scope census on 2026-10-14 found 2 of 252 values with footnotes, DEU SP.POP.TOTL 2022 and 2023 "Break in series."; the critic re-confirmed this with fresh `footnote=y` captures of all four golden URLs (`samples/synthesis/obs_*__golden_url_plus_footnote_y.json`) [observed by critic].

**Idempotency and refresh recommendation**
1. Call #1 first and read `lastupdated`.
2. Always pull the full 2018–2024 window. It is only 4 data calls (one per indicator), so incremental logic is not worth its complexity.
3. Key raw snapshots by `(source_id, source_last_updated)`. Merge or replace by `(indicator_id, country_iso3, year, source_last_updated)`. A re-run on the same release then rewrites identical rows, and a new release produces a new version.
4. Assert that each observation response's envelope `lastupdated` equals the value from call #1. On mismatch (CDN skew), warn and re-request or fail. **[observed by critic]** A plain re-request returns the same edge copy, so the re-request needs a fresh cache-buster token (area 02 C3/C4). Call #1 is itself edge-cached: at 01:59Z the exact R0 URL was a `HIT` rendered at 06:20Z the previous day (`critic__source_2_recheck.json`, `age 23275`), while `source/2?format=json&cb=…` gave a fresh render in 0.17 s with the same `lastupdated` (`critic__source_2_cachebuster.json`).
5. Do not rely on `ETag`, which is absent, or on `Last-Modified`/304, which reflect cache state rather than data changes.

### Recommended raw-layer landing (lineage + analyst-facing definitions)

Each table keeps the following lineage columns: `_request_url`, `_http_status`, `_content_type`, `_fetched_at_utc`, `_run_id`, `_response_sha256`. Keep the raw response body as well (file or JSON column) so any value can be re-derived.

| raw table | grain | from call | key columns to keep |
|---|---|---|---|
| `raw_wb__source` | source × fetch | #1 | `source_id`, `source_code`, `source_name`, `source_last_updated` (DATE), `data_available`, `metadata_available`, `concept_count` |
| `raw_wb__indicator` | indicator × fetch | #2–5 | `indicator_id`, `indicator_name`, `unit_raw` (expected `''`), `source_id`, `source_name`, `source_note`, `source_organization` |
| `raw_wb__indicator_topic` | indicator × topic | #2–5 | `indicator_id`, `topic_id`, `topic_value` (raw, untrimmed) |
| `raw_wb__metadata_kv` | (concept, variable, metatype) × fetch | #6 | `source_id`, `concept` (`Series`/`Country`/`Country-Series`), `variable_id`, `metatype`, `value`. Long format to absorb sparse keys. |

Staging/marts (dbt) then pivot `raw_wb__metadata_kv` into:
- **`dim_indicator`**: `indicator_id`, `indicator_name`, `unit_of_measure` (from `Unitofmeasure`, **not** `/indicator.unit`), `short_definition`, `long_definition`, `aggregation_method`, `periodicity`, `reference_period`, `limitations` (nullable), `topic`, `source_id`, `source_name`, `source_organization`, `license_type`, `license_url`, `source_last_updated`.
- `dim_country_series_note` (`country_iso3`, `indicator_id`, `note`). This is optional and only covers POP and LE.
- Every fact row carries `source_id`, `source_last_updated` (from its own envelope), `footnote`, and a pointer to its raw response (`_response_sha256` / `_request_url`).

## Evidence log

All calls were made 2026-10-14 between 01:11:13 and 01:17:00 UTC with `curl -sS -m 30`. Samples are in `api_calls/samples/metadata/` and each has a `.meta.json` sidecar. Header files have `set-cookie` values redacted.

| # | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` | 200 | `per_page:"50"` (string), `unit:""`, topics include `"Health "`, no lastupdated. cf HIT, age 78280, max-age 35524, no ETag | `indicator_sp_pop_totl.json`, `headers_indicator_metadata_cache_hit.txt` |
| 2 | `https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.CD?format=json` | 200 | `unit:""`, topic `3 Economy & Growth` | `indicator_ny_gdp_mktp_cd.json` |
| 3 | `https://api.worldbank.org/v2/indicator/NY.GDP.PCAP.CD?format=json` | 200 | `unit:""`, sourceNote typo `estimate.This` | `indicator_ny_gdp_pcap_cd.json` |
| 4 | `https://api.worldbank.org/v2/indicator/SP.DYN.LE00.IN?format=json` | 200 | `unit:""`, topic `8 Health ` | `indicator_sp_dyn_le00_in.json` |
| 5 | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?format=json` | 200 | `{"page":1,"pages":0,"per_page":"50","total":0},[]` | `indicator_multi_semicolon_returns_empty.json` |
| 6 | same as #5 + `&source=2` | 200 | still total 0 | (same as #5) |
| 7 | `https://api.worldbank.org/v2/sources?format=json&per_page=100` | 200 | 71 sources. Envelope all strings. description/url `""`. metadataavailability `""` in 5 rows. | `sources_list_per_page_100.json` |
| 8 | `https://api.worldbank.org/v2/source/2?format=json` | 200 | `"lastupdated":"2026-10-08"` | `source_2_wdi.json` |
| 9 | `https://api.worldbank.org/v2/source/2/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?format=json` | 200 | total 0 (empty) | (same as #5) |
| 10 | `https://api.worldbank.org/v2/sources/2/concepts?format=json` | 200 | object envelope, `per_page:50` (number), concepts Country/Series/Time | `source_2_concepts.json` |
| 11 | `https://api.worldbank.org/v2/sources/2/series/SP.POP.TOTL/metadata?format=json` | 200 | 16 metatypes incl. `License_Type:"CC BY-4.0"`. `per_page:"5000"` | `series_metadata_sp_pop_totl.json` |
| 12 | `https://api.worldbank.org/v2/sources/2/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json` | 200 | semicolons work. total 60 = metatype count. LE `Referenceperiod` 1960-2024 | `series_metadata_4_indicators.json` |
| 13 | `https://api.worldbank.org/v2/sources/2/country/USA;JPN/series/SP.POP.TOTL/metadata?format=json` | 200 | Country + Country-Series + Series concepts. `USA~SP.POP.TOTL` = "Data source: U.S. Census Bureau" | `country_series_metadata_usa_jpn_sp_pop_totl.json` |
| 14 | `https://api.worldbank.org/v2/sources/2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json` | 200 | 37,840 B. total 300. 18 Country-Series notes, only for POP and LE | `country_series_metadata_9_countries_4_indicators.json` |
| 15 | `https://api.worldbank.org/v2/sources/2/footnote/USA~SP.POP.TOTL~YR2020/metadata?format=json` | 200 | **text/xml** error id 160 "Data not found." | `footnote_metadata_usa_sp_pop_totl_2020_not_found.xml` |
| 16 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL?format=json&date=2018:2024&footnote=y&per_page=100` | 200 | envelope `sourceid:"2"`, `lastupdated:"2026-10-08"`, `per_page:100` (number). DEU 2022/2023 footnote "Break in series.". cf MISS, 0.99 s, Last-Modified = generation time | `data_sp_pop_totl_9_countries_2018_2024_footnote_y.json`, `headers_data_call_cache_miss.txt` |
| 17 | `https://api.worldbank.org/v2/sources/2/footnote/DEU~SP.POP.TOTL~YR2023/metadata?format=json` | 200 | FootNote = "Break in series." | `footnote_metadata_deu_sp_pop_totl_2023.json` |
| 18 | same as #16 (repeat) | 200 | cf HIT, `age: 17`, body byte-identical | `headers_data_call_cache_hit.txt` |
| 19 | same as #16 + `If-Modified-Since: Wed, 14 Oct 2026 01:12:36 GMT` | **304** | empty body, cf HIT | `headers_conditional_if_modified_since_data_304.txt` |
| 20 | #1 URL + `If-Modified-Since: Mon, 12 Oct 2026 13:18:37 GMT` | **304** | cf HIT, age 78391 | `headers_conditional_if_modified_since_indicator_304.txt` |
| 21 | same as #16 + `If-None-Match: *` | 200 | full body. No ETag support | `headers_conditional_if_none_match_star_200.txt` |
| 22 | `https://api.worldbank.org/v2/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023` | 200 | **no `footnote` key** without the param. cf MISS, **9.84 s** | `data_sp_pop_totl_deu_2022_2023_no_footnote_param.json` |
| 23 | `https://api.worldbank.org/indicator/SP.POP.TOTL?format=json` | **301** | Location → `/v2/indicator/...`. Text body says v1 was discontinued June 19, 2020 | `unversioned_path_redirect_301.txt`, `headers_unversioned_redirect_301.txt` |
| 24 | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL` | 200 | default `text/xml` (`<wb:indicators page="1" ...>`) | `indicator_sp_pop_totl_default_format.xml` |
| 25 | `https://api.worldbank.org/v2/sources/2/series/NOT.A.CODE/metadata?format=json` | 200 | text/xml error id 160 | `series_metadata_invalid_code_xml_error.xml` |
| 26 | `https://api.worldbank.org/v2/indicator/NOT.A.CODE?format=json` | 200 | JSON message id `"120"` "Invalid value" | `indicator_invalid_code_error.json` |
| 27 | `https://api.worldbank.org/v2/sources/2/country/DEU/series/SP.POP.TOTL/time/yr2023/data?format=json` | 200 | `"lastupdated":"2026-10-08"`, value 83287273 (number) | `advanced_data_deu_sp_pop_totl_2023.json` |
| 28 | `https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_SP_POP_TOTL&REF_AREA=DEU&top=10` | 200 | `count:66`. `OBS_VALUE` string. unordered. cf DYNAMIC | `data360_wdi_sp_pop_totl_deu_top10.json` |
| 29 | `https://api.worldbank.org/v2/sources/2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/metatypes/incomegroup/metadata?format=json` | 200 | 9 IncomeGroup values, no year | `country_incomegroup_metatype_9_countries.json` |
| 30 | `https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.CD?format=json&date=2024` | 200 | envelope `lastupdated:"2026-10-08"` (same as POP) | — (scratch only) |
| 31 | `https://api.worldbank.org/v2/country/USA/indicator/SP.DYN.LE00.IN?format=json&date=2024` | 200 | envelope `lastupdated:"2026-10-08"`. `decimal:0` while value is `78.890243902439` | — (scratch only) |
| 32 | `https://api.worldbank.org/v2/indicators/SP.POP.TOTL?format=json` | 200 | byte-identical to #1 | (same as #1) |
| 33 | `https://api.worldbank.org/v2/sources/2?format=json` | 200 | same row as `/source/2` | `sources_2_wdi_plural_path.json` |
| 34 | `https://api.worldbank.org/v2/sources/2/footnote/DEU~SP.POP.TOTL~YR2022;DEU~SP.POP.TOTL~YR2023/metadata?format=json` | 200 | semicolon keys work, 2 footnotes | `footnote_metadata_deu_sp_pop_totl_2022_2023.json` |
| 35 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?format=json&source=2&date=2018:2024&footnote=y&per_page=300` | 200 | 252 rows, **`sourceid:null`**, `lastupdated:"2026-10-08"`. 2 footnotes, 0 null values, all `obs_status:""`. cf MISS 1.14 s | `data_4_indicators_9_countries_2018_2024_footnote_y_source2.json` |
| V1 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2024` (no `source`) | 200 | JSON message id `"120"` "Invalid value". cf MISS | `data_multi_indicator_without_source_error_120.json` |
| V2 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2024&source=2` | 200 | `sourceid:null`, `lastupdated:"2026-10-08"`. cf MISS **12.49 s** | — (scratch only) |
| V3 | `https://api.worldbank.org/v2/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023&footnote=y` | timeout, then 200 | 1st try: curl timeout at 30 s (MISS). Retry 25 s later: HIT, `age: 22`, `Last-Modified 01:26:46` (≈ 33 s after the 1st request) | — (scratch only) |
| V4 | `https://api.worldbank.org/v2/sources/2/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json&per_page=10` (+ `&page=2`) | 200 | `per_page:"10"`, `pages:6`, `total:60`. NY.GDP.MKTP.CD split across pages 1–2 | `series_metadata_4_indicators_per_page_10_page_1.json`, `series_metadata_4_indicators_per_page_10_page_2.json` |
| V5 | call #6 URL + `&per_page=50` | 200 | `per_page:"50"`, `pages:6`, `total:300`. Page 1 = BRA + part of CAN | `country_series_metadata_9_countries_4_indicators_per_page_50_page_1.json` |
| V6 | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json&cb=verify20261014a` | 200 | unknown param accepted, cf MISS, `Last-Modified` = `Date`, body byte-identical to #1 | `headers_cache_busted_indicator_miss.txt` |
| V7 | call #6 URL + `&cb=verify20261014a`; #35 URL + `&cb=verify20261014a` | 200 / 200 | origin re-renders (MISS, 0.35 s / **13.07 s**) **byte-identical** to the samples from #14 and #35 | — (compared against existing samples) |
| V8 | `https://api.worldbank.org/v2/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023&footnote=n` | 200 | `footnote` key **still present** ("Break in series."). `Content-Type: application/json` (no charset) | `data_sp_pop_totl_deu_2022_2023_footnote_n.json` |
| V9 | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?format=json&source=2&date=2025` | 200 | 2025: POP, GDP and GDP pc present; LE `value:null`; GDP integer literal `30769700000000` | `data_4_indicators_usa_2025_source2.json` |
| V10 | `https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.PCAP.CD?format=json&date=2024` | 200 | `lastupdated:"2026-10-08"`, `sourceid:"2"` | — (scratch only) |
| V11 | `https://api.worldbank.org/v2/source/2?format=json` with `If-Modified-Since: Thu, 08 Oct 2026 00:00:00 GMT`; cache-busted `source/2` with `If-Modified-Since` = its own `Last-Modified` | 200 / **304** | IMS before `Last-Modified` → 200; IMS equal → 304 from edge HIT | — (scratch only) |

## Open questions

1. **Income group by year.** The API exposes only the current `IncomeGroup` (UNVERIFIED: FY27, inferred from the July 2026 release note announcing the FY27 classification). Should the analysis use the current classification for all years 2018–2024 (simple and reproducible)? Or should it use historical classifications, which these endpoints do not provide? The historical source would have to be identified; that is unknown here and probably a non-API file.
2. **Does `lastupdated` change on every revision?** We could not observe whether WDI changes values for 2018–2024 without bumping `source/2.lastupdated`, or whether a bump always means some value changed. Verifying this needs two runs that straddle a release.
3. **CDN skew window.** How long after a release can the edge keep serving pre-release responses? The ~24 h figure is inferred from `max-age=86400` and `Age`, not observed across a release. A cache-busting query parameter (`&cb=<token>`) was tested by the verifier: it is accepted and forces an edge MISS with an identical body. Still open: is it acceptable etiquette (it defeats the CDN and costs 1–33 s per call), and does the origin have its own cache behind the edge? *Critic update:* yes, the origin has inner caches (area 04 Caching; area 02 C2/C7). The edge copy of R0 seen at 01:59Z was ~19.7 h old; a cache-busted R0 cost 0.17 s. Still not observed: an actual release crossing either cache layer.
4. **Data360 timeline.** There is no dated v2 sunset or migration guide; the official `data360.worldbank.org/en/api` page is JS-rendered and unreadable here. Check that page and the Data Help Desk again before submission. Are Data360 `WB_WDI` values identical to v2 values for our scope? Only one indicator/country was sampled, and the 2018–2024 values were not compared.
5. **Metadata API stability.** The `footnote/{key}/metadata` pattern is not on the help-desk Metadata API page. It was found by testing, so its support status is unknown; the pipeline should not depend on it.
6. **Unit display.** Should analyst-facing `unit_of_measure` use the metadata strings as-is (`Unit` for population is not very descriptive), or a curated mapping (e.g. "people", "current US$", "years")?
7. **Attribution format.** Confirm the exact attribution line the reviewers expect in deliverables. The World Bank's recommended citation text on the WDI "Citations" tab could not be read.

## Verification

Adversarial verification on **2026-10-14 UTC** (01:23–01:31 UTC; 2026-10-13 local). It used 33 fresh live requests to `api.worldbank.org` and 1 to `data360api.worldbank.org`, about 1 s apart and with no credentials, plus reading the official docs and every saved sample. Full-body comparisons used `cmp` against the saved samples. Cache-busted requests (`&cb=verify20261014a`) forced origin renders, so a byte match is not just an edge replay of the original agent's call.

**Sample-file audit.** All 33 sample files named in the doc exist. All `.json` bodies parse, and each has a `.meta.json` sidecar with `url`, `http_status`, `content_type`, `fetched_at_utc` and `notes`. No unreferenced files were found. The verifier added 7 fixtures, each with a sidecar (see Evidence log V1–V11).

| claim | result | URL or doc used |
|---|---|---|
| `/indicator/{code}` envelope: `per_page` is the string `"50"`; page/pages/total are numbers | confirmed | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` (body byte-identical to sample) |
| `/indicator` `unit` is `""` for all 4; no `lastupdated`; topics incl. `"Health "` with trailing space | confirmed | 4 indicator samples + live re-fetch of SP.POP.TOTL |
| `/indicator/{a;b}` returns 200, `total:0`, `[]` (also via `/source/2/indicator/...`) | confirmed | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json`, `https://api.worldbank.org/v2/source/2/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json` |
| `/indicators/{code}` (plural) is byte-identical | confirmed | `https://api.worldbank.org/v2/indicators/SP.POP.TOTL?format=json` |
| Invalid indicator → 200 JSON message id `"120"`; docs say 404 | confirmed | `https://api.worldbank.org/v2/indicator/NOT.A.CODE?format=json`; [Error Codes](https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes) (120 → 404, 160 → 400, 105 → 503, 199 → 500) |
| Metadata API invalid series / missing footnote → 200 `text/xml` id 160 even with `format=json` | confirmed | `https://api.worldbank.org/v2/sources/2/series/NOT.A.CODE/metadata?format=json`, `https://api.worldbank.org/v2/sources/2/footnote/USA~SP.POP.TOTL~YR2020/metadata?format=json` |
| Unversioned path → 301 to `/v2/...` with a text body; docs say "Resource not found" | confirmed | `https://api.worldbank.org/indicator/SP.POP.TOTL?format=json`; [About the Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) |
| `source/2.lastupdated = "2026-10-08"`; `/source` envelope all strings | confirmed | `https://api.worldbank.org/v2/source/2?format=json` (HIT and cache-busted MISS) |
| `/sources`: 71 rows; default `per_page` 50 gives 2 pages | confirmed | `https://api.worldbank.org/v2/sources?format=json` → `pages:"2"`, `total:"71"` |
| `/sources` `description`/`url` empty in all 71; `metadataavailability` `""` in 5; `concepts` quoted `"3"`/`"4"` | confirmed | `sources_list_per_page_100.json` (re-derived) |
| Metadata API `per_page` is `"5000"` "regardless" | **corrected**: `"5000"` is only the default. `per_page` is honoured (10 → 6 pages; 50 on call #6 → 6 pages) and pages split variables | `.../sources/2/series/{4 codes}/metadata?format=json&per_page=10` (+`&page=2`), `.../sources/2/country/{9}/series/{4}/metadata?format=json&per_page=50`; [Metadata API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886695-metadata-api-queries) |
| Metadata API `total` counts metatype entries (60 / 300) | confirmed | same calls; 300 = 222 Country + 18 Country-Series + 60 Series |
| All Metadata API values are non-empty JSON strings in scope | confirmed | `country_series_metadata_9_countries_4_indicators.json` (300/300 strings, 0 empty) |
| Series metatype counts 16/15/15/14; NY.GDP.PCAP.CD lacks `Limitationsandexceptions`; only SP.POP.TOTL has `Othernotes` | confirmed | same sample, plus a cache-busted origin re-render that was byte-identical |
| Series table cells (Unitofmeasure, Aggregationmethod, Periodicity, Referenceperiod incl. LE `1960-2024`, License, Dataset, Topic) | confirmed | same |
| Country metatype key set and sparse counts (7/9, 6/9, 6/9, 5/9, 4/9, 4/9, 1/9); IncomeGroup values | confirmed | same + `https://api.worldbank.org/v2/sources/2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF/metatypes/incomegroup/metadata?format=json` |
| 18 Country-Series notes, only for POP and LE | confirmed | same |
| `sourceNote` = `Longdefinition` = `Shortdefinition`; `sourceOrganization` = `Source`; `name` = `IndicatorName` (all 4) | confirmed | indicator samples vs metadata sample |
| Text quirks: `\u000a` in raw bytes, `\n\n\n\n` runs, `estimate.This` | confirmed | raw bytes of samples |
| Observation envelope `lastupdated` equal for all 4 indicators | confirmed (NY.GDP.PCAP.CD newly checked) | `https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.PCAP.CD?format=json&date=2024` |
| `sourceid` is `null` on multi-indicator data calls | confirmed | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2024&source=2` |
| "Docs require `source` for multi-indicator data calls [documented]" | **corrected**: the docs only *show* `source=2` and never say it is required. Live: omitting it → 200 + message id 120 [observed] | `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD?format=json&date=2024`; [V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api); [Basic Call Structures](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures) |
| `footnote` key absent without the param; `footnote` allowed `y`/`n` | confirmed absent without the param. **Corrected/added**: `footnote=n` still returns footnotes (docs say it removes them) | `https://api.worldbank.org/v2/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023` and `...&footnote=n` |
| `decimal` is 0 for life expectancy despite many decimals | confirmed; **added**: `decimal` is 1 for NY.GDP.PCAP.CD, and `value` mixes integer/float literals | `data_4_indicators_9_countries_2018_2024_footnote_y_source2.json`, `https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN?format=json&source=2&date=2025` |
| 2025 values exist for 3 of 4 indicators | confirmed (USA): LE 2025 row present with `value:null` | same 2025 URL |
| No `ETag`; `If-None-Match: *` → 200 | confirmed | `https://api.worldbank.org/v2/source/2?format=json` with `If-None-Match: *` |
| `If-Modified-Since` → 304 from edge; not a data-change signal | confirmed (IMS = `Last-Modified` → 304; IMS before → 200) | `source/2` with and without the cache-buster |
| `Last-Modified` = response generation time on MISS | confirmed (cache-busted MISS: `Last-Modified` = `Date`). **Refined** for HITs: can predate the `Age`-implied fill time | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json&cb=verify20261014a`; `source/2` HIT |
| `Expires` moves with each request at a fixed `max-age=35524` on indicator metadata; edge copies ~22 h old | confirmed (01:25:30 → Expires 11:17:34; `Age: 79136`) | `https://api.worldbank.org/v2/indicator/SP.POP.TOTL?format=json` |
| Latency: "use a timeout of at least 30 s" | **corrected** → ≥ 60–90 s: one MISS exceeded 30 s (~33 s at origin); others 12.49 s / 13.07 s | `https://api.worldbank.org/v2/country/DEU/indicator/SP.POP.TOTL?format=json&date=2022:2023&footnote=y` |
| Cache-busting param "untested" (Open question 3) | **corrected**: tested. Accepted, forces MISS, byte-identical body | `...&cb=verify20261014a` on `/indicator`, `/source/2`, call #6, call #35 |
| Re-runs within a release return identical bytes (idempotency) | confirmed (new, same-day only) | cache-busted call #6 and #35 vs samples |
| Advanced Data API carries `lastupdated:"2026-10-08"`, value a number; labelled BETA | confirmed | `https://api.worldbank.org/v2/sources/2/country/DEU/series/SP.POP.TOTL/time/yr2023/data?format=json`; [Advanced Data API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886686-advanced-data-api-queries) |
| Data360: `count:66`, `OBS_VALUE` string, unordered, `cf-cache-status: DYNAMIC`, no cache headers | confirmed | `https://data360api.worldbank.org/data360/data?DATABASE_ID=WB_WDI&INDICATOR=WB_WDI_SP_POP_TOTL&REF_AREA=DEU&top=10` |
| Footnote-metadata path undocumented; `metatypes/incomegroup` documented | confirmed | [Metadata API Queries](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886695-metadata-api-queries) |
| V1 retired Nov 2018, discontinued June 19, 2020, "Resource not found", no key needed | confirmed | [889392](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) |
| `lastupdated` = "date of the source's most recent update" | confirmed | [V2 features](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api) |
| Formats incl. `jsonstat`, JSONP needs `prefix`; default 50/page; limits 60 indicators / 1,500 chars / 4,000 chars | confirmed | [Basic Call Structures](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures) |
| "not guaranteed to have 100% uptime"; cache results | confirmed | [Development Best Practices](https://datahelpdesk.worldbank.org/knowledgebase/articles/902064-development-best-practices) |
| "use of the APIs is governed by the Terms and Conditions"; 889386 does not mention Data360 | confirmed | [889386](https://datahelpdesk.worldbank.org/knowledgebase/articles/889386-developer-information-overview) |
| WBG Terms amended July 25, 2025; site, materials and APIs provided "as is" | confirmed (the page title is "Terms & Conditions \| World Bank Group") | [terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets) |
| CC BY 4.0 default; attribution + indicate changes; commercial use OK; mediation then arbitration | confirmed | [Public Licenses](https://datacatalog.worldbank.org/public-licenses) |
| WDI catalog: metadata Oct 12, 2026; CSV/Excel Oct 9, 2026; version 131; Annual; CC BY 4.0 | confirmed | [WDI dataset](https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators) |
| Data360 About quotes; no dates | confirmed | [Data360 About](https://data360.worldbank.org/en/about) |
| "July 2026 release added the FY27 income classification" | **corrected** wording → the release note says the FY27 classification "has been announced". UNVERIFIED that the API `IncomeGroup` is FY27 | [jul-2026 release note](https://datatopics.worldbank.org/world-development-indicators/release-note/jul-2026.html) |
| No v2 retirement date announced | unverified (a search found none, but absence cannot be proven) | web search 2026-10-14; Data360 About; 889386 |
| Jan/Apr 2026 release notes exist | unverified (not fetched by verifier either) | — |
| Default format is XML | confirmed from saved sample/meta only (not re-called) | `indicator_sp_pop_totl_default_format.xml` |

## Critic additions

Completeness-critic pass, 2026-10-14 01:59 UTC: 2 live requests in this area (`curl -sS -m 90`, no credentials), plus cross-references to the critic's calls in areas 01 and 02. Samples are in `samples/metadata/`, each with a `.meta.json` and a `.headers.txt` (cookie values redacted).

| C# | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| C1 | `https://api.worldbank.org/v2/source/2?format=json` (exact R0) | 200 | cf `HIT`, `age: 23275`, `max-age=38935`, `last-modified: Tue, 13 Oct 2026 06:20:07 GMT`. Body byte-identical to `source_2_wdi.json` (`lastupdated` `"2026-10-08"`) | `critic__source_2_recheck.json` |
| C2 | `https://api.worldbank.org/v2/source/2?format=json&cb=critic20261014a` | 200 | cf `MISS`, `application/json;charset=utf-8`, `last-modified` = `date`, `max-age=86400`, 0.17 s. Same body | `critic__source_2_cachebuster.json` |

**Change detection for the production note [inference from C1/C2 and area 02 C1–C4]:**
- The release signal is `source/2.lastupdated` (WDI-wide, date only). Its plain URL is edge-cached like everything else, so a run can see a release up to ~24 h late. One cache-busted `source/2` request is a cheap (≈0.2 s, 236 bytes) freshness probe for a scheduler: "has `lastupdated` moved since the last successful load?"
- When it has moved, the observation URLs may still be served pre-release copies from either cache layer. The per-response assertion `envelope.lastupdated == R0.lastupdated` catches this; recover by re-requesting with a fresh cache-buster per attempt, and if the skew persists, defer the load rather than mixing releases.
- Not observed and still open: whether `lastupdated` moves on every value revision (open question 2), and how long after a release both layers converge (open question 3). Only one release (`2026-10-08`) was ever seen.

Cross-file corrections made here: the "always send `footnote=y` for a stable schema" and "`_response_sha256` is a nothing-changed check" recommendations now point to README Contradictions #4 and #13; "Cache-busting works" is qualified; "~2 data calls" corrected to 4; zero-match `lastupdated` nullability added.
