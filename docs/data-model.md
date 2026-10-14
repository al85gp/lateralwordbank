# Data model

[← Back to the README](../README.md)

The grain, business keys and relationships of every model, and how the source is standardized.


The fact table's grain is the full requested spine, country × indicator × year, so a missing value is a row with a status rather than an absent row. With the default config that is 9 × 4 × 7 = 252 rows.

| Model | Layer | Materialized | Grain | Business key | Relationships |
| --- | --- | --- | --- | --- | --- |
| `stg_wb__selected_load` | staging | view | One row: the load to build from | `load_id` | The `load_id` var, else the latest completed load; every staging model joins to it |
| `stg_wb__scope` | staging | view | Requested country × indicator × year | (`country_iso3`, `indicator_code`, `year`) | Built from `raw.run_scope` of the selected load; carries each indicator's `unit` and `aggregation` |
| `stg_wb__countries` | staging | view | Country in the selected load | `country_iso3` | Parsed from the country responses |
| `stg_wb__observations` | staging | view | API record | (`country_iso3`, `indicator_code`, `year`) | Parsed from indicator responses with `record_index` |
| `dim_country` | marts | table | Country | `country_iso3` | One to many with the fact |
| `dim_indicator` | marts | table | Indicator | `indicator_code` | One to many with the fact |
| `fct_indicator_year` | marts | table | Country × indicator × year in scope | (`country_iso3`, `indicator_code`, `year`) | Many to one with both dims |

`fct_indicator_year` columns:

- `country_iso3`, `indicator_code`, `year` — the business key.
- `value` — DOUBLE, nullable. Never coerced to 0.
- `value_status` — `reported`, `null_in_source` (the API returned the row with a null value) or `not_returned` (requested, but no row came back).
- `obs_status`, `decimals` — passed through as the API sends them.
- `source_last_updated` — the API's `lastupdated` for that indicator's source.
- `load_id`, `request_url`, `body_sha256`, `record_index` — lineage to the exact raw record; null only when `not_returned`.

`dim_country`: `country_iso3`, `iso2_code`, `country_name`, `region_name`, `income_group_code`, `income_group_name`.
`dim_indicator`: `indicator_code`, `indicator_name`, `unit`, `aggregation`, `source_id`.

**Standardization:** one country key (ISO3) everywhere, because the API mixes ISO2 `country.id` and `countryiso3code`; `year` as an integer instead of the string `date`; every value as DOUBLE; nested `{id, value}` objects flattened; names trimmed; snake_case throughout.

The column lists above were checked against the real payload. The model drops these API fields; all of them stay in raw:

- Country call: `adminregion`, `lendingType`, `capitalCity`, `longitude`, `latitude`, and the region's and income level's `iso2code` aliases.
- Observations: `country.id` and `country.value`, which the ISO2 code and `dim_country` already cover, and `unit`, which is empty for all four indicators (the unit comes from config).
