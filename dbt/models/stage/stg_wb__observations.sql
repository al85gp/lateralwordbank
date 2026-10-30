-- One row per observation record per load, from each load's indicator responses, typed and flattened.
with pages as (
    select
        responses._dlt_load_id as load_id,
        responses.request_url,
        responses.body_sha256,
        responses.source_last_updated,
        json_extract_string(responses.body, '$[0].sourceid') as source_id,
        responses.body
    from {{ source('raw', 'api_responses') }} as responses
    inner join {{ ref('stg_wb__loads') }} as loads
        on responses._dlt_load_id = loads.load_id
    where responses.endpoint = 'indicator'
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
    record ->> '$.countryiso3code' as country_iso3,
    record ->> '$.indicator.id' as indicator_code,
    cast(record ->> '$.date' as integer) as year,
    -- A JSON null stays null; ints and floats both land as DOUBLE. Never coerced to 0.
    cast(record ->> '$.value' as double) as value,
    record ->> '$.indicator.value' as indicator_name,
    nullif(record ->> '$.obs_status', '') as obs_status,
    cast(record ->> '$.decimal' as integer) as decimals,
    source_last_updated,
    source_id,
    load_id,
    request_url,
    body_sha256,
    record_index
from records
