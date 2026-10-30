-- One row per country record per load, from each load's country response.
with pages as (
    select
        responses._dlt_load_id as load_id,
        responses.request_url,
        responses.body_sha256,
        responses.body
    from {{ source('raw', 'api_responses') }} as responses
    inner join {{ ref('stg_wb__loads') }} as loads
        on responses._dlt_load_id = loads.load_id
    where responses.endpoint = 'country'
),

records as (
    select
        pages.*,
        idx.record_index,
        json_extract(pages.body, '$[1][' || idx.record_index || ']') as record
    from pages
    cross join unnest(range(coalesce(json_array_length(pages.body, '$[1]'), 0)::bigint)) as idx(record_index)
)

select
    record ->> '$.id' as country_iso3,
    record ->> '$.iso2Code' as iso2_code,
    trim(record ->> '$.name') as country_name,
    record ->> '$.region.id' as region_code,
    trim(record ->> '$.region.value') as region_name,
    record ->> '$.incomeLevel.id' as income_group_code,
    trim(record ->> '$.incomeLevel.value') as income_group_name,
    load_id,
    request_url,
    body_sha256,
    record_index
from records
