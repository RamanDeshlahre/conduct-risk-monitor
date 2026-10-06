-- One row per firm x product group x half-year, with every FCA metric as a column.
select
    period,
    period_start,
    firm_key,
    max(firm_name)                                                         as firm_name,
    max(group_name)                                                        as group_name,
    product_group,
    max(case when metric = 'opened' then value end)                        as opened,
    max(case when metric = 'closed' then value end)                        as closed,
    max(case when metric = 'pct_closed_within_3d' then value end)          as pct_closed_within_3d,
    max(case when metric = 'pct_closed_3d_to_8w' then value end)           as pct_closed_3d_to_8w,
    max(case when metric = 'pct_upheld' then value end)                    as pct_upheld,
    max(case when metric = 'context_provision' then value end)             as context_provision,
    max(case when metric = 'context_intermediation' then value end)        as context_intermediation
from {{ ref('stg_fca__firm_complaints') }}
where metric != 'consumer_credit'
group by period, period_start, firm_key, product_group
