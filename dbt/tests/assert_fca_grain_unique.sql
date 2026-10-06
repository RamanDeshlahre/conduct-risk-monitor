-- Fails if any firm x product group x period appears twice.
select period, firm_key, product_group, count(*) as n
from {{ ref('fca_firm_period') }}
group by period, firm_key, product_group
having count(*) > 1
