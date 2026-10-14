-- Error: the fact has exactly countries x indicators x years rows of the load's scope, counted
-- straight from raw.run_scope, so no requested combination can silently go missing.
with scope as (
    select
        json_array_length(run_scope.countries)
        * json_array_length(run_scope.indicators)
        * (run_scope.year_end - run_scope.year_start + 1) as expected_rows
    from {{ source('raw', 'run_scope') }} as run_scope
    inner join {{ ref('stg_wb__selected_load') }} as selected
        on run_scope._dlt_load_id = selected.load_id
),

fact as (
    select count(*) as actual_rows
    from {{ ref('fct_indicator_year') }}
)

select scope.expected_rows, fact.actual_rows
from scope
cross join fact
where scope.expected_rows <> fact.actual_rows
