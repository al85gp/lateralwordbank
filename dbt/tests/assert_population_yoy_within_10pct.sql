-- Warn: population rarely moves more than 10% in a year; a larger jump usually means a unit or
-- source problem worth reading.
{{ config(severity='warn') }}

with population as (
    select country_iso3, year, value
    from {{ ref('fct_indicator_year') }}
    where indicator_code = 'SP.POP.TOTL' and value_status = 'reported'
)

select
    current_year.country_iso3,
    current_year.year,
    prior_year.value as prior_value,
    current_year.value,
    (current_year.value - prior_year.value) / prior_year.value as relative_change
from population as current_year
inner join population as prior_year
    on prior_year.country_iso3 = current_year.country_iso3
    and prior_year.year = current_year.year - 1
where abs(current_year.value - prior_year.value) / prior_year.value > 0.10
