-- The requested spine of the selected load: every country x indicator x year it asked for.
with scope as (
    select run_scope.*
    from {{ source('raw', 'run_scope') }} as run_scope
    inner join {{ ref('stg_wb__selected_load') }} as selected
        on run_scope._dlt_load_id = selected.load_id
),

countries as (
    select unnest(from_json(countries, '["VARCHAR"]')) as country_iso3
    from scope
),

indicators as (
    select unnest(
        from_json(indicators, '[{"code": "VARCHAR", "unit": "VARCHAR", "aggregation": "VARCHAR"}]'),
        recursive := true
    )
    from scope
),

years as (
    select cast(unnest(range(year_start, year_end + 1)) as integer) as year
    from scope
)

select
    countries.country_iso3,
    indicators.code as indicator_code,
    years.year,
    indicators.unit,
    indicators.aggregation,
    scope._dlt_load_id as load_id
from countries
cross join indicators
cross join years
cross join scope
