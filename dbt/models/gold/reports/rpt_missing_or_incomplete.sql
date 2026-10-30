-- R-ANL-01: which country x indicator x year combinations are missing, with the coverage of
-- their series. Empty when every requested value was reported.
with fact as (
    select * from {{ ref('fct_indicator_year') }}
),

series as (
    select
        country_iso3,
        indicator_code,
        count(*) filter (where value_status = 'reported') as years_reported,
        count(*) as years_expected
    from fact
    group by all
)

select
    fact.indicator_code,
    indicators.indicator_name,
    fact.country_iso3,
    countries.country_name,
    fact.year,
    fact.value_status,
    series.years_reported,
    series.years_expected,
    fact.request_url,
    fact.body_sha256,
    fact.record_index
from fact
inner join series
    on fact.country_iso3 = series.country_iso3
    and fact.indicator_code = series.indicator_code
left join {{ ref('dim_country') }} as countries
    on fact.country_iso3 = countries.country_iso3
left join {{ ref('dim_indicator') }} as indicators
    on fact.indicator_code = indicators.indicator_code
where fact.value_status <> 'reported'
