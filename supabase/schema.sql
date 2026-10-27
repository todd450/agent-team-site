-- #27's four fact tables. Each row has its place on the page (position) and the date it is true as of.
-- Text columns hold text, never HTML: links are [text](url) and literal values `code`.
-- Row-level security is on, and the anon role may only select.

create table status_steps (
  position int primary key check (position > 0),
  as_of date not null,
  name text not null,
  state text not null,
  detail text not null
);

create table costs (
  position int primary key check (position > 0),
  as_of date not null,
  figure text not null,
  lead text not null,
  claim text not null,
  source text not null
);

create table measures (
  position int primary key check (position > 0),
  as_of date not null,
  label text not null,
  bar text not null,
  current text not null,
  standing text check (standing in ('hit', 'pending', 'miss'))
);

create table record_entries (
  position int primary key check (position > 0),
  as_of date not null,
  headline text not null,
  entry text not null
);

do $$
declare t text;
begin
  foreach t in array array['status_steps', 'costs', 'measures', 'record_entries'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy "anyone can read" on %I for select to anon using (true)', t);
    execute format('revoke insert, update, delete, truncate on %I from anon, authenticated', t);
  end loop;
end $$;
