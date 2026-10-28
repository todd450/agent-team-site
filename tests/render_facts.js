#!/usr/bin/env node
//
// What a real renderer makes of every fact value this repo accepts.
//
//   node tests/render_facts.js
//
// scripts/pull-facts.js reads a fact's text itself -- which characters it may
// carry, what it quotes as code, where its links point -- because a pull is
// Node's standard library and nothing else. This is the oracle for that
// reading: markdown-it, a CommonMark + GFM renderer pinned in
// devDependencies, renders every text the gate accepts and asserts the two
// properties the gate exists for.
//
//   (a) every href and src a renderer would put on the page is relative, or
//       is a link this site vouches for (isAllowedLink).
//   (b) every "<" and ">" in a fact's text lands inside a <code> element, and
//       so reaches the page as a character rather than as markup.
//
// Those are properties of the rendered page, not of the gate's wording, so a
// spelling the gate reads differently from a renderer is a failing check here
// rather than an off-site link on the page. That is the thing this file is
// for: the gate is a hand-written reader, every round of review found another
// spelling it read differently, and reasoning is what kept missing them.
// Writing it found one -- a bare host with a real top-level domain
// ("evil-grabber.com/steal"), which markdown-it autolinks and the gate's
// link-like rule walked straight past, looking only for "://", "//", "www."
// and "@".
//
// The corpus is every text value in the committed snapshot, every text value
// in every scenario in fixtures/failed-read, and the forms below: a renderer
// makes a link or an element of each of those. A text the gate refuses is
// skipped and counted -- the gate is the first line, and this is the check on
// what gets through it.
//
// check.sh runs this after `npm ci`. Nothing here ships to the page and
// nothing here makes a network call.

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const MarkdownIt = require('markdown-it');

const { checkText, isAllowedLink, KINDS, SNAPSHOT_PATH } = require('../scripts/pull-facts.js');

const FIXTURES = path.join(__dirname, '..', 'fixtures', 'failed-read');

// linkify, because a renderer that autolinks is the one a fact's text has to
// survive: GFM autolinks a bare address, and markdown-it's linkify goes
// further still and autolinks a bare host with no scheme at all. html: false,
// because a fact's text is text and the generator (#28) renders it as text.
const RENDERER = { html: false, linkify: true };

// Forms a renderer turns into a link or an element, each with what it makes
// of it. The point of the corpus is that the gate refuses all of them: if one
// ever gets through, this check says what reaches the page.
const RENDERED_FORMS = [
  ['Recorded 26 Oct 2026; figures at evil-grabber.com/steal.',
    'a bare host with a real top-level domain, autolinked with no scheme in the text'],
  ['See agents.io/x for detail.', 'the same, on a two-letter top-level domain'],
  ['Notes at notes.co.uk/x.', 'the same, on a two-level one'],
  ['Run check.sh before you push.', 'a filename a renderer reads as a host: "sh" is a country'],
  ['Mail steal@attacker.example for detail.', 'an email address, autolinked as mailto:'],
  ['See www.attacker.example/evil for detail.', 'a www host, autolinked with no scheme'],
  ['See https://evil.example/x for detail.', 'a bare address, autolinked whole'],
  ['Served over //evil.example/x now.', 'a scheme-relative host, autolinked'],
  ['[the writeup [part two]](javascript:alert(1))',
    'a link to javascript:, since link text may hold balanced brackets'],
  ['[the run \\]log](javascript:alert(1))', 'the same, past a backslash-escaped bracket'],
  ['See [the writeup][r] for detail.\n\n[r]: javascript:alert(1)',
    'a reference link to javascript:, whose destination is named elsewhere'],
  ['An <img src=x onerror=alert(1)> outside backticks.', 'an image element, raw'],
  ['A flag &#60;img src=x&#62; here.', 'the same element, spelled in character references'],
  ['The flag ```<img src=x>` is set.', 'the same element, past backticks that do not pair'],
  ['`x\n\n<img src=x>`', 'the same element, past backticks a blank line apart'],
  ['A literal `<li>` in backticks is quoted code.', 'a code element, which is the allowed way'],
  ['Run `check.sh` and read `content/facts.json`.', 'two code elements, and no link at all'],
];

function load(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

// Every text value in a row of facts, whatever it came from: the columns
// after position and as_of are the ones that hold text a reader sees.
function textsInRows(rows, columns) {
  const texts = [];
  if (!Array.isArray(rows)) return texts;
  for (const row of rows) {
    if (row === null || typeof row !== 'object') continue;
    for (const column of columns.slice(2)) {
      if (typeof row[column] === 'string') texts.push(row[column]);
    }
  }
  return texts;
}

// The corpus: what this repo holds, and the forms above. Each entry is the
// text and where it came from, so a failure names the fact to fix.
function corpus() {
  const found = [];
  const snapshot = load(SNAPSHOT_PATH);
  for (const kind of KINDS) {
    for (const text of textsInRows(snapshot[kind.table], kind.columns)) {
      found.push([text, `content/facts.json ${kind.table}`]);
    }
  }
  for (const file of fs.readdirSync(FIXTURES).sort()) {
    if (!file.endsWith('.json')) continue;
    const scenario = load(path.join(FIXTURES, file));
    for (const kind of KINDS) {
      // rows.json holds the tables themselves; a scenario holds the rows it
      // answers one read with.
      const rows = [scenario[kind.table], (scenario.responses || {})[kind.table]?.rows];
      for (const text of rows.flatMap((some) => textsInRows(some, kind.columns))) {
        found.push([text, `fixtures/failed-read/${file} ${kind.table}`]);
      }
    }
  }
  for (const [text, renders] of RENDERED_FORMS) {
    found.push([text, `a form a renderer reads as ${renders}`]);
  }
  return found;
}

// Does the gate let this text into a snapshot at all? The gate's own
// function, not a reading of it: what this check judges is what gets through.
function accepted(text) {
  try {
    checkText('a fact', text);
    return true;
  } catch {
    return false;
  }
}

function everyToken(tokens, found = []) {
  for (const token of tokens || []) {
    found.push(token);
    everyToken(token.children, found);
  }
  return found;
}

function brackets(text) {
  return (text.match(/[<>]/g) || []).length;
}

// Why a renderer's reading of this text is not one this page can show: the
// addresses it would put in an href or a src that this site does not vouch
// for, and any "<" or ">" that would reach the page as markup rather than as
// a character inside a <code> element.
function problems(text) {
  const tokens = everyToken(new MarkdownIt(RENDERER).parse(text, {}));
  const found = [];
  for (const token of tokens) {
    for (const attribute of ['href', 'src']) {
      const address = token.attrGet ? token.attrGet(attribute) : null;
      if (address === null) continue;
      // Relative, which stays on this site wherever the page is served, or
      // an address the allowlist vouches for. Anything else is a page nobody
      // reviewed, reached from a fact.
      const absolute = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(address) || address.startsWith('//');
      if (absolute && !isAllowedLink(address)) {
        found.push(`a renderer puts ${attribute}="${address}" on the page`);
      }
    }
  }
  // Every bracket in the text, against every bracket inside the code the
  // renderer quotes. A bracket this reader thought was quoted and the
  // renderer did not is a tag on the page.
  const quoted = tokens
    .filter((token) => ['code_inline', 'code_block', 'fence'].includes(token.type))
    .reduce((count, token) => count + brackets(token.content), 0);
  if (quoted !== brackets(text)) {
    found.push(`${brackets(text) - quoted} of its brackets land outside a <code> element, where `
      + 'they reach the page as markup');
  }
  return found;
}

function main() {
  const texts = corpus();
  const failures = [];
  let rendered = 0;
  for (const [text, where] of texts) {
    if (!accepted(text)) continue;
    rendered += 1;
    for (const problem of problems(text)) {
      failures.push(`${where}: ${problem}\n  the fact the gate accepted: ${JSON.stringify(text)}`);
    }
  }
  if (rendered === 0) {
    throw new Error('the gate accepted none of the corpus, so nothing was rendered at all');
  }
  for (const failure of failures) console.error(`render-facts: ${failure}`);
  if (failures.length > 0) {
    console.error(`render-facts: ${failures.length} of ${rendered} accepted fact values render `
      + 'into something this page cannot show');
    process.exitCode = 1;
    return;
  }
  console.log(`render-facts: markdown-it renders all ${rendered} accepted fact values of `
    + `${texts.length} with no off-site href and no bracket outside <code>`);
}

main();
