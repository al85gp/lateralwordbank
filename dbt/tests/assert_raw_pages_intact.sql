-- Error: every raw page of the loads a build reads is stored exactly as received (its SHA-256 matches),
-- and fetched_url is the canonical URL on attempt 1, or the canonical URL plus that attempt's
-- cache-buster token on a retry.
select
    raw_page.request_url,
    raw_page.fetched_url,
    raw_page.attempt
from {{ source('raw', 'api_responses') }} as raw_page
inner join {{ ref('stg_wb__loads') }} as loads
    on raw_page._dlt_load_id = loads.load_id
where sha256(raw_page.body) <> raw_page.body_sha256
   or raw_page.fetched_url <> case
        when raw_page.attempt = 1 then raw_page.request_url
        else raw_page.request_url || '&cb=' || raw_page._dlt_load_id || '-' || raw_page.attempt
      end
