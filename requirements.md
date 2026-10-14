# Requirements — Lateral Senior Data Engineer Take-Home

Source: *Senior Data Engineer – Take Home Assessment.pdf* (2 pages). Every requirement gets an ID so the plan, ADRs, tests and README can trace back to it.

## Scenario

A research team needs a dependable, traceable **yearly** dataset of country indicators from the **World Bank API** that analysts can query without knowing the API or working around missing data.

## Fixed scope

| Dimension | Values |
|---|---|
| Source | World Bank public API only (no account, no key) — production data source |
| Countries | BRA, CAN, DEU, GBR, IND, JPN, MEX, USA, ZAF |
| Indicators | `SP.POP.TOTL` Population, total · `NY.GDP.MKTP.CD` GDP (current US$) · `NY.GDP.PCAP.CD` GDP per capita (current US$) · `SP.DYN.LE00.IN` Life expectancy at birth, total (years) |
| Years | 2018 – 2024 inclusive |
| Missing data | Some values may be unavailable. **Missing is a meaningful outcome, never zero.** |

Task: a **local, configuration-driven** pipeline that retrieves the data, prepares it for analytics, and produces a small set of reporting outputs.

## Explicit requirements

### 1. Data ingestion
| ID | Requirement |
|---|---|
| R-ING-01 | Retrieve **country metadata** from the API |
| R-ING-02 | Retrieve the required **indicator observations** from the API |
| R-ING-03 | Handle **paging** |
| R-ING-04 | Handle **API failures** |
| R-ING-05 | Every output value is **traceable to the API response it came from** |
| R-ING-06 | **One documented command** runs the pipeline locally from a clean checkout |
| R-ING-07 | Re-running with unchanged source data must **not create duplicate analytical records** |
| R-ING-08 | No cloud resources, paid accounts, or API accounts |

### 2. Data model and transformations
| ID | Requirement |
|---|---|
| R-MOD-01 | Keep **raw** source representation separate from **cleaned** and **analytics-ready** data |
| R-MOD-02 | Design and document the **grain, business keys, and relationships** of the final model |
| R-MOD-03 | **Standardize** so analysts don't need to understand the source format |
| R-MOD-04 | Add a **country or indicator via configuration** or documented parameters, without copying pipeline code |

### 3. Analytics outputs (SQL queries, views, tables, or equivalent reproducible reporting layer)
| ID | Question to answer |
|---|---|
| R-ANL-01 | Which country-indicator-year combinations are **missing or incomplete**? |
| R-ANL-02 | **Year-over-year change** for each available indicator, handling **missing or zero prior values** |
| R-ANL-03 | How do the countries compare for the **latest available year of each indicator**? |
| R-ANL-04 | How do the indicators compare **across income groups, by year**? |
| R-ANL-05 | *Optional:* an extra view, dashboard, or Power BI report |

### 4. Data quality and testing
| ID | Requirement |
|---|---|
| R-DQ-01 | Automated DQ checks and tests that give confidence in **correctness** and **safe reprocessing** |
| R-DQ-02 | Check that **related indicators are consistent** with each other |
| R-DQ-03 | Tests run **without calling the live API** |

### 5. Production design note (README section, ~1 page, bullets OK)
| ID | Topic |
|---|---|
| R-PRD-01 | Scheduling and incremental refreshes |
| R-PRD-02 | Late, corrected, or changed source data |
| R-PRD-03 | Monitoring, alerting, and data-quality failures |
| R-PRD-04 | Backfills and replay-ability |
| R-PRD-05 | Mapping the local design to **Snowflake + AWS Glue or EMR**, or comparable Azure services |

### 6. Deliverable
| ID | Requirement |
|---|---|
| R-DEL-01 | Any language/stack; **runs locally by following the README** (Docker is fine) |
| R-DEL-02 | README: setup, **assumptions**, **trade-offs**, short **walkthrough of key decisions**, **AI usage** |
| R-DEL-03 | **Sample output** of the reporting queries (e.g. CSV) |
| R-DEL-04 | **Git/GitHub repository with commit history** containing all of the above |

## Constraints and grading signals

> **Superseded in part (2026-10-13).** The recruiter's note says there is no time frame and asks for a polished, production-grade solution: *"a complete, thoughtful solution over a quick one."* The 4–6 h limit and the priority-order cut list below no longer constrain the plan. The grading focus still applies.


- Suggested time: **4–6 hours**. Stop at the limit; document unfinished work and next steps in the README.
- If time runs short, priority order:
  1. Correct, idempotent ingestion and a clearly defined data model
  2. Data-quality checks and tests
  3. The required analytics outputs
  4. README, production design note, walkthrough
  5. Anything optional
- Grading focus: **correctness, structure, clarity**. *"Correct, idempotent ingestion and a clear data model matter the most."*
- AI tools are encouraged (and their use must be documented, R-DEL-02).

## Derived requirements (our interpretation, to confirm)

These are not stated verbatim but fall out of the explicit ones. Each needs a documented assumption in the README.

| ID | Derived requirement | From |
|---|---|---|
| D-01 | Distinguish three states per country-indicator-year: **value present**, **API returned the row with a null value**, **API returned no row at all**. **Confirmed:** the API produces both forms; for example, life expectancy 2025 comes back as null rows, and years outside the source's time axis are omitted ([api_calls/02](api_calls/02_indicator_observations.md)) | R-ANL-01, "missing is meaningful" |
| D-02 | Build an expected-combination **spine** (countries × indicators × years from config) so absent rows become visible | R-ANL-01, R-MOD-04 |
| D-03 | YoY: prior missing → YoY null with a reason flag; prior zero → percent change undefined (null), absolute change still computed | R-ANL-02 |
| D-04 | "Latest available year" is per **country × indicator** (the latest non-null value), not a single global max year | R-ANL-03 |
| D-05 | Income group comes from country metadata. **Confirmed:** the v2 API exposes only the **current** classification (`?date=` is ignored), so "by year" means current group × observation year, documented as an assumption ([api_calls/01](api_calls/01_countries_and_classifications.md)) | R-ANL-04, R-ING-01 |
| D-06 | Consistency check: `NY.GDP.PCAP.CD ≈ NY.GDP.MKTP.CD / SP.POP.TOTL` within a tolerance; plus range checks (population > 0, life expectancy within plausible bounds) | R-DQ-02 |
| D-07 | Lineage per row: load/run id, request URL, page, fetched-at, response hash, source `lastupdated`; raw responses kept immutable | R-ING-05 |
| D-08 | Idempotency at the analytical layer: unique on (`country_iso3`, `indicator_code`, `year`); the raw layer may append per run but must not inflate analytics | R-ING-07, R-DQ-01 |
| D-09 | All scope (countries, indicators, years) lives in **one config file**; CLI flags can override for ad-hoc runs | R-MOD-04, R-ING-06 |
| D-10 | Saved real API responses become **test fixtures** (no live calls in tests) | R-DQ-03 |
