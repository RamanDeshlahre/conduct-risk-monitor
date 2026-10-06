-- Decision counts per firm x theme x month: the input to the Poisson early-warning test.
select
    firm_key,
    max(firm_name)              as firm_name,
    primary_theme,
    max(theme_label)            as theme_label,
    max(consumer_duty_label)    as consumer_duty_label,
    decision_month,
    count(*)                    as decisions,
    sum(is_upheld)              as upheld
from {{ ref('stg_fos__decisions') }}
group by firm_key, primary_theme, decision_month
