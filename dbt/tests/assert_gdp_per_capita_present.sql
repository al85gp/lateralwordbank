-- Warn: when GDP and population are both reported, GDP per capita should be reported too.
{{ config(severity='warn') }}

with fact as (
    select * from {{ ref('fct_indicator_year') }}
)

select gdp.country_iso3, gdp.year, per_capita.value_status as gdp_per_capita_status
from fact as gdp
inner join fact as population
    on population.country_iso3 = gdp.country_iso3 and population.year = gdp.year
    and population.indicator_code = 'SP.POP.TOTL'
inner join fact as per_capita
    on per_capita.country_iso3 = gdp.country_iso3 and per_capita.year = gdp.year
    and per_capita.indicator_code = 'NY.GDP.PCAP.CD'
where gdp.indicator_code = 'NY.GDP.MKTP.CD'
  and gdp.value_status = 'reported'
  and population.value_status = 'reported'
  and per_capita.value_status <> 'reported'
