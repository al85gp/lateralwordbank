# Reporting outputs

[← Back to the README](../README.md)

The four reports, their logic, how the CSVs are checked against their source tables, and what the committed CSVs show.


| Question | View | Grain | Logic |
| --- | --- | --- | --- |
| Which combinations are missing or incomplete? | `rpt_missing_or_incomplete` | Country × indicator × year not `reported` | Includes `value_status` plus the series' `years_reported` and `years_expected` |
| Year-over-year change | `rpt_yoy_change` | Country × indicator × year | Prior is exactly `year - 1`. When the current or prior value is missing, `abs_change` and `pct_change` are both null. When the prior value is 0, `abs_change` is still computed and only `pct_change` is null (D-03). `change_status` says which case applies: `ok`, `first_year_in_scope`, `no_current_value`, `no_prior_value`, `prior_is_zero` |
| Countries at the latest year | `rpt_latest_comparison` | Indicator × country | Per D-04, `latest_year` is each country's own latest year with a reported (non-null) value, so it can differ between countries. Shows that year's `value` and its `value_rank` among countries for the indicator (1 = highest; ties share a rank). `years_behind` is the most recent `latest_year` of any country minus this country's, so a comparison across different years is visible. A country with no reported value in scope has null `latest_year`, `value` and `value_rank` |
| Income groups by year | `rpt_income_group_by_year` | Income group × indicator × year | `sum` for population and GDP; `population_weighted_mean` for GDP per capita and life expectancy, weighted by the same country-year's population; `countries_in_group`, `countries_reporting` and `is_complete` show coverage |

`pct_change` is in percent of the prior value. CSVs are ordered by their keys. The three row-level reports carry `request_url`, `body_sha256` and `record_index` as lineage; the income-group report is an aggregate and has none. None of them carry `load_id` or `fetched_url`, so a rerun on unchanged data produces a zero-line git diff.

- 2018 has no prior year in scope, so its YoY status is `first_year_in_scope`. Set `years.start: 2017` to change that.
- India is the only lower-middle-income country in scope, so that group is a single country.

**Checking the CSVs against their source tables.** The dbt tests check the models inside the warehouse. `python -m wb_pipeline reconcile` (in Docker: `docker compose run --rm pipeline reconcile`) checks the files people actually read, from the outside:

| Check | What it compares |
| --- | --- |
| Each CSV equals its reporting view | Every row and value, both directions (`EXCEPT ALL`), with the CSV read using the view's column types |
| Row counts follow from scope | Facts and `rpt_yoy_change` = countries × indicators × years from `raw.run_scope`; `rpt_latest_comparison` = countries × indicators; `rpt_income_group_by_year` = groups × indicators × years; `rpt_missing_or_incomplete` = facts not reported |
| CSV values = fact table | Every `rpt_yoy_change` value, `request_url` and `record_index` against `fct_indicator_year`; every `rpt_latest_comparison` value against the fact at that country's latest reported year |
| Fact table = raw API response | Every reported value against the record at `record_index` in its stored body, whose SHA-256 must match |
| Income groups = country totals | For `sum` indicators, the groups add up to the reporting countries, per indicator and year |

On the committed output all 13 checks pass. `test_reconcile_*` runs the command after a pipeline run, and also edits one CSV value by hand to show that exactly the two checks that should fail do fail.

What the committed CSVs show (live run, source updated 2026-10-08):

- **`rpt_missing_or_incomplete`:** a header only. All 252 requested values were reported.
- **`rpt_yoy_change`:** 216 `ok` rows and 36 `first_year_in_scope` rows (2018). The largest falls are Brazil's GDP and GDP per capita in 2020 (about −21%). The largest rise is South Africa's GDP in 2021 (+24%).
- **`rpt_latest_comparison`:** every country's latest year is 2024, so `years_behind` is 0 everywhere. India ranks first for population, the USA for GDP and GDP per capita, and Japan for life expectancy.
- **`rpt_income_group_by_year`:** 84 rows (4 indicators × 3 groups × 7 years), all complete. In 2024, GDP per capita, weighted by population, was about $67,000 for high income, $10,900 for upper middle income and $2,600 for lower middle income.
