-- Market-wide view for the dashboard: volumes, uphold rates and redress by month, sector and theme.
select
    decision_month,
    sector,
    primary_theme,
    max(theme_label)                                        as theme_label,
    consumer_duty_label,
    count(*)                                                as decisions,
    sum(is_upheld)                                          as upheld,
    sum(case when redress_max_gbp > 0 then 1 else 0 end)   as decisions_with_redress_amount,
    sum(coalesce(redress_max_gbp, 0))                       as redress_gbp_in_sample
from {{ ref('stg_fos__decisions') }}
group by decision_month, sector, primary_theme, consumer_duty_label
