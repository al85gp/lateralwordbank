-- Error: within the selected load the API returned each record once. A duplicate would make two
-- different raw records claim the same fact.
select 'observation' as record_type, concat_ws('|', country_iso3, indicator_code, year) as record_key, count(*) as copies
from {{ ref('stg_wb__observations') }}
group by all
having count(*) > 1

union all

select 'country' as record_type, country_iso3 as record_key, count(*) as copies
from {{ ref('stg_wb__countries') }}
group by all
having count(*) > 1
