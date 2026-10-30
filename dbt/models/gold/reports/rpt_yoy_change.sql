-- R-ANL-02: year-over-year change per country and indicator. The prior year is exactly year - 1.
-- Missing current or prior value: both changes null. Prior value 0: abs_change is still computed,
-- pct_change is null (D-03). change_status says which case applies.
with fact as (
    select * from {{ ref('fct_indicator_year') }}
),

first_year as (
    select min(year) as year from fact
)

select
    current_year.indicator_code,
    indicators.indicator_name,
    current_year.country_iso3,
    countries.country_name,
    current_year.year,
    current_year.value,
    current_year.year - 1 as prior_year,
    prior_year.value as prior_value,
    case
        when current_year.value is not null and prior_year.value is not null
            then current_year.value - prior_year.value
    end as abs_change,
    case
        when current_year.value is not null and prior_year.value is not null and prior_year.value <> 0
            then (current_year.value - prior_year.value) / abs(prior_year.value) * 100
    end as pct_change,
    case
        when current_year.year = (select year from first_year) then 'first_year_in_scope'
        when current_year.value is null then 'no_current_value'
        when prior_year.value is null then 'no_prior_value'
        when prior_year.value = 0 then 'prior_is_zero'
        else 'ok'
    end as change_status,
    current_year.request_url,
    current_year.body_sha256,
    current_year.record_index
from fact as current_year
left join fact as prior_year
    on prior_year.country_iso3 = current_year.country_iso3
    and prior_year.indicator_code = current_year.indicator_code
    and prior_year.year = current_year.year - 1
left join {{ ref('dim_country') }} as countries
    on current_year.country_iso3 = countries.country_iso3
left join {{ ref('dim_indicator') }} as indicators
    on current_year.indicator_code = indicators.indicator_code
