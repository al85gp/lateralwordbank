-- Error: the lineage of every reported value resolves to the exact raw record, and that record
-- holds the same country, year and value. Proves a value can be traced to the response it came from.
select
    fact.country_iso3,
    fact.indicator_code,
    fact.year,
    fact.value
from {{ ref('fct_indicator_year') }} as fact
left join {{ source('raw', 'api_responses') }} as raw_page
    on raw_page._dlt_load_id = fact.load_id
    and raw_page.request_url = fact.request_url
where fact.value_status = 'reported'
  and (
      raw_page.body is null
      or raw_page.body_sha256 <> fact.body_sha256
      or json_extract_string(raw_page.body, '$[1][' || fact.record_index || '].countryiso3code') <> fact.country_iso3
      or json_extract_string(raw_page.body, '$[1][' || fact.record_index || '].indicator.id') <> fact.indicator_code
      or cast(json_extract_string(raw_page.body, '$[1][' || fact.record_index || '].date') as integer) <> fact.year
      or cast(json_extract_string(raw_page.body, '$[1][' || fact.record_index || '].value') as double) <> fact.value
  )
