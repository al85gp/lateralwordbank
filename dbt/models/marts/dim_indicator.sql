-- One row per indicator in scope: unit and aggregation from config, name and source from the API.
with scope as (
    select distinct
        indicator_code,
        unit,
        aggregation
    from {{ ref('stg_wb__scope') }}
),

from_api as (
    select
        indicator_code,
        min(indicator_name) as indicator_name,
        min(source_id) as source_id
    from {{ ref('stg_wb__observations') }}
    group by indicator_code
)

select
    scope.indicator_code,
    from_api.indicator_name,
    scope.unit,
    scope.aggregation,
    from_api.source_id
from scope
left join from_api
    on scope.indicator_code = from_api.indicator_code
