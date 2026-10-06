-- A decision dated in the future means a parsing error.
select drn, decision_date
from {{ ref('stg_fos__decisions') }}
where decision_date > current_date
