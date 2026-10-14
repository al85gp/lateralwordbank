-- Error: population and GDP are above 0; life expectancy is between 20 and 100 years.
select country_iso3, indicator_code, year, value
from {{ ref('fct_indicator_year') }}
where value_status = 'reported'
  and (
      (indicator_code in ('SP.POP.TOTL', 'NY.GDP.MKTP.CD', 'NY.GDP.PCAP.CD') and value <= 0)
      or (indicator_code = 'SP.DYN.LE00.IN' and value not between 20 and 100)
  )
