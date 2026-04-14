SELECT
    n.nspname AS schema,
    c.relname AS table,
    pg_size_pretty(pg_table_size(c.oid)) AS table_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS index_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    c.reltuples::bigint AS total_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname IN ('faers', 'medlens')
ORDER BY n.nspname, pg_total_relation_size(c.oid) DESC;



-- truncate tables before re-running data builder to avoid duplicates during development
TRUNCATE faers.demo, faers.drug, faers.indi, faers.outc, faers.reac, faers.ther;