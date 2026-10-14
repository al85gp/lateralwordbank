-- R-ANL-03: countries compared at the latest available year of each indicator. Per D-04 that is
-- each country's own latest year with a reported value, so it can differ between countries;
-- years_behind shows when a comparison spans different years.
with fact as (
    select * from {{ ref('fct_indicator_year') }}
),

series as (
    select
        country_iso3,
        indicator_code,
        max(year) filter (where value_status = 'reported') as latest_year
    from fact
    group by all
),

latest as (
    select
        series.indicator_code,
        series.country_iso3,
        series.latest_year,
        fact.value,
        fact.request_url,
        fact.body_sha256,
        fact.record_index
    from series
    left join fact
        on fact.country_iso3 = series.country_iso3
        and fact.indicator_code = series.indicator_code
        and fact.year = series.latest_year
)

select
    latest.indicator_code,
    indicators.indicator_name,
    indicators.unit,
    latest.country_iso3,
    countries.country_name,
    latest.latest_year,
    latest.value,
    -- 1 = highest value; null for a country with no reported value in scope.
    case
        when latest.value is not null
            then rank() over (partition by latest.indicator_code order by latest.value desc nulls last)
    end as value_rank,
    max(latest.latest_year) over (partition by latest.indicator_code) - latest.latest_year as years_behind,
    latest.request_url,
    latest.body_sha256,
    latest.record_index
from latest
left join {{ ref('dim_country') }} as countries
    on latest.country_iso3 = countries.country_iso3
left join {{ ref('dim_indicator') }} as indicators
    on latest.indicator_code = indicators.indicator_code
