-- One row per decision month: how many decisions are published and how far into the month they reach.
-- FOS publishes with a lag of several weeks, so the newest month is usually incomplete. Early warning
-- and the MI note use this to judge only complete months, rather than reading a half-published month
-- as a fall in complaints.
select
    decision_month,
    count(*)                         as decisions,
    sum(is_upheld)                   as upheld,
    min(decision_date)               as first_decision_date,
    max(decision_date)               as last_decision_date,
    count(distinct decision_date)    as days_with_decisions
from {{ ref('stg_fos__decisions') }}
group by decision_month
