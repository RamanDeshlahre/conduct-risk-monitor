-- Percentages must sit between 0 and 100 once ingestion has normalised them.
select *
from {{ ref('fca_firm_period') }}
where pct_upheld not between 0 and 100
   or pct_closed_within_3d not between 0 and 100
   or pct_closed_3d_to_8w not between 0 and 100
