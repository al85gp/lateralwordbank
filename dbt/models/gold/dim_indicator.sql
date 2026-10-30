-- One row per indicator any load up to the selected one requested: unit and aggregation from the
-- latest load's config, name and source from that load's API response.
with scope as (
    select distinct
        scope.indicator_code,
        scope.unit,
        scope.aggregation,
        scope.load_id
    from {{ ref('stg_wb__scope') }} as scope
    inner join {{ ref('stg_wb__loads') }} as loads
        on scope.load_id = loads.load_id
    qualify dense_rank() over (
        partition by scope.indicator_code
        order by loads.inserted_at desc, loads.load_id desc
    ) = 1
),

from_api as (
    select
        load_id,
        indicator_code,
        min(indicator_name) as indicator_name,
        min(source_id) as source_id
    from {{ ref('stg_wb__observations') }}
    group by all
)

select
    scope.indicator_code,
    from_api.indicator_name,
    scope.unit,
    scope.aggregation,
    from_api.source_id
from scope
left join from_api
    on scope.load_id = from_api.load_id
    and scope.indicator_code = from_api.indicator_code
