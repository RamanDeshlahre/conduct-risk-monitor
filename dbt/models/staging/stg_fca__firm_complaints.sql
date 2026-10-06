-- One row per period x firm x product group x metric, with manual name overrides applied.
with source as (
    select * from {{ source('raw', 'fca_firm_complaints') }}
),
overrides as (
    select firm_key_from, firm_key_to from {{ ref('firm_name_overrides') }}
)
select
    s.period,
    cast(s.period_start as date)                         as period_start,
    coalesce(o.firm_key_to, s.firm_key)                  as firm_key,
    trim(s.firm_name)                                    as firm_name,
    nullif(trim(s.group_name), '')                       as group_name,
    lower(cast(s.joint_reporting as {{ dbt.type_string() }})) = 'yes' as is_joint_reporter,
    s.reporting_period,
    s.product_group,
    s.metric,
    cast(s.value as {{ dbt.type_float() }})              as value
from source s
left join overrides o on s.firm_key = o.firm_key_from
where s.firm_key is not null and s.firm_key != ''
