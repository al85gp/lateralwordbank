-- Grain: the full requested spine, country x indicator x year. A missing value is a row with a
-- status, never an absent row and never a 0.
with spine as (
    select
        country_iso3,
        indicator_code,
        year
    from {{ ref('stg_wb__scope') }}
),

observations as (
    select * from {{ ref('stg_wb__observations') }}
),

indicator_updates as (
    select
        indicator_code,
        max(source_last_updated) as source_last_updated
    from observations
    group by indicator_code
)

select
    spine.country_iso3,
    spine.indicator_code,
    spine.year,
    observations.value,
    case
        when observations.country_iso3 is null then 'not_returned'
        when observations.value is null then 'null_in_source'
        else 'reported'
    end as value_status,
    observations.obs_status,
    observations.decimals,
    coalesce(observations.source_last_updated, indicator_updates.source_last_updated) as source_last_updated,
    observations.load_id,
    observations.request_url,
    observations.body_sha256,
    observations.record_index
from spine
left join observations
    on spine.country_iso3 = observations.country_iso3
    and spine.indicator_code = observations.indicator_code
    and spine.year = observations.year
left join indicator_updates
    on spine.indicator_code = indicator_updates.indicator_code
