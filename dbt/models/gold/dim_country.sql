-- One row per country any load up to the selected one returned, from the latest load that did.
select
    countries.country_iso3,
    countries.iso2_code,
    countries.country_name,
    countries.region_name,
    countries.income_group_code,
    countries.income_group_name
from {{ ref('stg_wb__countries') }} as countries
inner join {{ ref('stg_wb__loads') }} as loads
    on countries.load_id = loads.load_id
qualify row_number() over (
    partition by countries.country_iso3
    order by loads.inserted_at desc, loads.load_id desc
) = 1
