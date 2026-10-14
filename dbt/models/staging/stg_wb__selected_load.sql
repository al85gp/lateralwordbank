-- The one load every other model reads: var('load_id'), or else the latest completed load.
-- Only completed loads (status = 0) qualify, so rows left by a failed load step are never read.
with completed_loads as (
    select
        loads.load_id,
        loads.inserted_at
    from {{ source('raw', '_dlt_loads') }} as loads
    where loads.status = 0
      and exists (
          select 1
          from {{ source('raw', 'run_scope') }} as scope
          where scope._dlt_load_id = loads.load_id
      )
)

select
    load_id,
    inserted_at
from completed_loads
{% if var('load_id') is not none %}
where load_id = '{{ var("load_id") }}'
{% else %}
order by inserted_at desc, load_id desc
limit 1
{% endif %}
