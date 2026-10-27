#!/usr/bin/env node
//
// Pulls the page's facts out of Supabase and writes content/facts.json, the
// reviewed snapshot this repo commits (issue #27).
//
//   node scripts/pull-facts.js
//
// Only this script ever talks to Supabase. check.sh, CI, the generator and
// every deploy read the snapshot in the repo, so they stay offline and
// deterministic and a Supabase outage can never freeze a merge. A pull is a
// pull request like any other: run this, read the diff, and ship it.
//
// It writes nothing unless all four reads came back whole and usable. A read
// that is refused, slow, short, or carrying a row this page cannot show
// leaves the snapshot exactly as it was and exits non-zero
// (fixtures/failed-read).
//
// Node 22 and its standard library only; nothing here ships to the page.

'use strict';

const fs = require('node:fs');
const path = require('node:path');

// The project, and its publishable key: read-only, with row-level security on
// and a select-only policy for the anon role, so committing it gives away
// nothing that https://todd450.github.io/founding-agents-site/ does not
// already show. No service-role key is stored anywhere.
const SUPABASE_URL = 'https://hrpvhjsefhvjigfvlsmk.supabase.co';
const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_-WGmJhAUjuXfMwOKSTgi7w_8w9Y-UBo';

const SNAPSHOT_PATH = path.join(__dirname, '..', 'content', 'facts.json');
const READ_TIMEOUT_MS = 15000;

// One table per kind of fact, in the order the snapshot holds them. Every row
// carries position (1..n, the order it appears in) and as_of (the date the
// fact is true as of), then its own typed columns.
const KINDS = [
  { table: 'status_steps', columns: ['position', 'as_of', 'name', 'state', 'detail'] },
  // A cost's figure is the number alone and its unit the small tail beside
  // it, as the page shows them: "61,365" and "lines, in 8 days".
  {
    table: 'costs',
    columns: ['position', 'as_of', 'figure', 'unit', 'lead', 'claim', 'source'],
    optional: ['unit'],
  },
  {
    table: 'measures',
    columns: ['position', 'as_of', 'label', 'bar', 'current', 'standing'],
    optional: ['standing'],
  },
  { table: 'record_entries', columns: ['position', 'as_of', 'headline', 'entry'] },
];

// The same allowlist the page's link tests use: a fact may link this repo or
// its Pages site, and nowhere else.
const ALLOWED_LINK_PREFIXES = [
  'https://github.com/todd450/founding-agents-site',
  'https://todd450.github.io/founding-agents-site',
];

// The same rule as tests/links.py, which checks the snapshot and the page;
// tests/test_links.py runs both copies over the same cases.
const PLAIN_URL_CHARS = /^[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]*$/;
const ESCAPE = /%[0-9A-Fa-f]{2}/g;
const SEPARATOR_ESCAPE = /%(?:2[eEfF]|5[cC]|[01][0-9A-Fa-f]|7[fF])/;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const CODE_SPAN = /`[^`]*`/g;
const URL_IN_TEXT = /[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s)\]]+|(?<![\w/])\/\/[^\s)\]]+/g;
// What a [text](url) link points at, whatever its scheme: a target of
// "javascript:..." or a relative path carries no "://" for URL_IN_TEXT.
const LINK_TARGET = /\[[^\]]*\]\(([^)]*)\)/g;

class PullError extends Error {}

function fail(message) {
  throw new PullError(message);
}

// Where a link goes, not what it starts with. An address beginning with this
// repo's own and carrying a ".." segment resolves somewhere else entirely:
// https://github.com/todd450/founding-agents-site/../../attacker/evil is
// https://github.com/attacker/evil to every browser.
function isAllowedLink(link) {
  if (typeof link !== 'string') return false;
  for (const prefix of ALLOWED_LINK_PREFIXES) {
    if (!link.startsWith(prefix)) continue;
    const rest = link.slice(prefix.length);
    // A prefix match counts only if what follows it is a path, query or
    // fragment separator, or nothing at all: a lookalike host or repository
    // whose name merely starts with this one's is not this one.
    if (rest !== '' && !'/?#'.includes(rest[0])) continue;
    // Only the plain URL characters: no backslash, space, tab or newline,
    // each of which a browser rewrites or strips before it resolves the
    // address. A percent escape may stand for ordinary text (?q=is%3Apr) but
    // not for a character that decides where the address goes -- "%2e%2e"
    // resolves as "..".
    if (!PLAIN_URL_CHARS.test(rest)) return false;
    if (SEPARATOR_ESCAPE.test(rest) || rest.replace(ESCAPE, '').includes('%')) return false;
    if (rest.split('/').some((segment) => segment === '.' || segment === '..')) return false;
    // The browser's own parser has the last word on where the link lands.
    let resolved;
    try {
      resolved = new URL(link);
    } catch {
      return false;
    }
    if (resolved.protocol !== 'https:') return false;
    const landing = resolved.origin + resolved.pathname;
    return landing === prefix || (landing.startsWith(prefix) && landing[prefix.length] === '/');
  }
  return false;
}

function isRealDate(value) {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value);
}

// Text columns hold text, never HTML. A link is [text](url) and a literal
// value is `code`; the generator renders those two and escapes the rest. So a
// < or > anywhere but inside backticks would reach the page as markup nobody
// reviewed, and is a read this script refuses.
function checkText(where, value) {
  if (typeof value !== 'string') fail(`${where} is not text: ${JSON.stringify(value)}`);
  if ((value.match(/`/g) || []).length % 2 !== 0) {
    fail(`${where} has an unclosed backtick, so what is quoted as code is unclear`);
  }
  const outside = value.replace(CODE_SPAN, '');
  for (const bracket of ['<', '>']) {
    if (outside.includes(bracket)) {
      fail(`${where} carries ${bracket} outside backticks, which would put markup on the page`);
    }
  }
  const targets = [...value.matchAll(LINK_TARGET)].map((match) => match[1]);
  for (const link of targets.concat(value.match(URL_IN_TEXT) || [])) {
    if (!isAllowedLink(link)) fail(`${where} links off this site: ${link}`);
  }
  return value;
}

// The rows of one table, as they will be written: in position order, each row
// carrying exactly its columns, each column checked.
function checkRows(kind, rows) {
  if (!Array.isArray(rows)) fail(`${kind.table}: the answer was not a list of rows`);
  if (rows.length === 0) {
    fail(`${kind.table}: no rows came back, which would empty a section of the page`);
  }
  const [, , ...textColumns] = kind.columns;
  const checked = rows.map((row, index) => {
    const where = `${kind.table}[${index}]`;
    if (row === null || typeof row !== 'object' || Array.isArray(row)) fail(`${where} is not a row`);
    const position = row.position;
    if (!Number.isInteger(position) || position < 1) {
      fail(`${where} has no position: ${JSON.stringify(position)}`);
    }
    if (!isRealDate(row.as_of)) {
      fail(`${where} has no as_of date: ${JSON.stringify(row.as_of)}`);
    }
    const out = { position, as_of: row.as_of };
    for (const column of textColumns) {
      const optional = (kind.optional || []).includes(column);
      const value = row[column] === null && optional ? '' : row[column];
      if (value === undefined) fail(`${where} has no ${column}`);
      out[column] = checkText(`${where}.${column}`, value);
    }
    return out;
  });
  checked.sort((a, b) => a.position - b.position);
  checked.forEach((row, index) => {
    if (row.position !== index + 1) {
      fail(`${kind.table}: positions are not 1..n with no gap and no repeat `
        + `(${checked.map((r) => r.position).join(', ')})`);
    }
  });
  return checked;
}

// PostgREST answers "0-5/6", or "*/0" for an empty table. Asking for an exact
// count is how a whole read is told from one the connection cut short.
function totalFromContentRange(header, table) {
  const total = typeof header === 'string' ? /\/(\d+)$/.exec(header) : null;
  if (!total) fail(`${table}: no row count came back, so a short read cannot be ruled out`);
  return Number(total[1]);
}

async function readTable(kind, { fetchImpl, baseUrl, key, timeoutMs }) {
  const url = new URL(`/rest/v1/${kind.table}`, baseUrl);
  url.searchParams.set('select', kind.columns.join(','));
  url.searchParams.set('order', 'position.asc');

  let response;
  try {
    response = await fetchImpl(url.toString(), {
      headers: {
        apikey: key,
        authorization: `Bearer ${key}`,
        accept: 'application/json',
        prefer: 'count=exact',
      },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    fail(`${kind.table}: the read did not come back (${error.message})`);
  }
  if (response.status !== 200) {
    fail(`${kind.table}: the read answered ${response.status}, not 200`);
  }

  let body;
  try {
    body = await response.text();
  } catch (error) {
    fail(`${kind.table}: the body did not arrive (${error.message})`);
  }
  let rows;
  try {
    rows = JSON.parse(body);
  } catch {
    fail(`${kind.table}: the answer was not JSON, so the body was cut off or is an error page`);
  }

  const total = totalFromContentRange(response.headers.get('content-range'), kind.table);
  if (!Array.isArray(rows) || rows.length !== total) {
    fail(`${kind.table}: ${Array.isArray(rows) ? rows.length : 0} rows came back of ${total}`);
  }
  return checkRows(kind, rows);
}

function serialise(snapshot) {
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}

function factsOf(snapshot) {
  const facts = { ...snapshot };
  delete facts.pulled_at;
  return serialise(facts);
}

function readPrevious(out) {
  try {
    return JSON.parse(fs.readFileSync(out, 'utf8'));
  } catch {
    return null;
  }
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Reads all four tables, then writes the snapshot. Nothing is written until
 * every read is in and sound, so a failure leaves the file as it was.
 *
 * options.today is the day the pull happens on, so a test can run the same
 * read weeks apart and see whether the snapshot's stamp moved; left out, it
 * is today.
 *
 * @returns {{path: string, changed: boolean}}
 */
async function pull(options = {}) {
  const settings = {
    fetchImpl: options.fetchImpl || globalThis.fetch,
    baseUrl: options.baseUrl || SUPABASE_URL,
    key: options.key || SUPABASE_PUBLISHABLE_KEY,
    timeoutMs: options.timeoutMs || READ_TIMEOUT_MS,
  };
  const out = options.out || SNAPSHOT_PATH;

  const facts = {};
  for (const kind of KINDS) {
    // One table at a time: the first bad read stops the pull.
    facts[kind.table] = await readTable(kind, settings);
  }

  // pulled_at is the day these facts were last pulled and found to differ, so
  // a pull that finds nothing new rewrites the same bytes and shows an empty
  // diff rather than a date change nobody needs to review.
  const previous = readPrevious(out);
  const unchanged = previous !== null && factsOf(previous) === serialise(facts);
  const pulled_at = unchanged && isRealDate(previous.pulled_at) ? previous.pulled_at : (options.today || today());

  write(out, serialise({ pulled_at, ...facts }));
  return { path: out, changed: !unchanged };
}

// Written beside the snapshot and moved over it, so the file a reader sees is
// either the old snapshot or the whole new one, never half of either.
function write(out, text) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const temporary = `${out}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, text, 'utf8');
    fs.renameSync(temporary, out);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

if (require.main === module) {
  pull()
    .then(({ path: out, changed }) => {
      console.log(`${changed ? 'wrote' : 'no change to'} ${out}`);
    })
    .catch((error) => {
      console.error(`pull-facts: ${error.message}`);
      console.error('content/facts.json is untouched.');
      process.exitCode = 1;
    });
}

module.exports = {
  pull, isAllowedLink, KINDS, SNAPSHOT_PATH, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY,
};
