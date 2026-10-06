-- One row per ombudsman decision. Free text stays out of the warehouse on purpose.
with source as (
    select * from {{ source('raw', 'fos_decisions_enriched') }}
),
overrides as (
    select firm_key_from, firm_key_to from {{ ref('firm_name_overrides') }}
)
select
    s.drn,
    cast(s.decision_date as date)                    as decision_date,
    cast(s.decision_month as date)                   as decision_month,
    coalesce(o.firm_key_to, s.firm_key)              as firm_key,
    trim(s.firm_name)                                as firm_name,
    s.outcome,
    cast(s.is_upheld as {{ dbt.type_int() }})        as is_upheld,
    s.sector,
    s.primary_theme,
    s.theme_label,
    s.consumer_duty_outcome,
    s.consumer_duty_label,
    cast(s.redress_max_gbp as {{ dbt.type_float() }}) as redress_max_gbp
from source s
left join overrides o on s.firm_key = o.firm_key_from
where s.decision_date is not null
