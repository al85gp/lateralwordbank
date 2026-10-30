-- Grain: country x indicator x year, for every key any load up to the selected one requested. A
-- missing value is a row with a status, never an absent row and never a 0.
--
-- Incremental, merged on the grain: a run merges only the selected load's spine, updating the
-- keys it requested and inserting new ones (a wider config adds rows). Keys it did not request
-- keep the value of the latest load that did. A full refresh (`rebuild`) gives the same result by
-- keeping, per key, the latest load in stg_wb__loads that requested it.
{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['country_iso3', 'indicator_code', 'year'],
        on_schema_change='fail',
    )
}}

with loads as (
    select
        load_id,
        inserted_at
    from {{ ref('stg_wb__loads') }}
    {% if is_incremental() %}
    where is_selected
    {% endif %}
),

spine as (
    select
        scope.country_iso3,
        scope.indicator_code,
        scope.year,
        scope.load_id
    from {{ ref('stg_wb__scope') }} as scope
    inner join loads
        on scope.load_id = loads.load_id
    qualify row_number() over (
        partition by scope.country_iso3, scope.indicator_code, scope.year
        order by loads.inserted_at desc, loads.load_id desc
    ) = 1
),

observations as (
    select observations.*
    from {{ ref('stg_wb__observations') }} as observations
    inner join loads
        on observations.load_id = loads.load_id
),

indicator_updates as (
    select
        load_id,
        indicator_code,
        max(source_last_updated) as source_last_updated
    from observations
    group by all
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
    spine.load_id as scope_load_id,
    observations.load_id,
    observations.request_url,
    observations.body_sha256,
    observations.record_index
from spine
left join observations
    on spine.load_id = observations.load_id
    and spine.country_iso3 = observations.country_iso3
    and spine.indicator_code = observations.indicator_code
    and spine.year = observations.year
left join indicator_updates
    on spine.load_id = indicator_updates.load_id
    and spine.indicator_code = indicator_updates.indicator_code
