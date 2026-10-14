-- Error: every year-over-year row's numbers agree with its change_status, so a missing or zero
-- prior value can never produce a number that looks real.
select indicator_code, country_iso3, year, change_status, abs_change, pct_change
from {{ ref('rpt_yoy_change') }}
where not (
    (change_status = 'ok'
        and abs_change = value - prior_value
        and abs(pct_change - (value - prior_value) / abs(prior_value) * 100) < 1e-9)
    or (change_status = 'prior_is_zero' and prior_value = 0 and abs_change = value and pct_change is null)
    or (change_status in ('first_year_in_scope', 'no_current_value', 'no_prior_value')
        and abs_change is null and pct_change is null)
)
