-- One row per firm across both sources, so the dashboard can show both pictures side by side.
with fca as (
    select firm_key, max(firm_name) as fca_name, max(group_name) as group_name, max(period) as latest_fca_period
    from {{ ref('stg_fca__firm_complaints') }}
    group by firm_key
),
fos as (
    select firm_key, max(firm_name) as fos_name, count(*) as fos_decisions, avg(cast(is_upheld as {{ dbt.type_float() }})) as fos_uphold_rate
    from {{ ref('stg_fos__decisions') }}
    group by firm_key
),
keys as (
    select firm_key from fca
    union distinct
    select firm_key from fos
)
select
    k.firm_key,
    coalesce(fos.fos_name, fca.fca_name)       as firm_name,
    fca.group_name,
    fca.firm_key is not null                    as in_fca_data,
    fos.firm_key is not null                    as in_fos_data,
    fca.latest_fca_period,
    coalesce(fos.fos_decisions, 0)              as fos_decisions,
    fos.fos_uphold_rate
from keys k
left join fca on k.firm_key = fca.firm_key
left join fos on k.firm_key = fos.firm_key
