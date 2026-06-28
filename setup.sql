-- skill-secret v4: Supabase schema setup.
-- Run this ONCE in the Supabase SQL editor for your project, then re-run
-- `secret init`. The Python module `supabase_kms.py` ships the same SQL
-- in the `_SCHEMA_SQL` constant — keep them in sync.

create table if not exists notes (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    body text not null,
    kind text not null default 'note' check (kind in ('note', 'bootstrap')),
    created_at timestamptz not null default now()
);

create index if not exists notes_kind_idx on notes (kind);

create or replace function search_notes(
    query_text text,
    max_results int
) returns table (
    id uuid,
    title text,
    body text,
    rank real
) language sql stable as $$
    select
        n.id,
        n.title,
        n.body,
        ts_rank(
            to_tsvector('english', n.title || ' ' || n.body),
            websearch_to_tsquery('english', query_text)
        ) as rank
    from notes n
    where
        n.kind = 'note'
        and to_tsvector('english', n.title || ' ' || n.body)
            @@ websearch_to_tsquery('english', query_text)
    order by rank desc
    limit max_results;
$$;