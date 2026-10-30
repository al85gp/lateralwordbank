-- Every completed load up to and including the selected one, oldest first. Stage parses all of
-- them; gold keeps, per key, the value from the latest load that asked for it. An incremental
-- build of the fact merges only the selected load (is_selected); a full refresh replays them all.
select
    loads.load_id,
    loads.inserted_at,
    loads.load_id = selected.load_id as is_selected
from {{ source('raw', '_dlt_loads') }} as loads
inner join {{ ref('stg_wb__selected_load') }} as selected
    on (loads.inserted_at, loads.load_id) <= (selected.inserted_at, selected.load_id)
where loads.status = 0
  and exists (
      select 1
      from {{ source('raw', 'run_scope') }} as scope
      where scope._dlt_load_id = loads.load_id
  )
