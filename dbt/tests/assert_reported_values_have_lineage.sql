-- Error: every value that came from the API carries full lineage, and only not_returned rows lack it.
select country_iso3, indicator_code, year, value_status
from {{ ref('fct_indicator_year') }}
where (value_status <> 'not_returned'
       and (load_id is null or request_url is null or body_sha256 is null or record_index is null))
   or (value_status = 'not_returned' and value is not null)
   or (value_status <> 'reported' and value is not null)
