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
// This file is also the one definition of a valid fact value (decided on #27,
// 28 Oct 2026). checkSnapshot is the whole of it, the pull runs it on the
// snapshot it is about to write, and the Python suite asks it through
// tests/check_facts.js rather than holding a copy -- because two copies of a
// rule drift, and a copy that drifts is a snapshot a pull writes and the
// repo's own checks then reject.
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
//
// optional names the columns a fact may leave empty, because the fact has
// nothing to put there; every other column has to show a reader something.
// lists names the columns the tables restrict to a short list of words, with
// the same words supabase/schema.sql holds.
const KINDS = [
  { table: 'status_steps', columns: ['position', 'as_of', 'name', 'state', 'detail'] },
  // A cost's figure is the number alone and its unit the small tail beside
  // it, as the page shows them: "61,365" and "lines, in 8 days".
  {
    table: 'costs',
    columns: ['position', 'as_of', 'figure', 'unit', 'lead', 'claim', 'source'],
    optional: ['unit'],
  },
  // standing is empty when a measure has no bar to be judged against, and is
  // one of three words when it has: the same three the tables allow
  // (check (standing in ('hit', 'pending', 'miss'))) and the page's own test
  // reads. A word off the list, or one of them in another letter case, is a
  // snapshot the checks would then reject, so it never gets written.
  {
    table: 'measures',
    columns: ['position', 'as_of', 'label', 'bar', 'current', 'standing'],
    optional: ['standing'],
    lists: { standing: ['hit', 'pending', 'miss'] },
  },
  { table: 'record_entries', columns: ['position', 'as_of', 'headline', 'entry'] },
];

// The same allowlist the page's link tests use: a fact may link this repo or
// its Pages site, and nowhere else.
const ALLOWED_LINK_PREFIXES = [
  'https://github.com/todd450/founding-agents-site',
  'https://todd450.github.io/founding-agents-site',
];

// The cases this rule has to answer are in tests/test_rules.py, which asks
// this copy -- the only one -- through tests/check_facts.js.
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
// The characters a fact's text may carry, as an allowlist (decided on #27,
// 28 Oct 2026). A fact is prose, a date, a figure, `code` and [text](url), so
// what it needs is letters, marks, numbers, punctuation, symbols and ordinary
// spaces -- Unicode's general categories L, M, N, P, S and Zs -- and never a
// character Unicode itself calls default-ignorable, which is invisible by
// design even where its category is an allowed one.
//
// An allowlist because the list it replaced could not be finished: round after
// round named another character outside it -- the bidirectional marks, the
// soft hyphen, the variation selectors, the invisible operators, the fillers
// -- and every one of them is the same two harms. A character a reader cannot
// see fills a required column and leaves the page blank, and a character that
// reorders what follows it makes the page state one thing and the snapshot
// another. Neither shows in `git diff`, and that a human reads the diff is
// this snapshot's whole safety argument.
//
// Written as the complement, so one pass over a value finds every character it
// refuses and the reason a pull gives can name them. "Every character is in L,
// M, N, P, S or Zs, and none of them is default-ignorable" is the same rule
// read the other way round.
//
// A line ending is outside the allowlist too, and is reported as the line
// ending it is rather than by code point: UNREADABLE_TEXT runs first and
// gives the reason a renderer makes of it.
const REFUSED_CHARACTER = /[^\p{L}\p{M}\p{N}\p{P}\p{S}\p{Zs}]|\p{Default_Ignorable_Code_Point}/gu;
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
//
// Neither class may hold the character that opens what it is inside: no "["
// in the text, no "(" in the destination. That is what keeps the reader
// linear in the length of the text. A class that can swallow its own opening
// character rescans from every one of them, so a value made of a long run of
// "[" takes the reader minutes rather than milliseconds -- and a fact's text
// has no length limit (tests/test_rules.py pins the measurement).
//
// It costs nothing a fact needs: a destination may hold a balanced pair of
// parentheses, which this reader must not read as a whole address anyway, and
// link text may hold balanced brackets, which it must not either. In both the
// text keeps its "](" and is reported as a link that cannot be read whole.
const LINK_TARGET = /\[[^[\]]*\]\(([^()]*)\)/g;
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
// The other two spellings of a link, which name their destination elsewhere:
// a reference or collapsed link ("[text][ref]", "[text][]") and the
// definition that gives it an address ("[ref]: ..."). Neither carries a
// "](", and a definition's address needs no "://", so a reader looking for
// those finds nothing at all where a renderer finds a link.
const REFERENCE_LINK = /\]\[/;
// No "[" in the label here either, for the same reason: with one, every line
// start is a fresh scan of the whole text. A label holds no unescaped "["
// anyway, and a backslash before one is refused before any link is read.
const LINK_DEFINITION = /^ {0,3}\[[^[\]]*\]:/m;

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
// reader could not read whole.
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
// "][" and a line that defines a reference are each a link it cannot see
// whole. A bracket in a link's text and a parenthesis in its destination need
// no rule of their own: LINK_TARGET matches neither, so such a link is left
// in the residue with its "](" intact. When the reader cannot read a link
// whole it says so, and the pull refuses the fact rather than vouching for a
// link it never read.
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
    || LINK_DEFINITION.test(text);
  return { targets, unreadable };
}

function isRealDate(value) {
  if (typeof value !== 'string' || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(value);
}

// Every character in this value the allowlist refuses, by code point, in the
// order they are first found -- so the reason a pull gives names a character
// nobody reading the page or the diff could have named for themselves.
function refusedCharacters(value) {
  const found = typeof value === 'string' ? value.match(REFUSED_CHARACTER) || [] : [];
  return [...new Set(found)].map(
    (character) => `U+${character.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')}`);
}

// A day, and not one later than the day it is measured against. A fact is
// true "as of" a day, and the snapshot is stamped with the day it was pulled;
// either one dated after the day of the pull is a clock, a time zone or a
// typo, and the page would show a reader a date in the future with nothing
// downstream in a position to notice (decided on #27, 28 Oct 2026). ISO dates
// compare as strings, which is the whole reason the snapshot writes them that
// way.
//
// The day a date is measured against is named in the reason, because the two
// have different repairs: a fact after the day of the pull is a date to fix,
// and a fact after pulled_at is a stamp to move.
function checkDay(where, value, day, dayIs = 'the day of the pull', repair = '') {
  if (!isRealDate(value)) fail(`${where} is not a date: ${JSON.stringify(value)}`);
  if (value > day) fail(`${where} is dated after ${dayIs} (${day}): ${value}${repair}`);
}

// The newest day any of these facts is true as of, which is the earliest day
// a snapshot of them can honestly say it was pulled on.
function newestAsOf(facts) {
  return Object.values(facts)
    .flat()
    .reduce((newest, row) => (row.as_of > newest ? row.as_of : newest), '');
}

// The brackets this text would put on the page as markup -- or, when this
// reader cannot read the text the way a renderer would, the character that
// makes it unreadable, because then what is quoted as code, and what the page
// would show, is nobody's guess.
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
  const refused = refusedCharacters(value);
  if (refused.length > 0) {
    fail(`${where} carries ${refused.join(', ')}, which a fact's text may not: a fact carries `
      + 'letters, marks, numbers, punctuation, symbols and ordinary spaces (Unicode L, M, N, P, '
      + 'S and Zs) and no character that is invisible by default, wherever in a value it sits, '
      + 'inside backticks or out');
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
// carrying exactly its columns, each column checked. day is the day of the
// pull, which no fact may be dated after.
function checkRows(kind, rows, day) {
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
    checkDay(`${where}.as_of`, row.as_of, day);
    const out = { position, as_of: row.as_of };
    for (const column of textColumns) {
      const optional = (kind.optional || []).includes(column);
      const value = row[column] === null && optional ? '' : row[column];
      if (value === undefined) fail(`${where} has no ${column}`);
      const text = checkText(`${where}.${column}`, value);
      // "" is what an optional column holds when the fact has nothing to put
      // there, and it is the only empty any column may hold. `not null` in
      // the tables stops none of this -- "" is not null -- so a step with no
      // state, a measure with no label, or a record entry of one tab would
      // otherwise be written into the snapshot and leave a gap on the page
      // that nothing downstream notices.
      if (!(optional && text === '') && text.trim() === '') {
        fail(`${where}.${column} is blank: ${JSON.stringify(text)}`);
      }
      const list = (kind.lists || {})[column];
      if (list && text !== '' && !list.includes(text)) {
        fail(`${where}.${column} is not one of ${list.join(', ')}: ${JSON.stringify(text)}`);
      }
      out[column] = text;
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

async function readTable(kind, { fetchImpl, baseUrl, key, timeoutMs, day }) {
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
  return checkRows(kind, rows, day);
}

// The one check on a whole snapshot, and the only definition of a valid fact
// value there is (decided on #27, 28 Oct 2026): the keys the snapshot holds
// and their order, the day it was pulled on, and every row of every kind by
// the same rules a read is checked against.
//
// Two callers, which is the point. pull() runs it on the snapshot it is about
// to write, so no pull can leave the repo red; and the Python suite runs it
// on the committed file through tests/check_facts.js, so what check.sh
// demands of the snapshot and what a pull enforces cannot be two different
// rules.
function checkSnapshot(snapshot, options = {}) {
  // options.today is the day to judge the snapshot's dates against: a pull
  // passes the day it is running on, and a test the day it is asking about.
  // Left out -- which is how check.sh and CI ask -- it is the latest day any
  // calendar has reached, because the calendar the checker is on is not the
  // one the snapshot was pulled on.
  const day = options.today || latestDayAnywhere();
  if (snapshot === null || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
    fail(`the snapshot is not an object: ${JSON.stringify(snapshot)}`);
  }
  const expected = ['pulled_at', ...KINDS.map((kind) => kind.table)];
  const keys = Object.keys(snapshot);
  if (keys.join(',') !== expected.join(',')) {
    fail(`the snapshot's keys are not ${expected.join(', ')}, in that order: ${keys.join(', ')}`);
  }
  checkDay('pulled_at', snapshot.pulled_at, day);
  for (const kind of KINDS) {
    const rows = snapshot[kind.table];
    if (!Array.isArray(rows)) fail(`${kind.table} is not a list of rows`);
    rows.forEach((row, index) => {
      const where = `${kind.table}[${index}]`;
      if (row === null || typeof row !== 'object' || Array.isArray(row)) {
        fail(`${where} is not a row`);
      }
      if (Object.keys(row).join(',') !== kind.columns.join(',')) {
        fail(`${where} does not carry exactly ${kind.columns.join(', ')}, in that order: `
          + `${Object.keys(row).join(', ')}`);
      }
    });
    // checkRows puts the rows in position order and refuses a gap or a
    // repeat, so rows a reader would see out of order are the ones it moved.
    const checked = checkRows(kind, rows, day);
    if (serialise(checked) !== serialise(rows)) {
      fail(`${kind.table} is not stored in position order, so the page would read in one order `
        + 'and the snapshot in another');
    }
    // And no fact is true as of a day the snapshot had not been pulled on.
    // pulled_at is carried forward whenever a pull finds nothing new, so
    // without this the stamp and the facts can part company for good: a stamp
    // edited behind the facts, or a stale fact repaired by hand and dated the
    // day it was repaired, would leave the file claiming it was read before
    // the facts in it were true. It is also the rule that keeps the two
    // callers in step -- the suite judges the committed file as of the day in
    // the file, and a pull judges its own output as of the day it runs, and
    // as_of <= pulled_at <= the day of the pull is what makes those the same
    // question.
    for (const row of checked) {
      checkDay(`${kind.table}[${row.position - 1}].as_of`, row.as_of, snapshot.pulled_at,
        'pulled_at', ': a fact cannot be true as of a day the snapshot had not been pulled on, '
        + 'so pulled_at belongs on the day the facts were last read');
    }
  }
  return snapshot;
}

function serialise(snapshot) {
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}

function factsOf(snapshot) {
  const facts = { ...snapshot };
  delete facts.pulled_at;
  return serialise(facts);
}

// The snapshot as it stands, as bytes and as facts: the bytes say whether
// this pull changes the file at all, and the facts say what it said.
function readPrevious(out) {
  let text;
  try {
    text = fs.readFileSync(out, 'utf8');
  } catch {
    return { text: null, snapshot: null };
  }
  try {
    return { text, snapshot: JSON.parse(text) };
  } catch {
    return { text, snapshot: null };
  }
}

// The day of the pull, as the person running it would write it: their own
// calendar day, and not the UTC one. toISOString() gives the UTC day, which
// west of UTC stamps the snapshot with a day that has not started for them,
// and east of UTC refuses a fact they entered this morning as a day that has
// not happened -- for every pull before 09:00 in a zone nine hours ahead.
// The stamp and that refusal are both this one day, so it is the day where
// the pull is run.
function today(now = new Date()) {
  const month = `${now.getMonth() + 1}`.padStart(2, '0');
  const dayOfMonth = `${now.getDate()}`.padStart(2, '0');
  return `${now.getFullYear()}-${month}-${dayOfMonth}`;
}

// The furthest ahead of UTC any calendar in use runs, in hours: UTC+14, on
// the line islands.
const FURTHEST_AHEAD_HOURS = 14;

// The latest day any calendar on Earth has reached, which is the day a check
// judges a snapshot's dates against when it is not told one.
//
// Whoever pulled the snapshot and whatever is checking it are not in the same
// place: a pull stamps the file with the day where it was run, CI runs in
// UTC, and a reviewer runs check.sh wherever they are. Judging by the
// checker's own day would make a snapshot pulled in a zone ahead of it a file
// dated in the future, and freeze every merge for the hours the two calendars
// disagree. So a date is from the future only when no calendar anywhere has
// reached it: a typo (2099) or a clock a month out is still refused, and the
// day's benefit of the doubt goes to the zone the checker cannot see.
function latestDayAnywhere(now = new Date()) {
  const anywhere = new Date(now.getTime() + FURTHEST_AHEAD_HOURS * 60 * 60 * 1000);
  return anywhere.toISOString().slice(0, 10);
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
  const day = options.today || today();
  const settings = {
    fetchImpl: options.fetchImpl || globalThis.fetch,
    baseUrl: options.baseUrl || SUPABASE_URL,
    key: options.key || SUPABASE_PUBLISHABLE_KEY,
    timeoutMs: options.timeoutMs || READ_TIMEOUT_MS,
    day,
  };
  const out = options.out || SNAPSHOT_PATH;

  sweep(out);

  const facts = {};
  for (const kind of KINDS) {
    // One table at a time: the first bad read stops the pull.
    facts[kind.table] = await readTable(kind, settings);
  }

  // pulled_at is the day these facts were last pulled and found to differ, so
  // a pull that finds nothing new rewrites the same bytes and shows an empty
  // diff rather than a date change nobody needs to review.
  //
  // A stamp already in the file is carried forward only if it is a day the
  // facts it stamps could have been read on: not one that has not happened
  // (a fast clock, or a hand edit, writes a stamp in the future that no later
  // pull would look at again, and the page would show a reader a date that
  // has not come), and not one earlier than the newest fact under it (a stamp
  // edited backwards, or a fact repaired by hand and dated the day it was
  // repaired, leaves the file claiming it was read before its own facts were
  // true). Either way the day of the pull is the truth, so the stamp is
  // replaced and the next pull is also the fix.
  const previous = readPrevious(out);
  const unchanged = previous.snapshot !== null && factsOf(previous.snapshot) === serialise(facts);
  const stamp = unchanged ? previous.snapshot.pulled_at : null;
  const carried = isRealDate(stamp) && stamp <= day && stamp >= newestAsOf(facts);
  const snapshot = { pulled_at: carried ? stamp : day, ...facts };

  // The pull's own output, by the rules check.sh holds the committed snapshot
  // to -- the same function, not a second copy of it. A pull that wrote a
  // snapshot the checks then rejected would have to be undone by hand in the
  // reviewed file, so it refuses instead.
  checkSnapshot(snapshot, { today: day });
  const text = serialise(snapshot);
  write(out, text);
  // What the diff will show: the bytes that were there against the bytes now.
  return { path: out, changed: previous.text !== text };
}

// The working file this script writes beside the snapshot, named for the
// process that wrote it.
const WORKING_FILE = /^\d+\.tmp$/;

// Any working file an earlier run left in the snapshot's directory, cleared
// before this one starts. A run killed between the write and the rename
// leaves its file behind -- a signal skips the cleanup in write() -- and the
// leftover is as large as the snapshot, sits untracked beside a reviewed
// file where a `git add -A` can commit it, and accumulates one per process
// until something clears it. The next pull is that something, whether it goes
// on to write a snapshot or to refuse the read.
function sweep(out) {
  const directory = path.dirname(out);
  const prefix = `${path.basename(out)}.`;
  let names;
  try {
    names = fs.readdirSync(directory);
  } catch {
    return; // no directory yet, so nothing to clear
  }
  for (const name of names) {
    if (name.startsWith(prefix) && WORKING_FILE.test(name.slice(prefix.length))) {
      fs.rmSync(path.join(directory, name), { force: true });
    }
  }
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
  checkSnapshot,
  isAllowedLink,
  linkTargets,
  markupOutsideBackticks,
  refusedCharacters,
  today,
  ALLOWED_LINK_PREFIXES,
  KINDS,
  SNAPSHOT_PATH,
  SUPABASE_URL,
  SUPABASE_PUBLISHABLE_KEY,
};
