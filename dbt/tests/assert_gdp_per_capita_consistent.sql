-- Warn: GDP per capita is within 1% of GDP / population for the same country and year.
-- A warning, not an error: the source revises indicators on different dates.
{{ config(severity='warn') }}

with fact as (
    select * from {{ ref('fct_indicator_year') }} where value_status = 'reported'
),

joined as (
    select
        gdp.country_iso3,
        gdp.year,
        per_capita.value as gdp_per_capita,
        gdp.value / population.value as gdp_over_population
    from fact as gdp
    inner join fact as population
        on population.country_iso3 = gdp.country_iso3 and population.year = gdp.year
        and population.indicator_code = 'SP.POP.TOTL'
    inner join fact as per_capita
        on per_capita.country_iso3 = gdp.country_iso3 and per_capita.year = gdp.year
        and per_capita.indicator_code = 'NY.GDP.PCAP.CD'
    where gdp.indicator_code = 'NY.GDP.MKTP.CD'
)

select
    *,
    abs(gdp_per_capita - gdp_over_population) / gdp_over_population as relative_difference
from joined
where abs(gdp_per_capita - gdp_over_population) / gdp_over_population > 0.01
