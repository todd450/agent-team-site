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
// A parenthesis is not among the plain URL characters: CommonMark keeps a
// balanced pair inside a link's destination, so a reader that stops at the
// first ")" is left holding a prefix of the address and would vouch for a
// page nobody wrote down. Nor is an ampersand: a renderer resolves a
// character reference inside a destination and an HTML parser resolves one
// inside an href, so ".../&period;&period;/evil" and ".../&#37;2e&#37;2e/evil"
// are dots and escapes by the time a browser reads them. No "&" means no
// reference in any spelling; a semicolon goes with it, since it ends one.
const PLAIN_URL_CHARS = /^[A-Za-z0-9\-._~:/?#[\]@!$'*+,=%]*$/;
const ESCAPE = /%[0-9A-Fa-f]{2}/g;
const SEPARATOR_ESCAPE = /%(?:2[eEfF]|5[cC]|[01][0-9A-Fa-f]|7[fF])/;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
// What two backticks quote as code: a short literal, between single
// backticks, on one line of text that holds nothing a renderer reads before
// it reads an inline.
//
// A renderer is stricter than it looks here. It closes a run of N backticks
// only with the next run of exactly N; it reads a backslash-escaped backtick
// as a literal character rather than a delimiter; it decodes a character
// reference ("&#96;", "&#60;") before it reads anything at all; and it cuts
// the text into blocks first, so a line ending or a cell separator can end a
// span -- or stop one forming -- before any backtick is paired. Each of those
// is a pairing a reader that takes "the next backtick" as the closer gets
// wrong, and getting it wrong puts a live tag where this reader saw quoted
// code.
//
// So rather than parse blocks inside a string that has none, a fact's text is
// one line that quotes in pairs of single backticks, and each of these is
// reported instead of read:
//
//   "\n"  a line ending, which ends the paragraph and the span with it -- a
//         blank line, a list item, a heading, a thematic break or the second
//         row a table needs. CommonMark counts a lone carriage return as a
//         line ending too, so "\r\r" is a paragraph break exactly as "\n\n"
//         is.
//   "\\"  a backslash, an escape a renderer honours and a reader cannot see.
//   "&"   an ampersand, which may begin a character reference: what the text
//         says and what the page shows are then two different strings.
//   "|"   a pipe, a cell separator wherever the text lands in a row: a row is
//         split into cells before any inline is read, so the backticks on
//         either side of one never pair.
//   "`"   backticks this reader cannot pair: a run of two or more, or an odd
//         number of them.
//
// What is left is one line with no escape, no reference and no separator in
// it, where every run is one backtick long and the count is even. A renderer
// reads that left to right exactly as this reader does -- nothing before the
// first backtick can open a construct that swallows it, since any "<" outside
// the pairs is reported -- so a "<" this reader finds inside a span is inside
// one for a renderer too, whatever block the text is rendered in.
const CODE_SPAN = /`[^`\n\r]*`/g;
// In the order they are reported, so both copies answer any text the same way.
const UNREADABLE_TEXT = [
  ['\n', (value) => /[\n\r]/.test(value)],
  ['\\', (value) => /\\/.test(value)],
  ['&', (value) => value.includes('&')],
  ['|', (value) => value.includes('|')],
  ['`', (value) => /``/.test(value) || (value.match(/`/g) || []).length % 2 !== 0],
];
// What a [text](url) link points at, whatever its scheme.
const LINK_TARGET = /\[[^\]]*\]\(([^)]*)\)/g;
// A bare address, or anything else a renderer turns into a link on its own.
// GFM autolinks a literal "https://..." run, a scheme-relative "//host", a
// "www." host and an email address, and it ends such a run at whitespace --
// keeping the brackets and parentheses a reader stops at, so where a reader
// thinks a bare address ends and where a renderer puts it in the href are two
// different answers. A fact writes a link one way, [text](url), which this
// reader reads whole; link-like text in any other form is refused rather than
// read (decided on #27, 27 Oct 2026).
const LINK_LIKE = /:\/\/|\/\/|www\.|@/i;
// A backslash before a bracket or a parenthesis: an escape a renderer
// honours and this reader cannot see, so the two disagree about where a link
// ends.
const ESCAPED_BRACKET = /\\[[\]()]/;
// A parenthesis inside a target the reader did read: a destination may hold
// a balanced pair, so what the reader stopped at is not where the link ends.
const PAREN = /[()]/;
// The other two spellings of a link, which name their destination elsewhere:
// a reference or collapsed link ("[text][ref]", "[text][]") and the
// definition that gives it an address ("[ref]: ..."). Neither carries a
// "](", and a definition's address needs no "://", so a reader looking for
// those finds nothing at all where a renderer finds a link.
const REFERENCE_LINK = /\]\[/;
const LINK_DEFINITION = /^ {0,3}\[[^\]]*\]:/m;

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
    // A dot segment needs no slash after it to walk up a level: a query, a
    // fragment or the end of the address ends the path just as well, so the
    // segments are read from the path alone.
    const [pathOnly] = rest.split(/[?#]/, 1);
    if (pathOnly.split('/').some((segment) => segment === '.' || segment === '..')) return false;
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

// Where every link in this text points, and whether it also writes one this
// reader could not read whole. The same reader as tests/links.py.
//
// A renderer reads more as a link than LINK_TARGET does. CommonMark says link
// text may hold balanced brackets ("[see [this]](...)"), an image may be
// wrapped in a link ("[![chart](ok.png)](...)"), a bracket may be
// backslash-escaped, a destination may hold a balanced pair of parentheses
// ("(.../a(b)/...)"), and a link may name its destination elsewhere
// ("[text][ref]" with "[ref]: ..."); GFM autolinks a bare address with no
// brackets at all. In each of those this reader finds no target at all, or
// only part of one, so the destination a renderer would put in the href is a
// destination nothing checked -- which is how a "javascript:" target walks
// past an allowlist.
//
// So the reader accounts for every "](" the text contains: whatever is left
// once the links it did read are taken out must hold none, and must hold
// nothing else link-like either. Beyond that, a backslash-escaped bracket, a
// target carrying a parenthesis, a "][" and a line that defines a reference
// are each a link it cannot see whole. When it cannot read one whole it says
// so, and the pull refuses the fact rather than vouching for a link it never
// read.
function linkTargets(text) {
  const targets = [];
  const residue = text.replace(LINK_TARGET, (span, target) => {
    targets.push(target);
    return ' '; // a space, so taking a link out can join no new "]("
  });
  const unreadable = residue.includes('](')
    || LINK_LIKE.test(residue)
    || ESCAPED_BRACKET.test(text)
    || REFERENCE_LINK.test(text)
    || LINK_DEFINITION.test(text)
    || targets.some((target) => PAREN.test(target));
  return { targets, unreadable };
}

function isRealDate(value) {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value);
}

// The brackets this text would put on the page as markup -- or, when this
// reader cannot read the text the way a renderer would, the character that
// makes it unreadable, because then what is quoted as code, and what the page
// would show, is nobody's guess. The same reader as tests/links.py;
// tests/check_links.js runs both copies over the same texts.
function markupOutsideBackticks(value) {
  const unreadable = UNREADABLE_TEXT
    .filter(([, carries]) => carries(value))
    .map(([found]) => found);
  if (unreadable.length > 0) return unreadable;
  const outside = value.replace(CODE_SPAN, '');
  return ['<', '>'].filter((bracket) => outside.includes(bracket));
}

// Why each character markupOutsideBackticks reports is a read this script
// refuses: the text and the page would not say the same thing.
const UNREADABLE_REASON = {
  '\n': 'carries a line break, and a fact is one line: a line ending ends the paragraph, and '
    + 'with it any code span, so what the text quotes depends on blocks it does not carry',
  '\\': 'carries a backslash, an escape a renderer honours and this reader cannot see, so the '
    + 'two disagree about what is quoted and where a link ends',
  '&': 'carries an ampersand, which may begin a character reference: a renderer decodes '
    + '"&#60;" into a bracket and "&period;" into a dot, so what the text says is not what '
    + 'the page would show',
  '|': 'carries a pipe, a cell separator wherever the text is rendered inside a row, which is '
    + 'read before any code span is paired',
  '`': 'quotes code in a form this snapshot does not read: a fact quotes a short literal '
    + 'between single backticks, so what this one quotes is unclear',
};

// Text columns hold text, never HTML. A link is [text](url) and a literal
// value is `code`; the generator renders those two and escapes the rest. So a
// < or > anywhere but inside backticks would reach the page as markup nobody
// reviewed, and is a read this script refuses -- as is a text whose markup
// this reader and a renderer would not read the same way.
function checkText(where, value) {
  if (typeof value !== 'string') fail(`${where} is not text: ${JSON.stringify(value)}`);
  for (const found of markupOutsideBackticks(value)) {
    if (UNREADABLE_REASON[found]) fail(`${where} ${UNREADABLE_REASON[found]}`);
    fail(`${where} carries ${found} outside backticks, which would put markup on the page`);
  }
  const { targets, unreadable } = linkTargets(value);
  if (unreadable) {
    fail(`${where} writes a link in a form this snapshot does not read: a fact writes a link `
      + 'as [text](url) and nothing else, so where this one points is a destination nothing '
      + 'has checked');
  }
  for (const link of targets) {
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
  pull,
  isAllowedLink,
  linkTargets,
  markupOutsideBackticks,
  KINDS,
  SNAPSHOT_PATH,
  SUPABASE_URL,
  SUPABASE_PUBLISHABLE_KEY,
};
