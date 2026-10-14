# 01 — Countries, regions, income levels & lending types

Area key: `countries` · Samples: `api_calls/samples/countries/` · Live calls made: 35 (2026-10-14 01:11–01:18 UTC), plus 41 verification calls (2026-10-14 01:24–01:31 UTC, see "Verification"), plus 8 completeness-critic calls (02:00–02:03 UTC, see "Critic additions") · API: World Bank Indicators API v2, `https://api.worldbank.org/v2/`

Tags: **[observed]** = called live today (URL + HTTP status + excerpt in the Evidence log). **[documented]** = stated in official docs (URL cited). **[secondary]** = third-party source, needs confirmation. **[inference]** = our reasoning from observed data, not stated anywhere.

---

## Why the pipeline needs this

- **Country dimension.** For each of the 9 economies (BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF) we need name, ISO3/ISO2, region, **income level** and lending type.
- **Income-group comparison by year.** The assessment asks us to compare indicators across income groups by year. That depends on what the API returns for income classification. It returns the **current classification only**, with no year dimension (see "Behavior & gotchas" section B).
- **Join keys.** Indicator observations (area 02) identify a country differently from the country endpoint. This doc pins down which key joins to which.
- **Aggregate filtering.** The country list and `country/all` mix 79 aggregates (WLD, HIC, EUU, …) with 217 real economies. We must be able to tell them apart.
- **Optional benchmark.** Income-group aggregates (HIC/UMC/LMC/LIC) can be queried as "countries" in the indicator endpoint. They give World Bank-computed group totals to compare against.

---

## Endpoints & URL templates

All calls use `format=json`. Without it the API returns XML (see section E).

| Endpoint | URL template | Used by our pipeline? | Notes |
|---|---|---|---|
| Country (selected) | `https://api.worldbank.org/v2/country/{codes}?format=json&per_page=50` | **YES (core)** | `{codes}` = `;`-separated ISO3 or ISO2, case-insensitive. Returns 9 rows in 1 page. |
| Country (all) | `https://api.worldbank.org/v2/country?format=json&per_page={n}&page={p}` | Optional | 296 rows (217 economies + 79 aggregates). Use for a full dimension or to validate codes. |
| Country filtered by group (URL style) | `https://api.worldbank.org/v2/incomeLevel/{id}/country?format=json&per_page=100` | Optional | Members of an income level, **current** classification. Also works for `region/{code}/country` (byte-identical to `country?region=` [observed]) and `lendingType/{id}/country`, **but the lending-type filter returns duplicate rows** for IBD and IDX (see section A) [observed]. The docs state argument- and URL-style are equivalent only with the `incomeLevel=LIC` example [documented, 898581]. |
| Country filtered by group (argument style) | `https://api.worldbank.org/v2/country?incomeLevel={id}&format=json&per_page=100` | Optional | Byte-identical to the URL-style result [observed]. Docs also show `country?region=LCN`, `country?incomelevel=UMC`, `country?lendingtype=IBD` [documented, 898614]. `lendingtype=IBD` returns every economy twice (total 134, 67 unique) [observed]. |
| Income levels | `https://api.worldbank.org/v2/incomeLevel?format=json` | **YES (reference dim)** | 7 rows. `incomelevel` (lowercase) is identical. |
| Income level (single) | `https://api.worldbank.org/v2/incomeLevel/{id}?format=json` | No | |
| Regions | `https://api.worldbank.org/v2/region?format=json&per_page=100` | Optional | 44 rows. **Different record shape** (`code`/`name`, not `id`/`value`). |
| Region (single) | `https://api.worldbank.org/v2/region/{code}?format=json` | No | |
| Lending types | `https://api.worldbank.org/v2/lendingType?format=json` | Optional | 4 rows. `lendingtypes` (plural, as in the docs [documented, 898614]) is byte-identical. |
| Indicator for aggregates | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/{indicator}?format=json&date=2018:2024&per_page=100` | **Optional benchmark** | 4 groups × 7 years = 28 rows. Aggregates use **current** membership for every year [observed, section C]. |
| WDI country metadata | `https://api.worldbank.org/v2/sources/2/country/{iso3}/metadata?format=json` | No | Has an `IncomeGroup` field, also current-only (no year). |
| Language prefix | `https://api.worldbank.org/v2/{lang}/country/{codes}?format=json` | **No: always English** | Translates labels and some values. Ids are unchanged. |
| Historical income classification (NOT the Indicators API) | Data Catalog resource DR0095334 "Historical classification by income" (OGHIST), Excel: `https://ddh-openapi.worldbank.org/resources/DR0095334/download` | **Decision needed** | The only official source of classification **by year**. Excel file, not part of the REST API (section B). |

---

## Parameters

| Param | Allowed values | Default | Observed behavior | Notes |
|---|---|---|---|---|
| `{codes}` path segment | ISO3 (`BRA`), ISO2 (`BR`), aggregate codes (`WLD`, `HIC`, `EUU`…), `all`; joined by `;` | — | Case-insensitive (`bra`, `BRA`, `br` give byte-identical bodies). ISO2 and ISO3 can be mixed in one request. Duplicates are removed (`BRA;bra;BR` → total 1). Results come back **sorted by `id` ascending, not in request order**. One unknown code fails the **whole** request with error 120. | Docs: max 1,500 chars between slashes, 4,000 chars per URL [documented, 898581]. Our 9 codes are 35 chars. |
| `format` | `json`, `xml`, `jsonP` (+`prefix`), `jsonstat` | **xml** | Without it: `text/xml`, root `<wb:countries>`. | Always send `format=json`. `jsonstat` is documented as BETA and only for indicator data queries [documented, 1886674]. |
| `per_page` | integer | 50 | Echoed as a **string** in country/reference envelopes (`"per_page":"50"`). `per_page=500` and `per_page=1000` both returned all 296 countries in one page. `per_page=abc` → error 120, HTTP 200. | Upper limit not probed on `/country`. On the observation endpoint the ceiling is **32767** (32768 → HTTP 400 HTML) [observed, areas 04/05]. |
| `page` | integer ≥1 | 1 | `page=31` when `pages=30` → HTTP 200, `[{"page":31,"pages":30,…},[]]` (empty list, no error). | Stop on `page >= pages`. Don't rely on an empty page. |
| `incomeLevel` / `region` / `lendingType` (query args on `/country`) | group ids | — | `incomeLevel=HIC` gives the same result as `/incomeLevel/HIC/country`; `region=LCN` is byte-identical to `/region/LCN/country`. Path segment and code are case-insensitive (`/incomelevel/lmc/country` → 47 rows). `lendingType=IBD` / `lendingtype=IBD` / `/lendingType/IBD/country` return **each economy twice** (total 134, 67 unique); `/lendingType/IDX/country` likewise (118, 59 unique); `/lendingType/LNX/country` has no duplicates (72). | Current classification only. If the lending-type filter is ever used, dedupe by `id`. |
| `date` on `/country` | any | — | **Ignored.** `country/BRA?date=2018`, `?date=2000:2001` and even the invalid `?date=abcd` are all byte-identical to `country/BRA` (no error 120 here, unlike the indicator endpoint). | There is no way to ask for a past classification. |
| `{lang}` prefix | en, es, fr, ar, zh, bg, de, hi, id, ja, km, ko, mk, mn, pl, pt, ro, ru, sq, th, tr, uk, vi [documented, 898581] | en | `/es/` translates `name`, `region.value`, `incomeLevel.value`, `lendingType.value` **and `capitalCity`** (ZAF: "Pretoria" → "Ciudad del Cabo"). `/xx/` → error 150 with HTTP 200. | Use English (no prefix). Key on ids. |
| `ctrycode` | `y`/`n` | — | Not tested | [documented, 1886674] Adds/removes WB 3-char code in responses. |

---

## Response envelope & field dictionary

JSON success responses from `/country`, `/incomeLevel`, `/region`, `/lendingType` and `/country/{codes}/indicator/{id}` are a **2-element top-level array**: `[ <envelope object>, <array of records> ]`. **Exception:** the metadata endpoints (`/sources/2/country/{iso3}/metadata`, also `/sources/2/series/{ids}/metadata`) return a **single top-level JSON object** `{"page":1,"pages":1,"per_page":"5000","total":26,"source":[…]}`, not an array [observed]. Every JSON error response is a **1-element array**: `[ {"message":[…]} ]`, usually with **HTTP 200** (section D).

### Envelope types differ by endpoint [observed]

| Endpoint | `page` | `pages` | `per_page` | `total` | extra |
|---|---|---|---|---|---|
| `/country…`, `/incomeLevel/{id}/country` | number | number | **string** | number | — |
| `/incomeLevel`, `/region`, `/lendingType` (and their single forms) | **string** | **string** | **string** | **string** | — |
| `/country/{codes}/indicator/{id}` | number | number | number | number | `sourceid` string (`"2"`), `lastupdated` string (`"2026-10-08"`) |
| `/sources/2/country/{iso3}/metadata` | number | number | string (`"5000"`) | number | nested `source[]`; the envelope **is** the top-level object (no outer array) |

Cast every envelope field with `int(...)` in the loader.

### Country record (`/country…`) — 10 keys, identical on all 296 records [observed]

| JSON path | JSON type | Example (BRA) | Nullable/empty? | Suggested column | Notes |
|---|---|---|---|---|---|
| `[1][].id` | string (3 chars) | `"BRA"` | never empty | `country_iso3` (PK) | ISO 3166-1 alpha-3 for economies. WB code where no ISO code exists (`CHI` Channel Islands, `XKX` Kosovo). For aggregates it's the WB aggregate code (`WLD`, `HIC`). Equals indicator `countryiso3code`. |
| `[1][].iso2Code` | string (2 chars) | `"BR"` | never empty | `country_iso2` | **camelCase `iso2Code`** at top level. Aggregates use non-ISO codes (`1W`, `XD`, `4E`, `EU`). Equals indicator `country.id`. |
| `[1][].name` | string | `"Brazil"` | never empty | `country_name` | Can have a **trailing space** (`"Latin America & Caribbean "`, `"Sub-Saharan Africa "`, and DNS/DSF names). Trim in staging. |
| `[1][].region.id` | string | `"LCN"` | `"NA"` for aggregates | `region_code` | Geographic region (7 for economies: EAS, ECS, LCN, MEA, NAC, SAS, SSF). |
| `[1][].region.iso2code` | string | `"ZJ"` | `"NA"` for aggregates | `region_iso2` | **lowercase `iso2code`** inside nested objects. |
| `[1][].region.value` | string | `"Latin America & Caribbean "` | `"Aggregates"` for aggregates | `region_name` | **Trailing space** on LCN and SSF values. This field is the documented way to tell aggregates apart. |
| `[1][].adminregion.id` | string | `"LAC"` | **`""` for all 86 HIC economies and all aggregates** | `admin_region_code` | "Excluding high income" region (LAC, SAS, SSA, EAP, ECA, MNA). Empty string, not null. |
| `[1][].adminregion.iso2code` | string | `"XJ"` | `""` as above | `admin_region_iso2` | |
| `[1][].adminregion.value` | string | `"Latin America & Caribbean (excluding high income)"` | `""` as above | `admin_region_name` | |
| `[1][].incomeLevel.id` | string | `"UMC"` | `"NA"` for aggregates | `income_level_code` | HIC / UMC / LMC / LIC for economies (today: 86/59/47/25; none `INX`). **Current classification only.** |
| `[1][].incomeLevel.iso2code` | string | `"XT"` | `"NA"` for aggregates | `income_level_iso2` | Join key to income-aggregate indicator rows (`country.id` = `XD`/`XT`/`XN`/`XM`). |
| `[1][].incomeLevel.value` | string | `"Upper middle income"` | `"Aggregates"` for aggregates | `income_level_name` | |
| `[1][].lendingType.id` | string | `"IBD"` | **`""` (empty, not `"NA"`) for aggregates** | `lending_type_code` | IBD / IDB / IDX / LNX. Its aggregate sentinel differs from region/incomeLevel. |
| `[1][].lendingType.iso2code` | string | `"XF"` | `""` for aggregates | `lending_type_iso2` | |
| `[1][].lendingType.value` | string | `"IBRD"` | `"Aggregates"` for aggregates | `lending_type_name` | Values: "IBRD", "Blend", "IDA", "Not classified". |
| `[1][].capitalCity` | string | `"Brasilia"` | `""` for aggregates and for CHI, GIB, HKG, ISR, MAC, PSE | `capital_city` | Translated by language prefix. |
| `[1][].longitude` | **string** (not number) | `"-47.9292"` | `""` for aggregates and CHI, CUW, GIB, MAF, PSE, SXM | `longitude` (DOUBLE, `NULLIF(…,'')`) | Never JSON null. Cast in staging. |
| `[1][].latitude` | **string** (not number) | `"-15.7801"` | same as longitude | `latitude` (DOUBLE) | |

No `lastupdated` or version field on country records. Lineage has to come from our own `fetched_at` + request URL.

### Income level record (`/incomeLevel`) [observed]

| JSON path | JSON type | Example | Nullable/empty? | Suggested column | Notes |
|---|---|---|---|---|---|
| `[1][].id` | string | `"HIC"` | no | `income_level_code` | |
| `[1][].iso2code` | string | `"XD"` | no | `income_level_iso2` | |
| `[1][].value` | string | `"High income"` | no | `income_level_name` | |

All 7 rows (sorted by id): `HIC`/XD High income · `INX`/XY Not classified · `LIC`/XM Low income · `LMC`/XN Lower middle income · `LMY`/XO Low & middle income · `MIC`/XP Middle income · `UMC`/XT Upper middle income.
**LMY and MIC are composite groups** (LMY = LIC+LMC+UMC, MIC = LMC+UMC). The 2018 population aggregates add up exactly: LMY 6,311,329,885 = LIC+LMC+UMC, and MIC 5,677,402,468 = LMC+UMC [observed, call #26]. No economy has them as its `incomeLevel`. The 4 "atomic" levels for comparison are **LIC < LMC < UMC < HIC**. Add an explicit `income_rank` 1–4 yourself; the API has no ordering field.

### Region record (`/region`) — different shape [observed]

| JSON path | JSON type | Example | Nullable/empty? | Suggested column | Notes |
|---|---|---|---|---|---|
| `[1][].id` | string | `"3"` (LCN) | **`""` for 37 of 44** | `region_numeric_id` (drop) | Numeric string only for the 7 geographic regions (EAS=1, ECS=2, LCN=3, MEA=4, NAC=6, SAS=8, SSF=9). **Not** the same as `country.region.id`. |
| `[1][].code` | string | `"LCN"` | no | `region_code` | **This** is the join key to `country.region.id`. |
| `[1][].iso2code` | string | `"ZJ"` | no | `region_iso2` | |
| `[1][].name` | string | `"Latin America & Caribbean "` | no | `region_name` | Key is `name` here, `value` in country records. Same trailing spaces. |

### Lending type record (`/lendingType`) [observed]

`id`/`iso2code`/`value`, all strings. 4 rows: `IBD`/XF IBRD · `IDB`/XH Blend · `IDX`/XI IDA · `LNX`/XX Not classified.

### Indicator row for an income aggregate (only the fields relevant to this area) [observed]

| JSON path | JSON type | Example | Nullable/empty? | Suggested column | Notes |
|---|---|---|---|---|---|
| `country.id` | string | `"XD"` | no | `country_iso2` | ISO2-style code. For economies it's ISO2 (`"BR"`), **not** ISO3. |
| `country.value` | string | `"High income"` | no | `country_name` | |
| `countryiso3code` | string | `""` | **`""` for HIC/UMC/LMC/LIC/INX aggregate rows**; `"BRA"` for economies; set for most other aggregates (`"AFE"`, `"ARB"`) | `country_iso3` | Can't be the only join key. Fall back to `country.id` → `incomeLevel.iso2code` / `country.iso2Code`. |
| `date` | string | `"2024"` | no | `year` | String, cast to INT. |
| `value` | number | `1417926222` | (area 02) | `value` | |

---

## Behavior & gotchas

### A. Codes, keys, ordering
- **[observed]** ISO3 and ISO2 are both accepted, case-insensitive, and can be mixed in one request: `country/zaf;US;de;BRA` → `BRA, DEU, USA, ZAF`. `id` holds the ISO3 code and `iso2Code` the ISO2 code. **[documented]** "The API returns ISO 3 and ISO 2 codes wherever present". Where no ISO code exists it returns WB codes (Channel Islands `CHI`/`JG`). [https://datahelpdesk.worldbank.org/knowledgebase/articles/898590-country-api-queries]
- **[observed]** Results are **sorted by `id` ascending** whatever order the request used. **[documented]** The basic-call-structure page says ordering "cannot be controlled" [https://datahelpdesk.worldbank.org/knowledgebase/articles/898581]. Don't rely on order. Key everything by `id`.
- **[observed]** **Key casing is inconsistent**: top-level `iso2Code` (capital C) vs nested `region.iso2code` / `incomeLevel.iso2code` / `lendingType.iso2code` (lowercase c). The region list uses `code` + `name`, while nested objects use `id` + `value`. Use explicit per-endpoint field mappings, not a generic flattener that assumes uniform names.
- **[observed]** Join keys across endpoints: `country.id` (ISO3) = indicator `countryiso3code`, and `country.iso2Code` = indicator `country.id`. For income-group aggregate rows `countryiso3code` is `""`, so `country.id` (XD/XT/XN/XM) has to be mapped through `incomeLevel.iso2code`.
- **[observed]** The lending-type member filter returns **duplicate rows**: `lendingType/IBD/country` (and `country?lendingType=IBD` / `?lendingtype=IBD`) has envelope `total: 134` but only 67 unique ids, each record appearing twice, adjacent and identical. `lendingType/IDX/country` gives 118 rows / 59 unique. `lendingType/LNX/country` (72), `incomeLevel/{id}/country` and `region/{code}/country` showed no duplicates. Sample: `lendingType_IBD_country_duplicates.json`. Our pipeline doesn't use this filter; if anyone does, dedupe by `id` and don't trust `total`.
- **[observed]** Group ids double as "country" codes. `HIC`, `UMC`, `LMC`, `LIC`, `LMY`, `MIC`, `INX`, `IBD`, `IDB`, `IDX` (and region codes such as `LCN`, `SSF`) all appear as aggregate rows in `/country`. Example: `IBD` is both the lending-type id "IBRD" and the aggregate "IBRD only". Don't put group ids and economy ids in one unqualified key space.

### B. Income classification is current-only (CRITICAL for "income groups by year")
- **[observed]** No Indicators-API endpoint returns income classification by year. `/country/{code}` has no date dimension, and `?date=2018` is **ignored** (byte-identical body). `/incomeLevel/{id}/country` returns today's members. `/sources/2/country/BRA/metadata` has `"IncomeGroup":"Upper middle income"` with no year.
- **[documented]** Classification cadence: countries are assigned on **July 1** each year, based on Atlas GNI per capita for the **previous calendar year**, and "Income groupings remain fixed for the entire fiscal year" [https://datahelpdesk.worldbank.org/knowledgebase/articles/378834-how-does-the-world-bank-classify-countries; https://datatopics.worldbank.org/world-development-indicators/the-world-by-income-and-region.html].
- **[documented]** The current classification is for **FY2027**: low income is GNI per capita of $1,175 or less in 2025; LMC $1,176–4,635; UMC $4,636–14,375; HIC above $14,375. Counts are LIC 25 / LMC 47 / UMC 59 / HIC 87 [https://datahelpdesk.worldbank.org/knowledgebase/articles/906519-world-bank-country-and-lending-groups]. So a classification labelled FY*N* is released July 1 of *N*−1 and uses GNI from *N*−2.
- **[observed vs documented]** The API has **86** HIC economies, not 87. The other counts (59/47/25) match. **[inference]** The missing one is very likely *Taiwan, China*: the help page lists it as High income and in East Asia & Pacific [same 906519 page], but no `TWN` record is in the API country list. The page's region counts also differ from the API by exactly one, in EAS only (page EAS [38] vs API 37; ECS 58, LCN 42, MEA 23, NAC 3, SAS 6, SSF 48 and lending IDA 59 / Blend 19 / IBRD 67 all match) [verifier]. Section C supports this: the HIC aggregate exceeds the sum of the 86 API members by exactly 23,574,274 people in 2018, which is about Taiwan's population. **[inference]** The API reflects the FY2027 classification. Venezuela is `LMC` in the API, and the FY27 page lists it in bold as a changed LMC classification.
- **[documented]** Historical classifications are published **only as Excel files** on the Data Catalog (dataset 0037712, all three "Last Updated Oct 9, 2026") [https://datacatalog.worldbank.org/infrastructure-data/search/dataset/0037712/World-Development-Indicators]:
  - `DR0095334` "Historical classification by income" → `OGHIST_2026_10_08.xlsx` (also linked as `https://ddh-openapi.worldbank.org/resources/DR0095334/download` from the help pages). **This is the OGHIST file.**
  - `DR0095347` "Historical classification by income, lending, and fragile status" → `CLASS_hist_2026_10_08.xlsx`
  - `DR0095333` "Current classification by income" → `CLASS_2026_10_08.xlsx`
- **[documented]** The Data Catalog API "provides information about … datasets", meaning metadata, not data contents [https://datahelpdesk.worldbank.org/knowledgebase/articles/1886698-data-catalog-api]. That page gives no base URL or example requests; it points to "provisional documentation" in a GitHub Gist (https://gist.github.com/tgherzog/e6090f9b2ba74f49f75b228f5c7169b9, not reviewed). UNVERIFIED: the resource's "API Service" page lists `resource_unique_id/version/top/skip/filter/select` parameters but no request URL. **Unknown** whether any API returns OGHIST rows as JSON. We did not download the XLSX files (that needs the user's approval).
- **[observed by critic]** The WDI archive source does not give a historical group either. Source 57 ("WDI Database Archives") has a `Version` concept with 142 archived WDI releases (`/sources/57/version/data` → 198904 … 202607), and its Metadata API returns a Country `IncomeGroup`. But `/sources/57/country/VEN/version/202307/metadata?format=json` is **byte-identical** to the same call without `/version/202307/` (both fresh renders), and it returns `"Lower middle income"`, the current value (VEN was unclassified in 2021–2024 per OWID [secondary]). The version segment is silently ignored, so no Indicators-API route to as-of-year income groups was found. OGHIST stays the only candidate source (decision D1/D2).
- **[secondary]** UNVERIFIED: Our World in Data's series (source: World Bank, 1987–2025, last updated July 1, 2026; the page doesn't name OGHIST) shows **all 9 of our countries in the same income group in every year 2017–2025**. The verifier could not confirm the per-country values: the page text doesn't list them, and the CSV wasn't downloaded. The page does say Venezuela (2021–2024) and Ethiopia (2024) were unclassified in some years. Groups claimed: BRA/MEX/ZAF UMC, IND LMC, CAN/DEU/GBR/JPN/USA HIC [https://ourworldindata.org/grapher/world-bank-income-groups]. If OGHIST confirms this, then for our 9 economies "current" and "historical" classification **give the same answer for 2018–2024**. The modelling choice only matters for income-group aggregates and for making the method explicit.

### C. Income-group aggregates in the indicator endpoint
- **[documented]** "You can retrieve data for aggregates by using the appropriate code where you would otherwise specify a country" [https://datahelpdesk.worldbank.org/knowledgebase/articles/898614-aggregate-api-queries].
- **[observed]** `country/HIC;UMC;LMC;LIC/indicator/SP.POP.TOTL?date=2018:2024` → HTTP 200, 28 rows, `country.id` ∈ {XD, XM, XN, XT}, `countryiso3code: ""`, `decimal: 0`. Example: `{"country":{"id":"XD","value":"High income"},"countryiso3code":"","date":"2024","value":1417926222,…}`.
- **[observed]** **Aggregates use CURRENT membership for every year.** For 2018 the World Bank LIC, LMC and UMC population aggregates equal *exactly* the sum of the 2018 populations of the economies currently in those groups (diff = 0). HIC differs by +23,574,274, which points to an economy outside the API list (see B). Sample: `indicator_all_SP.POP.TOTL_2018_membership_check.json` + `income_aggregates_SP.POP.TOTL.json`. **[documented]** The aggregation methodology page doesn't say whether current or historical membership is used. It only says the rules "are intended to yield estimates for a consistent set of economies from one period to the next" [https://datahelpdesk.worldbank.org/knowledgebase/articles/198549-what-methods-are-used-to-calculate-aggregates-for].
- **[documented]** How a WB aggregate treats missing members **depends on the indicator's aggregation method** [same 198549 page]:
  - *Gap-filled total*: missing values are imputed from proxies (GNI, population, etc.). Imputation is usually skipped if more than ⅓ of the benchmark-year total is missing.
  - *Sum*: missing values are **not** imputed. No sum is computed if more than ⅓ of observations (or of a proxy) are missing.
  - *Weighted average*: missing values are assumed to equal the average of the available data. No aggregate is calculated if more than ⅓ of the benchmark-year weights are missing.
  
  **[observed]** For our indicators (`/sources/2/series/…/metadata`, `Aggregationmethod`): SP.POP.TOTL = `Sum`, NY.GDP.MKTP.CD = `Gap-filled total`, NY.GDP.PCAP.CD = `Weighted average`, SP.DYN.LE00.IN = `Weighted average`. So only GDP (current US$) is gap-filled. Either way, a WB group aggregate is **not** the same as a simple average over our 9 countries. Label it as a benchmark, never mix it with our own computed group stats.
- **[observed]** In `country/all/indicator/...`, 49 aggregate rows are mixed in with the 217 economies (266 rows total for 2018). That includes an `INX`/`XY` "Not classified" row with `countryiso3code ""` and `value: null` for 2018. If anyone uses `country/all`, filter aggregates by joining to `/country` and dropping `region.id == "NA"`.

### D. Errors (docs vs live disagree, live drives design)
- **[observed]** Invalid parameter values (unknown country code, `per_page=abc`) return **HTTP 200** with body `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]`. **[documented]** Error 120 is listed as **HTTP 404** [https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes]. → The loader must check the **body shape** (`len(body)==1 and "message" in body[0]`), not just the status code.
- **[observed]** One bad code poisons the whole multi-country request (`BRA;XXX` → error 120 with no BRA data).
- **[observed]** Unsupported language `/v2/xx/…` → **HTTP 200** + error `150` (`"key":"Language is not yet supported in the API"`). **[documented]** 150 = HTTP 400.
- **[observed]** Unknown endpoint `/v2/countryz` → **HTTP 404**, `text/html` IIS-style page **starting with a UTF-8 BOM** (not JSON). **[documented]** Error 140 "Endpoint not found" = HTTP 400 with an error body.
- **[observed]** Error `id` is a **string** (`"120"`), not a number.
- **[observed]** Error bodies are cached by the CDN like normal responses (`cf-cache-status: HIT`, `max-age=86400`). A retry within the cache window can get the same cached error, so a retry won't help for 120/150. That's fine for deterministic errors; just be aware of it if a transient error were ever cached.
- **[documented]** 105 → 503 "Service currently unavailable", 199 → 500 "Unexpected error". Not seen today. Treat 5xx and 429 as retryable.

### E. Formats, language, caching
- **[observed]** Default format is XML (`text/xml`, `<wb:countries page="1" pages="1" per_page="50" total="1" xmlns:wb="http://www.worldbank.org">`). Always send `format=json`. JSON bodies are compact, with no trailing newline.
- **[observed]** `/es/` translates labels **and `capitalCity`** (ZAF "Pretoria" → "Ciudad del Cabo"; lending "IBRD" → "BIRF únicamente"). Ids are unchanged. **[documented]** Default is English, translations aren't available for every country, and metadata notes are English-only [898581, https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api]. → No language prefix (or `/en/`).
- **[observed]** Responses go through Cloudflare. `cache-control: public, max-age=…` and `cf-cache-status` was HIT/MISS/EXPIRED. `max-age` is **not constant**: 86400 on MISSes and most HITs, smaller values (14931–80726) on some JSON HITs, and 14400 on the XML response and on the 404 HTML page. Example: the full list came back as a HIT with `age: 84206` and `last-modified: Mon, 12 Oct 2026 10:57:03 GMT`. A re-run within ~24h can get a **cached** body. **Error envelopes are cached too**: the error-120 body for `BRA;XXX` was a HIT with `age: 738`. `last-modified` appears to be when the origin generated the cached copy (for 3 re-fetched URLs it equals the first agent's MISS time to within a second). It is **not** a data revision date. **[inference]** Re-runs are naturally idempotent for country metadata. Store the response headers (`cf-cache-status`, `age`, `last-modified`) with each raw payload for lineage.
- **[observed]** No auth, no API key, CORS `*`. No rate-limit headers were seen. 35 calls spaced ~1s apart had no throttling, and neither did 41 more verification calls. Latency was 0.07–1.5 s in the first pass, but one verification call (an indicator MISS, `country/HIC;UMC;LMC;LIC/indicator/NY.GDP.PCAP.CD`) took **10.7 s**, and the critic's cold `country/HIC;UMC;LMC;LIC/indicator/NY.GDP.MKTP.CD` took **45.2 s** [observed by critic]. The original "≥30 s" advice is superseded: the README contract uses a **90 s read timeout** (README Contradictions #1).

### F. Data quality quirks to handle in staging
- **[observed]** Trailing whitespace in `region.value` / region `name` for LCN and SSF, and in aggregate names LCN, SSF, DNS, DSF. Keep raw, trim in staging.
- **[observed]** Empty-string sentinels (never JSON `null`) in `adminregion.*` (all HIC), `lendingType.id/iso2code` (aggregates), `capitalCity` / `longitude` / `latitude` (aggregates and a few economies). Use `NULLIF(x,'')` in staging.
- **[observed]** Aggregate sentinel differs by field: `region.id = "NA"`, `incomeLevel.id = "NA"`, but `lendingType.id = ""`. **Use `region.id == "NA"` (or `region.value == "Aggregates"`) as the single aggregate test.** That splits the list into exactly 79 aggregates and 217 economies.
- **[observed]** No economy currently has `incomeLevel.id = "INX"`, but the level exists. Keep the column nullable/`INX`-safe. Historically some economies were unclassified in some years (e.g. Venezuela per the OWID series) [secondary].

---

## Failure modes

| Failure | How it shows up | Pipeline handling |
|---|---|---|
| Unknown/invalid country code (typo in config) | HTTP 200, body `[{"message":[{"id":"120",…}]}]`, **no data for any code in the request** | Detect the error envelope by shape and fail loudly with the URL. Validate configured codes against `/country` first if desired. |
| Invalid param value (`per_page=abc`; bad `date` on the **indicator** endpoint) | HTTP 200 + error 120 | Same as above. On `/country`, `date` is ignored entirely, so `date=abcd` returns normal data with no error. |
| Lending-type member filter (`lendingType/{id}/country`, `?lendingtype=`) | HTTP 200, each economy twice for IBD/IDX, `total` doubled | Not used by our pipeline. If used, dedupe by `id`. |
| Wrong path / endpoint | HTTP 404, HTML body with BOM | `raise_for_status()` catches it. Don't try to JSON-parse it. |
| Unsupported language | HTTP 200 + error 150 | Don't use language prefixes. |
| Service unavailable / unexpected error | Documented 503 (105) / 500 (199). Not seen today | Retry with exponential backoff (e.g. 3–5 attempts), then fail the run. |
| Page past the end | HTTP 200, empty record list, `page > pages` | Loop `page = 1..pages` from the first envelope. Assert `sum(len(records)) == total`. |
| Stale CDN copy | `cf-cache-status: HIT`, `age` up to ~86400 s | Acceptable for this use (classification changes once a year). Record headers. |
| Classification change on July 1 | `incomeLevel` for an economy changes between runs | Snapshot each run's country payload with `fetched_at` (dbt snapshot / SCD2 on `country_iso3`) so history accrues from now on. |
| Response count mismatch | `total` ≠ 9 or the returned id set ≠ configured set | Assert after load. |

---

## Calls our pipeline will make

Expected per full run for this area. All `GET`, no auth, `format=json`.

| # | URL | Requests | Pages | Rows | Purpose |
|---|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=50` | 1 | 1 | 9 | `raw_country` → `dim_country` (current region, income level, lending type) |
| 2 | `https://api.worldbank.org/v2/incomeLevel?format=json` | 1 | 1 | 7 | `raw_income_level` → `dim_income_level` (+ our own `income_rank`; keep LIC/LMC/UMC/HIC as atomic) |
| 3 *(optional)* | `https://api.worldbank.org/v2/region?format=json&per_page=100` | 1 | 1 | 44 | Region labels. Not strictly needed, since names are embedded in #1. |
| 4 *(optional)* | `https://api.worldbank.org/v2/lendingType?format=json` | 1 | 1 | 4 | Lending type labels. Also embedded in #1. |
| 5 *(optional benchmark)* | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/{SP.POP.TOTL\|NY.GDP.MKTP.CD\|NY.GDP.PCAP.CD\|SP.DYN.LE00.IN}?format=json&date=2018:2024&per_page=100` | 4 (one per indicator) | 1 each | 28 each (112 total) | WB-computed income-group aggregates (current membership; aggregation method per indicator: Sum / Gap-filled total / Weighted average). 28 rows, no nulls for 2018–2024, verified for all four indicators (NY.GDP.MKTP.CD added by the critic). Fixtures for all four: `income_aggregates_{SP.POP.TOTL,NY.GDP.MKTP.CD,NY.GDP.PCAP.CD,SP.DYN.LE00.IN}.json` [observed by critic]. Load to `raw_indicator_aggregate`. Kept separate from our 9-country facts. |
| 6 *(decision needed, not REST)* | OGHIST Excel (`DR0095334`) | 0 at runtime | — | 9 countries × 7 years = 63 rows we'd keep | If historical classification is required: convert once to a **dbt seed** (`seed_income_class_history.csv`: `country_iso3, fiscal_year, income_level_code, source_file, source_last_updated`) and commit it, so tests stay offline. |

Minimum: **2 requests** (#1, #2). With all optional calls: **8 requests**. Paging code should still loop `1..pages` generically.

Recommended model for "compare indicators across income groups by year" (to confirm in planning):
- `dim_country` from call #1 holds the **current** income group (WB's own aggregates use this convention too, per section C).
- If OGHIST is adopted, a `bridge_country_income_year (country_iso3, year, income_level_code, classification_basis)` gives the as-of-year group, with a documented FY→data-year rule (see open questions).
- For our 9 countries the two approaches are expected to be identical for 2018–2024 [secondary, needs OGHIST check]. State this in the README.

---

## Evidence log

All calls on 2026-10-14 (UTC) via `curl -sS -m 30`. Every saved sample has a sibling `.meta.json` (url, http_status, content_type, fetched_at_utc, notes). "scratch" means analysed but not kept (too large or redundant).

| # | URL | HTTP status | Observation | Sample file |
|---|---|---|---|---|
| 1 | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=50` | 200 | `{"page":1,"pages":1,"per_page":"50","total":9}`. per_page is a string. lat/long strings. adminregion `""` for HIC | `country_9_selected.json` |
| 2 | `https://api.worldbank.org/v2/country/BRA?format=json` | 200 | 1 row, ISO3 | `country_single_BRA.json` |
| 3 | `https://api.worldbank.org/v2/country/br?format=json` | 200 | Byte-identical to #2 (ISO2, lowercase accepted) | `country_single_br_iso2_lower.json` |
| 4 | `https://api.worldbank.org/v2/country/bra?format=json` | 200 | Byte-identical to #2 | scratch |
| 5 | `https://api.worldbank.org/v2/country/zaf;US;de;BRA?format=json` | 200 | total 4, returned `BRA, DEU, USA, ZAF` (sorted by id, mixed ISO2/3 OK) | `country_mixed_iso2_iso3_reordered.json` |
| 6 | `https://api.worldbank.org/v2/country/XXX?format=json` | 200 | `[{"message":[{"id":"120","key":"Invalid value",…}]}]` | `country_invalid_code_XXX.json` |
| 7 | `https://api.worldbank.org/v2/country/BRA;XXX?format=json` | 200 | Same error 120. BRA not returned | `country_valid_plus_invalid.json` |
| 8 | `https://api.worldbank.org/v2/country/BRA` | 200 | `text/xml`, `<wb:countries page="1" …>` | `country_no_format_xml_default.xml` |
| 9 | `https://api.worldbank.org/v2/country?format=json&per_page=10&page=1` | 200 | `{"page":1,"pages":30,"per_page":"10","total":296}`. First rows ABW, AFE (aggregate), AFG… | `country_all_page1.json` |
| 10 | `https://api.worldbank.org/v2/country?format=json&per_page=500` | 200 | 296 rows (114 KB). 79 with `region.id "NA"`, 217 economies. Sorted by id. CF HIT `age: 84206` | scratch (>100 KB; equivalent to #33–35) |
| 11 | `https://api.worldbank.org/v2/incomeLevel?format=json` | 200 | `{"page":"1","pages":"1","per_page":"50","total":"7"}`. All envelope fields strings | `incomeLevel.json` |
| 12 | `https://api.worldbank.org/v2/incomelevel?format=json` | 200 | Byte-identical to #11 | scratch |
| 13 | `https://api.worldbank.org/v2/region?format=json&per_page=100` | 200 | 44 rows, shape `{"id":"","code":"AFE","iso2code":"ZH","name":…}`, numeric id only on 7 | `region.json` |
| 14 | `https://api.worldbank.org/v2/lendingType?format=json` | 200 | 4 rows IBD/IDB/IDX/LNX | `lendingType.json` |
| 15 | `https://api.worldbank.org/v2/lendingtypes?format=json` | 200 | Byte-identical to #14 (doc spelling works) | scratch |
| 16 | `https://api.worldbank.org/v2/incomeLevel/HIC/country?format=json&per_page=100` | 200 | total 86. Includes CAN, DEU, GBR, JPN, USA. No aggregates | `incomeLevel_HIC_country.json` |
| 17 | `https://api.worldbank.org/v2/country?incomeLevel=HIC&format=json&per_page=100` | 200 | Byte-identical to #16 | scratch |
| 18 | `https://api.worldbank.org/v2/incomeLevel/UMC?format=json` | 200 | `[{"page":"1",…,"total":"1"},[{"id":"UMC","iso2code":"XT","value":"Upper middle income"}]]` | `incomeLevel_UMC_single.json` |
| 19 | `https://api.worldbank.org/v2/region/LCN?format=json` | 200 | `{"id":"3","code":"LCN","iso2code":"ZJ","name":"Latin America & Caribbean "}` | `region_LCN_single.json` |
| 20 | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/SP.POP.TOTL?format=json&date=2018:2024&per_page=100` | 200 | 28 rows. `country.id` XD/XM/XN/XT. `countryiso3code ""`. `lastupdated "2026-10-08"` | `income_aggregates_SP.POP.TOTL.json` |
| 21 | `https://api.worldbank.org/v2/country/BRA?format=json&date=2018` | 200 | Byte-identical to #2: `date` ignored, no historical classification | `country_BRA_with_date_param.json` |
| 22 | `https://api.worldbank.org/v2/es/country/BRA;DEU;ZAF?format=json` | 200 | "Brasil", "Ingreso mediano alto", ZAF capital "Ciudad del Cabo" | `country_9_selected_es.json` (despite the name, holds only **3** countries: BRA, DEU, ZAF) |
| 23 | `https://api.worldbank.org/v2/country/WLD;HIC;EUU?format=json` | 200 | Aggregates: region/incomeLevel `"NA"`, lendingType id `""`, lat/long `""` | `country_aggregates_WLD_HIC_EUU.json` |
| 24 | `https://api.worldbank.org/v2/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024` | 200 | `"country":{"id":"BR",…},"countryiso3code":"BRA"`. Envelope per_page is a number | `indicator_BRA_join_key_demo.json` |
| 25 | `https://api.worldbank.org/v2/sources/2/country/BRA/metadata?format=json` | 200 | `{"id":"IncomeGroup","value":"Upper middle income"}`, no year | `sources2_country_BRA_metadata.json` |
| 26 | `https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?format=json&date=2018&per_page=400` | 200 | 266 rows. Sum of current LIC/LMC/UMC members = WB aggregate exactly. HIC +23,574,274 | `indicator_all_SP.POP.TOTL_2018_membership_check.json` |
| 27 | `https://api.worldbank.org/v2/country?format=json&per_page=10&page=31` | 200 | `[{"page":31,"pages":30,"per_page":"10","total":296},[]]` | `country_page_beyond_end.json` |
| 28 | `https://api.worldbank.org/v2/country?format=json&per_page=abc` | 200 | Error 120 | `country_per_page_invalid.json` |
| 29 | `https://api.worldbank.org/v2/countryz?format=json` | **404** | `text/html`, BOM + IIS "Service" page | `error_unknown_endpoint.html` |
| 30 | `https://api.worldbank.org/v2/xx/country/BRA?format=json` | 200 | `[{"message":[{"id":"150","key":"Language is not yet supported in the API",…}]}]` | `error_unsupported_language.json` |
| 31 | `https://api.worldbank.org/v2/incomelevel/lmc/country?format=json&per_page=100` | 200 | total 47 (path and code case-insensitive). IND included | scratch |
| 32 | `https://api.worldbank.org/v2/country/BRA;bra;BR?format=json` | 200 | total 1 (deduplicated) | scratch |
| 33 | `https://api.worldbank.org/v2/country?format=json&per_page=100&page=1` | 200 | `{"page":1,"pages":3,"per_page":"100","total":296}`, 100 rows | `country_all_per_page_100_page1.json` |
| 34 | `https://api.worldbank.org/v2/country?format=json&per_page=100&page=2` | 200 | 100 rows | `country_all_per_page_100_page2.json` |
| 35 | `https://api.worldbank.org/v2/country?format=json&per_page=100&page=3` | 200 | 96 rows. Pages 1–3 concatenated == #10 exactly | `country_all_per_page_100_page3.json` |

### Our 9 countries as returned today (call #1, 2026-10-14) [observed]

| id (ISO3) | iso2Code | name | region.id / value | adminregion.id | incomeLevel.id / value | lendingType.id / value | capitalCity | lon / lat (strings) |
|---|---|---|---|---|---|---|---|---|
| BRA | BR | Brazil | LCN / "Latin America & Caribbean " | LAC | UMC / Upper middle income | IBD / IBRD | Brasilia | "-47.9292" / "-15.7801" |
| CAN | CA | Canada | NAC / North America | "" | HIC / High income | LNX / Not classified | Ottawa | "-75.6919" / "45.4215" |
| DEU | DE | Germany | ECS / Europe & Central Asia | "" | HIC / High income | LNX / Not classified | Berlin | "13.4115" / "52.5235" |
| GBR | GB | United Kingdom | ECS / Europe & Central Asia | "" | HIC / High income | LNX / Not classified | London | "-0.126236" / "51.5002" |
| IND | IN | India | SAS / South Asia | SAS | LMC / Lower middle income | IBD / IBRD | New Delhi | "77.225" / "28.6353" |
| JPN | JP | Japan | EAS / East Asia & Pacific | "" | HIC / High income | LNX / Not classified | Tokyo | "139.77" / "35.67" |
| MEX | MX | Mexico | LCN / "Latin America & Caribbean " | LAC | UMC / Upper middle income | IBD / IBRD | Mexico City | "-99.1276" / "19.427" |
| USA | US | United States | NAC / North America | "" | HIC / High income | LNX / Not classified | Washington D.C. | "-77.032" / "38.8895" |
| ZAF | ZA | South Africa | SSF / "Sub-Saharan Africa " | SSA | UMC / Upper middle income | IBD / IBRD | Pretoria | "28.1871" / "-25.746" |

Income-group coverage of our 9: **HIC 5, UMC 3, LMC 1, LIC 0**. No low-income country is in scope, so any "by income group" comparison has only 3 groups, and LMC has a single member (India).

### Documentation consulted
- About the Indicators API: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation
- Basic call structures: https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
- Country API queries: https://datahelpdesk.worldbank.org/knowledgebase/articles/898590-country-api-queries
- Aggregate API queries: https://datahelpdesk.worldbank.org/knowledgebase/articles/898614-aggregate-api-queries
- New features in V2: https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api
- API error codes: https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes
- Data Catalog API: https://datahelpdesk.worldbank.org/knowledgebase/articles/1886698-data-catalog-api
- Country and lending groups (FY2027): https://datahelpdesk.worldbank.org/knowledgebase/articles/906519-world-bank-country-and-lending-groups
- How the WB classifies countries: https://datahelpdesk.worldbank.org/knowledgebase/articles/378834-how-does-the-world-bank-classify-countries
- Aggregation methods: https://datahelpdesk.worldbank.org/knowledgebase/articles/198549-what-methods-are-used-to-calculate-aggregates-for
- WDI "World by income and region": https://datatopics.worldbank.org/world-development-indicators/the-world-by-income-and-region.html
- Data Catalog WDI dataset (OGHIST/CLASS resources): https://datacatalog.worldbank.org/infrastructure-data/search/dataset/0037712/World-Development-Indicators
- Secondary: Our World in Data, World Bank income groups: https://ourworldindata.org/grapher/world-bank-income-groups

---

## Open questions

1. **Modelling decision: current vs historical income group for "compare across income groups by year".** The API has only the current (FY2027) classification. Options: (a) current group for all years. Simple, fully API-sourced, same convention WB uses for its own aggregates. (b) As-of-year group from OGHIST, which is not in the REST API and would be a committed dbt seed. (c) Both, with a `classification_basis` column. For our 9 countries a secondary source says the result is the same for 2018–2024. Needs a decision from Alan.
2. **If OGHIST is used: may we download it?** File: `OGHIST_2026_10_08.xlsx`, ~113 KB, from `datacatalogfiles.worldbank.org` / `ddh-openapi.worldbank.org/resources/DR0095334/download`. We did not download it here; it needs the user's approval. Its sheet layout and the exact column/FY labelling are **unknown** until opened.
3. **FY → data-year mapping rule (only if OGHIST is used).** FY*N* is released July 1 of *N*−1 and based on GNI of *N*−2. For data year *Y*, do we attach the group *in effect during Y* (FY*Y* for H1 / FY*Y+1* for H2), or the group *computed from Y's GNI* (FY*Y+2*)? It needs a documented choice.
4. **Does any API return OGHIST rows as JSON?** The Data Catalog "API Service" page for DR0095334 lists `resource_unique_id`, `version`, `top`, `skip`, `filter`, `select` but no request URL. The help article says the catalog API returns metadata and points to provisional docs in a GitHub Gist (https://gist.github.com/tgherzog/e6090f9b2ba74f49f75b228f5c7169b9). Unknown, not tested. *Critic update:* the Indicators API's own archive source (57, "WDI Database Archives", 142 versions) does **not** provide it either: the Metadata API ignores a `/version/{v}` segment and returns the current `IncomeGroup` (see "Critic additions"). The Data Catalog API itself is still untested.
5. **The 87th HIC economy** missing from the API (very likely Taiwan, China) is included in WB's HIC aggregate. That only matters if we compare our numbers with the WB `HIC` aggregate benchmark. Confirm the identity from CLASS.xlsx if the benchmark is used.
6. **Include the WB income-aggregate benchmark (call #5)?** It's useful context, but those values are computed on current membership, and missing members are handled by each indicator's aggregation method (GDP current US$ is gap-filled; GDP per capita and life expectancy assume missing members equal the average of the rest; population is a plain sum). They are not comparable to stats over our 9 countries. Decide whether it goes in the deliverable.
7. **Maximum `per_page`** for `/country` is unknown (500 and 1000 worked). Not needed for our 9 codes. *Critic note:* the observation endpoint caps at 32767 (areas 04/05); `/country` was never probed above 1000.
8. **Retry behavior under real 5xx/503 (error 105)** wasn't observed today. Assume the documented statuses and retry with backoff.
9. **Snapshotting:** should `dim_country` be an SCD2 snapshot (dbt snapshot on `country_iso3`, tracking `income_level_code`), so that classification changes after each July 1 are captured from now on? Recommended, but it adds scope.
10. **Why does the lending-type member filter duplicate rows** (IBD, IDX) but not LNX? It doesn't affect our pipeline, which reads lending type from the country record. Noted so nobody builds on `lendingType/{id}/country` without deduping.
11. **OWID per-country values** for 2017–2025 were not verified (CSV not downloaded). Confirm from OGHIST (Q2) before stating "current = historical for our 9" in the README.

---

## Verification

Adversarial re-check run on **2026-10-14 (UTC)**, 01:24–01:31 (first and last response `date` headers in the verifier's scratch log). 41 fresh live calls (`curl -sS -m 30`, ~1 s apart, no credentials), official help pages read again, and every saved sample re-parsed offline. Raw verification responses were kept in scratch, not in the project. One new sample was added: `lendingType_IBD_country_duplicates.json` (+ `.meta.json`). "Byte-identical" means `cmp` of the fresh body against the saved sample.

| Claim | Result | URL or doc used |
|---|---|---|
| All 28 sample files named in this doc exist, parse (JSON where expected; `.xml`/`.html` start with `<?xml` / BOM+`<?xml`), and each has a `.meta.json` with url/http_status/content_type/fetched_at_utc/notes | confirmed | local files in `samples/countries/` |
| 9-country call: 1 page, 9 rows, `page`/`pages`/`total` numbers, `per_page` string `"50"` | confirmed (fresh body byte-identical to `country_9_selected.json`) | `https://api.worldbank.org/v2/country/BRA;CAN;DEU;GBR;IND;JPN;MEX;USA;ZAF?format=json&per_page=50` |
| "Our 9 countries as returned today" table (ids, iso2, region, adminregion, income, lending, capital, lon/lat strings) | confirmed | same call + `country_9_selected.json` |
| Country record: 10 keys on all 296 records; all 18 leaf values are JSON strings; no JSON `null` anywhere; lon/lat are strings | confirmed | `country_all_per_page_100_page{1,2,3}.json` (offline) |
| 296 records = 217 economies + 79 aggregates; `region.id=="NA"` ⇔ `region.value=="Aggregates"`; aggregates have `incomeLevel.id "NA"`, `lendingType.id ""`, adminregion/capital/lon/lat `""` | confirmed | same samples; `https://api.worldbank.org/v2/country?format=json&per_page=1000` (total 296, 1 page) |
| Income counts HIC 86 / UMC 59 / LMC 47 / LIC 25, no economy `INX` | confirmed | samples; `…/incomeLevel/HIC/country?…` (86), `…/incomelevel/lmc/country?…` (47), `…/incomeLevel/UMC/country?format=json&per_page=200` (59) |
| `adminregion.*` is `""` for all 86 HIC economies; ids LAC/SAS/SSA/EAP/ECA/MNA otherwise | confirmed | samples (offline) |
| `capitalCity ""` for CHI, GIB, HKG, ISR, MAC, PSE; lon/lat `""` for CHI, CUW, GIB, MAF, PSE, SXM | confirmed | samples (offline) |
| Trailing spaces: region value/name for LCN and SSF; aggregate names LCN, SSF, DNS, DSF | confirmed | samples (offline) |
| ISO2/ISO3 mixed + case-insensitive; result sorted by `id`; duplicates removed | confirmed | `…/country/zaf;US;de;BRA?format=json` (byte-identical to sample), `…/country/BRA;bra;BR?format=json` (total 1) |
| `date` ignored on `/country` | confirmed and extended: `date=2018`, `date=2000:2001` and invalid `date=abcd` all byte-identical to plain `country/BRA` | `…/country/BRA?format=json&date=2018`, `…&date=2000:2001`, `…&date=abcd` |
| Failure-modes row "bad date → HTTP 200 + error 120" | corrected: true only on the indicator endpoint; on `/country` a bad date is ignored | `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=abcd` (error 120) vs `…/country/BRA?format=json&date=abcd` (data) |
| Unknown code / `per_page=abc` → HTTP 200 + error `"120"` (string id); one bad code fails the whole request | confirmed | `…/country/BRA;XXX?format=json`, `…/country?format=json&per_page=abc` |
| `/xx/` language → HTTP 200 + error `"150"` | confirmed | `…/v2/xx/country/BRA?format=json` |
| Unknown endpoint → HTTP 404, `text/html`, body starts with UTF-8 BOM (`EF BB BF`) | confirmed | `…/v2/countryz?format=json` |
| Page past the end → HTTP 200, `[{"page":31,"pages":30,…},[]]` | confirmed | `…/country?format=json&per_page=10&page=31` |
| Documented statuses 120→404, 150→400, 140→400, 105→503, 199→500 | confirmed (docs and live disagree, as stated) | https://datahelpdesk.worldbank.org/knowledgebase/articles/898620-api-error-codes |
| Reference envelopes (`/incomeLevel`, `/region`, `/lendingType`) have all-string page/pages/per_page/total | confirmed (bodies byte-identical to samples) | `…/incomeLevel?format=json`, `…/region?format=json&per_page=100`, `…/lendingType?format=json` |
| `incomelevel` (lowercase) and `lendingtypes` (plural) byte-identical to camelCase forms; docs use `lendingtypes` | confirmed | `…/incomelevel?format=json`, `…/lendingtypes?format=json`; https://datahelpdesk.worldbank.org/knowledgebase/articles/898614-aggregate-api-queries |
| `/region`: 44 rows, shape `id/code/iso2code/name`, numeric-string `id` only for EAS 1, ECS 2, LCN 3, MEA 4, NAC 6, SAS 8, SSF 9 | confirmed (also 44 with default per_page) | `…/region?format=json`, `…/region?format=json&per_page=100` |
| `country?incomeLevel=HIC` byte-identical to `/incomeLevel/HIC/country` | confirmed; also `country?region=LCN` == `/region/LCN/country` | `…/country?incomeLevel=HIC&format=json&per_page=100`, `…/region/LCN/country?format=json&per_page=100` |
| "Also works for `lendingType/{id}/country` [documented: argument- and URL-style are equivalent]" | corrected: the docs state equivalence only with the `incomeLevel=LIC` example; the lending-type filter (both styles) returns each economy **twice** for IBD (134/67) and IDX (118/59), not for LNX (72) | https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures; `…/lendingType/IBD/country?format=json&per_page=200`, `…/country?lendingtype=IBD&…`, `…/lendingType/IDX/country?…`, `…/lendingType/LNX/country?…` |
| "Every JSON success response is a 2-element top-level array" | corrected: `/sources/2/country/{iso3}/metadata` (and `/sources/2/series/{ids}/metadata`) return a top-level JSON **object** | `…/sources/2/country/BRA/metadata?format=json` (byte-identical to sample), `…/sources/2/series/SP.POP.TOTL;NY.GDP.MKTP.CD;NY.GDP.PCAP.CD;SP.DYN.LE00.IN/metadata?format=json` |
| WDI country metadata `IncomeGroup` has no year | confirmed | `…/sources/2/country/BRA/metadata?format=json` |
| Join keys: indicator `country.id` = ISO2 (`BR`), `countryiso3code` = ISO3 (`BRA`) | confirmed (byte-identical) | `…/country/BRA/indicator/SP.POP.TOTL?format=json&date=2024` |
| Income aggregates: 28 rows, `country.id` XD/XM/XN/XT, `countryiso3code ""` | confirmed for SP.POP.TOTL (byte-identical) and also for SP.DYN.LE00.IN and NY.GDP.PCAP.CD (28 rows each, no nulls); NY.GDP.MKTP.CD not re-checked (critic: checked in C1, 28 rows, no nulls) | `…/country/HIC;UMC;LMC;LIC/indicator/{SP.POP.TOTL,SP.DYN.LE00.IN,NY.GDP.PCAP.CD}?format=json&date=2018:2024&per_page=100` |
| Current membership: 2018 LIC/LMC/UMC aggregates = exact sum of current members; HIC +23,574,274; LMY = LIC+LMC+UMC; MIC = LMC+UMC | confirmed (recomputed offline: diffs 0/0/0, HIC 23,574,274; LMY 6,311,329,885; MIC 5,677,402,468) | `indicator_all_SP.POP.TOTL_2018_membership_check.json` + `country_all_per_page_100_page*.json` |
| `country/all/indicator`: 266 rows = 217 economies + 49 aggregates; INX/XY row `value: null`; `countryiso3code ""` only for XD/XM/XN/XT/XY | confirmed | same sample (offline) |
| Aggregates "gap-filled/imputed … imputation is skipped if more than ⅓ is missing" | corrected: depends on the aggregation method. Gap-filled total imputes; Sum does not impute; Weighted average assumes missing = average of available. Ours: POP = Sum, GDP = Gap-filled total, GDP pc and LE = Weighted average | https://datahelpdesk.worldbank.org/knowledgebase/articles/198549-what-methods-are-used-to-calculate-aggregates-for; `…/sources/2/series/…/metadata` |
| FY2027 classification: LIC ≤ $1,175 (2025 GNI), LMC 1,176–4,635, UMC 4,636–14,375, HIC > 14,375; counts 25/47/59/87; Venezuela bold in LMC; Taiwan, China in HIC | confirmed | https://datahelpdesk.worldbank.org/knowledgebase/articles/906519-world-bank-country-and-lending-groups |
| 87th HIC economy is Taiwan, China | still [inference], strengthened: no `TWN` in API; page region counts match API except EAS 38 vs 37 | same page + samples |
| Classified July 1 on previous calendar year's Atlas GNI; groupings fixed for the fiscal year | confirmed | https://datahelpdesk.worldbank.org/knowledgebase/articles/378834-how-does-the-world-bank-classify-countries; https://datatopics.worldbank.org/world-development-indicators/the-world-by-income-and-region.html |
| Data Catalog dataset 0037712: DR0095334 OGHIST_2026_10_08.xlsx, DR0095347 CLASS_hist_…, DR0095333 CLASS_…, all "Last Updated Oct 9, 2026"; help page links `ddh-openapi…/DR0095334/download` | confirmed | https://datacatalog.worldbank.org/infrastructure-data/search/dataset/0037712/World-Development-Indicators; 906519 |
| Data Catalog API returns dataset information (not data) | confirmed (quote); the page gives no base URL and points to a GitHub Gist | https://datahelpdesk.worldbank.org/knowledgebase/articles/1886698-data-catalog-api |
| "API Service" page lists `resource_unique_id/version/top/skip/filter/select` | unverified (page not checked) | — |
| OWID: all 9 countries same group 2017–2025 | unverified: the OWID page text has no per-country values; CSV not downloaded | https://ourworldindata.org/grapher/world-bank-income-groups |
| Docs: 1,500 chars between slashes / 4,000 per URL; language list; default XML; order "cannot be controlled"; default per_page 50 | confirmed | 898581 |
| Docs: "The API returns ISO 3 and ISO 2 codes wherever present" (else WB codes) | confirmed | https://datahelpdesk.worldbank.org/knowledgebase/articles/898590-country-api-queries |
| `ctrycode=y/n` and `jsonstat` documented | confirmed (`jsonstat` is BETA, indicator queries only) | https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api |
| Default format XML (`text/xml`, `<wb:countries …>`) | confirmed (byte-identical to sample) | `…/country/BRA` |
| `/es/` translates names, labels and `capitalCity` (ZAF "Ciudad del Cabo"; "BIRF únicamente") | confirmed (byte-identical) | `…/v2/es/country/BRA;DEU;ZAF?format=json` |
| `cache-control: public, max-age=86400` | corrected: max-age varies (86400 on MISS/most HITs; 14931–80726 on some HITs; 14400 on XML and on the 404 page). New: error bodies are cached too | response headers of the 41 verification calls |
| `last-modified` is the CDN copy time, not a data revision date | confirmed, refined: it equals the first agent's MISS fetch time for 3 URLs (origin generation time of the cached copy) | headers of `…/country/BRA;CAN;…`, `…/country/BRA?…&date=2018`, `…/country/BRA;XXX?…` |
| Latency 0.07–1.5 s | corrected: one indicator MISS took 10.7 s; others 0.07–0.80 s | `…/country/HIC;UMC;LMC;LIC/indicator/NY.GDP.PCAP.CD?…` |
| No auth, CORS `*`, no rate-limit headers, no throttling | confirmed (41 more calls, no 429) | response headers |
| `per_page` upper limit unknown | confirmed unknown; 1000 also works | `…/country?format=json&per_page=1000` |
| `country/{codes}` accepts `all` | confirmed (total 296) | `…/country/all?format=json&per_page=5` |
| Real 5xx/503 behavior | unverified (can't be provoked) | — |

Sample-naming note: `country_9_selected_es.json` holds **3** countries (BRA, DEU, ZAF), as its `.meta.json` URL says. The file was not renamed, so existing references still work.

---

## Critic additions

Completeness-critic pass, 2026-10-14 02:00–02:03 UTC: 8 live calls in this area with `curl -sS -m 90`, about 1 s apart, no credentials. Every sample below has a `.meta.json` and a `.headers.txt` (cookie values redacted). Findings are tagged **[observed by critic]** in the sections above.

| # | URL | HTTP status | observation | sample file |
|---|---|---|---|---|
| C1 | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/NY.GDP.MKTP.CD?format=json&date=2018:2024&per_page=100` | 200 | 28 rows, no nulls, `countryiso3code ""`, 26 float + 2 int tokens (XD 2018, XT 2020). Cold MISS **45.2 s**, the slowest response seen in the whole mapping | `income_aggregates_NY.GDP.MKTP.CD.json` |
| C2 | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/NY.GDP.PCAP.CD?format=json&date=2018:2024&per_page=100` | 200 | 28 rows, no nulls, all floats, `decimal 1`. cf HIT | `income_aggregates_NY.GDP.PCAP.CD.json` |
| C3 | `https://api.worldbank.org/v2/country/HIC;UMC;LMC;LIC/indicator/SP.DYN.LE00.IN?format=json&date=2018:2024&per_page=100` | 200 | 28 rows, no nulls, all floats. cf HIT | `income_aggregates_SP.DYN.LE00.IN.json` |
| C4 | `https://api.worldbank.org/v2/sources/57/concepts?format=json` | 200 | Concepts Country, Series, Time, **Version** | `critic__source_57_wdi_archives_concepts.json` |
| C5 | `https://api.worldbank.org/v2/sources/57/country/BRA/metadata?format=json` | 200 | Object envelope; `IncomeGroup` "Upper middle income", no version/year attribute | `critic__source_57_country_BRA_metadata.json` |
| C6 | `https://api.worldbank.org/v2/sources/57/version/data?format=json&per_page=1000` | 200 | 142 versions, `198904` … `202607` | `critic__source_57_version_list.json` |
| C7 | `https://api.worldbank.org/v2/sources/57/country/VEN/version/202307/metadata?format=json` | 200 | `IncomeGroup` "Lower middle income" (current value) | `critic__source_57_country_VEN_version_202307_metadata.json` |
| C8 | `https://api.worldbank.org/v2/sources/57/country/VEN/metadata?format=json` | 200 | Byte-identical to C7 (sha256 `d4dbdee7…`): `/version/` is ignored | `critic__source_57_country_VEN_metadata_no_version.json` |

Cross-file fixes made by the critic in this doc: the verification call count (39 → 41) and window (01:24–01:31) now match the verifier's scratch log; the `per_page` ceiling and timeout advice now point to areas 04/05 and the README; call #5 now covers all four aggregate fixtures.
