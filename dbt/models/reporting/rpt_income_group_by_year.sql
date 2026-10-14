-- R-ANL-04: indicators compared across income groups, by year. Each indicator aggregates the
-- way config says: `sum` (population, GDP) or `population_weighted_mean` (GDP per capita, life
-- expectancy), over reporting countries only. Coverage columns show what is missing.
with fact as (
    select
        fact.country_iso3,
        fact.indicator_code,
        fact.year,
        fact.value,
        countries.income_group_code,
        countries.income_group_name,
        indicators.aggregation
    from {{ ref('fct_indicator_year') }} as fact
    inner join {{ ref('dim_country') }} as countries
        on fact.country_iso3 = countries.country_iso3
    inner join {{ ref('dim_indicator') }} as indicators
        on fact.indicator_code = indicators.indicator_code
),

population as (
    select country_iso3, year, value as population
    from {{ ref('fct_indicator_year') }}
    where indicator_code = '{{ var("population_indicator") }}'
      and value_status = 'reported'
),

contributions as (
    select
        fact.*,
        population.population,
        case
            when fact.aggregation = 'sum' then fact.value is not null
            else fact.value is not null and population.population is not null
        end as is_reporting
    from fact
    left join population
        on fact.country_iso3 = population.country_iso3
        and fact.year = population.year
)

select
    indicator_code,
    income_group_code,
    income_group_name,
    year,
    aggregation,
    case aggregation
        when 'sum' then sum(value) filter (where is_reporting)
        when 'population_weighted_mean'
            then sum(value * population) filter (where is_reporting)
                 / sum(population) filter (where is_reporting)
    end as value,
    count(*) as countries_in_group,
    count(*) filter (where is_reporting) as countries_reporting,
    count(*) filter (where is_reporting) = count(*) as is_complete
from contributions
group by indicator_code, income_group_code, income_group_name, year, aggregation
