select *
from {{ ref('fos_firm_theme_month') }}
where upheld > decisions or upheld < 0
