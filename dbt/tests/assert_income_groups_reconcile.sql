-- Error: for `sum` indicators the income groups add up to the total of the reporting countries,
-- and every country in scope is counted in exactly one group.
with groups as (
    select
        indicator_code,
        year,
        aggregation,
        sum(value) as group_total,
        sum(countries_in_group) as countries_in_groups
    from {{ ref('rpt_income_group_by_year') }}
    group by all
),

countries as (
    select
        indicator_code,
        year,
        sum(value) filter (where value_status = 'reported') as country_total,
        count(*) as countries_in_scope
    from {{ ref('fct_indicator_year') }}
    group by all
)

select groups.*, countries.country_total, countries.countries_in_scope
from groups
inner join countries
    on groups.indicator_code = countries.indicator_code
    and groups.year = countries.year
where groups.countries_in_groups <> countries.countries_in_scope
   or (groups.aggregation = 'sum' and abs(groups.group_total - countries.country_total) > 1e-6 * abs(countries.country_total))
