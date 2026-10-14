-- One row per country in the selected load.
select
    country_iso3,
    iso2_code,
    country_name,
    region_name,
    income_group_code,
    income_group_name
from {{ ref('stg_wb__countries') }}
