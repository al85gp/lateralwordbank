-- The requested spine of each load: every country x indicator x year it asked for.
with scope as (
    select run_scope.*
    from {{ source('raw', 'run_scope') }} as run_scope
    inner join {{ ref('stg_wb__loads') }} as loads
        on run_scope._dlt_load_id = loads.load_id
),

countries as (
    select
        _dlt_load_id as load_id,
        unnest(from_json(countries, '["VARCHAR"]')) as country_iso3
    from scope
),

indicators as (
    select
        _dlt_load_id as load_id,
        unnest(
            from_json(indicators, '[{"code": "VARCHAR", "unit": "VARCHAR", "aggregation": "VARCHAR"}]'),
            recursive := true
        )
    from scope
),

years as (
    select
        _dlt_load_id as load_id,
        cast(unnest(range(year_start, year_end + 1)) as integer) as year
    from scope
)

select
    countries.country_iso3,
    indicators.code as indicator_code,
    years.year,
    indicators.unit,
    indicators.aggregation,
    countries.load_id
from countries
inner join indicators
    on countries.load_id = indicators.load_id
inner join years
    on countries.load_id = years.load_id
