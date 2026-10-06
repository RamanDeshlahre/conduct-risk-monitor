{#- Use the folder's schema name as-is (staging, marts) instead of dbt's default "main_marts".
    On BigQuery, prefix with crm_ so datasets group together: crm_staging, crm_marts. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is none -%}
    {{ target.schema }}
  {%- elif target.type == 'bigquery' -%}
    crm_{{ custom_schema_name | trim }}
  {%- else -%}
    {{ custom_schema_name | trim }}
  {%- endif -%}
{%- endmacro %}
