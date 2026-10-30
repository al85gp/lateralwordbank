-- Error: the fact has one row per key that any load up to the selected one requested, counted
-- from raw.run_scope, and every key of the selected load was merged from it. So no requested
-- combination can silently go missing, and a merge never skips the new load.
with scope as (
    select distinct
        c.country_iso3,
        i.indicator_code,
        cast(y.year as integer) as year,
        loads.load_id
    from {{ source('raw', 'run_scope') }} as run_scope
    inner join {{ ref('stg_wb__loads') }} as loads
        on run_scope._dlt_load_id = loads.load_id
    cross join unnest(from_json(run_scope.countries, '["VARCHAR"]')) as c(country_iso3)
    cross join unnest(
        list_transform(from_json(run_scope.indicators, '[{"code": "VARCHAR"}]'), indicator -> indicator.code)
    ) as i(indicator_code)
    cross join unnest(range(run_scope.year_start, run_scope.year_end + 1)) as y(year)
),

expected as (
    select count(distinct (country_iso3, indicator_code, year)) as expected_rows
    from scope
),

fact as (
    select count(*) as actual_rows
    from {{ ref('fct_indicator_year') }}
),

not_merged as (
    select count(*) as selected_keys_not_merged
    from scope
    inner join {{ ref('stg_wb__selected_load') }} as selected
        on scope.load_id = selected.load_id
    left join {{ ref('fct_indicator_year') }} as fact
        on fact.country_iso3 = scope.country_iso3
        and fact.indicator_code = scope.indicator_code
        and fact.year = scope.year
        and fact.scope_load_id = scope.load_id
    where fact.country_iso3 is null
)

select expected.expected_rows, fact.actual_rows, not_merged.selected_keys_not_merged
from expected
cross join fact
cross join not_merged
where expected.expected_rows <> fact.actual_rows
   or not_merged.selected_keys_not_merged > 0
