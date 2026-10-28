#!/usr/bin/env node
//
// Writes this site's pages from the three things the repo holds: the
// templates, the page's wording as string-key JSON, and the reviewed facts
// snapshot (issue #28).
//
//   npm run build                     write every page
//   node scripts/generate.js --check  fail if a committed page is not a fresh build
//
// The generated pages are committed at the repo root (P4, decided on #14), so
// the diff a reviewer reads is the bytes a reader gets and every existing
// check -- html-validate, stylelint, Playwright over file://, the Python suite
// -- keeps reading the served file as a repo file. check.sh runs --check, so a
// page edited by hand, or left behind by a change to a template or a fact,
// cannot merge.
//
// What it reads, all of it in the repo:
//
//   content/site.json           the site's address and title, in one place
//   content/copy/en/common.json the chrome every page shares, by key
//   content/copy/en/<page>.json one page's own wording, by key
//   content/facts.json          the facts snapshot scripts/pull-facts.js writes
//   templates/layout.html       head, masthead and footer
//   templates/pages/<page>.html one page's sections
//   templates/partials/<x>.html one partial per fact-driven section
//
// and where each page goes: templates/pages/index.html to index.html, and
// anything else to <name>/index.html. So a page added later costs a template
// and a copy file, and no change here.
//
// Node 22 and its standard library only -- no npm dependency, because the npm
// packages in this repo are for the checks and nothing they bring may ship to
// the page (D2, D8, decided on #14). It makes no network call: the facts it
// renders are the committed snapshot's, and only scripts/pull-facts.js ever
// talks to Supabase.

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const REPO = path.join(__dirname, '..');

// ---------------------------------------------------------------------------
// The template language. Six forms, and no more, because a template's job is
// the page's shape: the wording is a key and the facts are the snapshot's.
//
//   {{! a note }}             a note to whoever reads the template; it does
//                             not reach the page
//   {{>name}}                 include templates/partials/name.html, or the
//                             page's own template for the reserved name
//                             "page", which is the one the layout uses
//   {{#each kind}}...{{/each}} repeat for every row of that kind of fact, in
//                             position order
//   {{#if name}}...{{/if}}    only when that value is not empty
//   {{name}}                  the value or the copy key, as text
//   {{&name}}                 the same, read as a fact's text is read: its
//                             two inline forms become a link and a literal
//
// A tag alone on its line takes the line with it, so the page's indentation is
// the template's.
const COMMENT_LINE = /^[ \t]*\{\{![^}]*\}\}[ \t]*\r?\n/gm;
const COMMENT = /\{\{![^}]*\}\}/g;
const OWN_LINE = /^[ \t]*(\{\{(?:[#/>][^}]*)\}\})[ \t]*\r?\n/gm;
const INCLUDE = /\{\{>\s*([\w.-]+)\s*\}\}/g;
const BLOCK_OPEN = /\{\{#(each|if)\s+([\w.]+)\s*\}\}/;
const BLOCK_TAG = /\{\{(#(?:each|if)\s+[\w.]+|\/(?:each|if))\s*\}\}/g;
const VALUE = /\{\{(&?)\s*([\w.]+)\s*\}\}/g;
// Anything in a copy string this reader would not substitute. A copy string
// may name a value -- the site's address, a row's date -- and nothing else: a
// partial, a block or a raw insertion in one would put the page's shape in the
// wording, which is the thing the copy files exist to stop.
const NOT_A_VALUE_IN_COPY = /\{\{\s*[&#/>!]/;

// The two inline forms a fact's text may carry, read the way
// scripts/pull-facts.js reads them -- a link whose text holds no bracket and
// whose destination holds no parenthesis, and a short literal between single
// backticks on one line.
//
// The puller is the gate on what may be in the snapshot at all, so the two
// readings have to agree or a destination the puller checked is not the
// destination that reaches the page. tests/test_generate.py is what says they
// do: on every fact in the committed snapshot, the links this reader puts on
// the page are exactly the ones the puller reads in it, and taking these two
// forms back off the page gives the fact's text character for character. What
// a real renderer makes of the same backticks is check.sh's markdown-it run
// over the whole corpus (tests/render_facts.js).
const LINK = /\[([^[\]]*)\]\(([^()]*)\)/;
const CODE = /`([^`\n\r]*)`/;
const INLINE = new RegExp(`${LINK.source}|${CODE.source}`, 'g');

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

// The copy keys the generator itself reads, rather than a template naming
// them. They count as called, because they are.
const GENERATOR_KEYS = ['record.thisBuild'];

// A page's name, which is also its URL: lowercase words and hyphens, so
// templates/pages/<name>.html becomes <name>/index.html and the address is the
// one shape every static host serves identically (P6, decided on #14).
const PAGE_NAME = /^[a-z0-9-]+$/;

class BuildError extends Error {}

function fail(message) {
  throw new BuildError(message);
}

function read(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch (error) {
    return fail(`${path.relative(REPO, file)} cannot be read (${error.code || error.message})`);
  }
}

function readJson(file) {
  const text = read(file);
  try {
    return JSON.parse(text);
  } catch (error) {
    return fail(`${path.relative(REPO, file)} is not JSON: ${error.message}`);
  }
}

// ---------------------------------------------------------------------------
// Escaping. Every attribute in a template is double-quoted, so text needs the
// three characters that make markup plus the double quote, and an attribute
// needs the apostrophe as well.
function escapeText(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttribute(value) {
  return escapeText(value).replace(/'/g, '&#39;');
}

// A fact's text, as the page shows it: its two inline forms rendered and
// every other character escaped. So a row in the store carrying a tag, a
// quote or an ampersand reaches the page as those characters and never as
// markup.
function inline(text) {
  let html = '';
  let at = 0;
  for (const found of String(text).matchAll(INLINE)) {
    html += escapeText(String(text).slice(at, found.index));
    if (found[3] === undefined) {
      html += `<a href="${escapeAttribute(found[2])}">${escapeText(found[1])}</a>`;
    } else {
      html += `<span class="num">${escapeText(found[3])}</span>`;
    }
    at = found.index + found[0].length;
  }
  return html + escapeText(String(text).slice(at));
}

// ---------------------------------------------------------------------------
// Dates. A day as the page writes it -- "4 Oct 2026", and "4 Oct" for the
// column label the phone layout draws -- read straight out of the ISO string,
// so no time zone is involved anywhere.
function dateParts(iso, where) {
  const found = ISO_DATE.exec(String(iso));
  const month = found && Number(found[2]);
  if (!found || month < 1 || month > 12) {
    fail(`${where} is not a date: ${JSON.stringify(iso)}`);
  }
  return [Number(found[3]), MONTHS[month - 1], found[1]];
}

function longDate(iso, where) {
  const [day, month, year] = dateParts(iso, where);
  return `${day} ${month} ${year}`;
}

function shortDate(iso, where) {
  const [day, month] = dateParts(iso, where);
  return `${day} ${month}`;
}

// The newest day any of these rows is true as of -- the day the section they
// fill is dated by. With no rows to date it, the day the facts were last
// pulled.
function newestAsOf(rows, pulledAt) {
  if (!Array.isArray(rows)) return pulledAt;
  return rows.reduce((newest, row) => (row.as_of > newest ? row.as_of : newest), '') || pulledAt;
}

// The address as a reader reads it rather than as a browser follows it:
// "todd450.github.io/founding-agents-site". Derived, so content/site.json
// holds the address once and #30 moves it in one place.
function shownAddress(address) {
  return String(address).replace(/^https?:\/\//, '').replace(/\/+$/, '');
}

// ---------------------------------------------------------------------------
// Putting the templates together: notes out, own-line tags tightened onto
// their neighbours, then every include expanded. Expanding first means a
// partial's blocks and values are rendered exactly as if they had been
// written where it is included.
function tidy(template) {
  return template.replace(COMMENT_LINE, '').replace(COMMENT, '').replace(OWN_LINE, '$1');
}

function expand(template, partial, where, seen = []) {
  return template.replace(INCLUDE, (whole, name) => {
    if (seen.includes(name)) {
      return fail(`${where}: {{>${name}}} includes itself (${[...seen, name].join(' -> ')})`);
    }
    return expand(partial(name), partial, where, [...seen, name]);
  });
}

// The body of a block, and what follows it. Counts the blocks inside it, so a
// block nested in another closes the inner one first.
function splitBlock(text, keyword, where) {
  const tags = new RegExp(BLOCK_TAG.source, 'g');
  let depth = 0;
  let found;
  while ((found = tags.exec(text)) !== null) {
    if (found[1].startsWith('#')) {
      depth += 1;
    } else if (depth > 0) {
      depth -= 1;
    } else if (found[1] !== `/${keyword}`) {
      fail(`${where}: {{#${keyword}}} is closed by {{${found[1]}}}`);
    } else {
      return { body: text.slice(0, found.index), after: text.slice(tags.lastIndex) };
    }
  }
  return fail(`${where}: {{#${keyword}}} is never closed`);
}

// Every name a template calls outside its {{#each}} blocks, where the values
// in scope are known before a single fact is read. The names inside them
// depend on the row being rendered, so they are checked as each row renders.
function outsideEach(template, where) {
  let rest = template;
  let out = '';
  for (;;) {
    const open = BLOCK_OPEN.exec(rest);
    if (!open) return out + rest;
    out += rest.slice(0, open.index);
    const { body, after } = splitBlock(rest.slice(open.index + open[0].length), open[1], where);
    rest = (open[1] === 'each' ? '' : body) + after;
  }
}

function namesIn(template) {
  return [...template.matchAll(VALUE)].map((found) => found[2]);
}

function ifNamesIn(template) {
  const names = [];
  const tags = new RegExp(BLOCK_OPEN.source, 'g');
  let found;
  while ((found = tags.exec(template)) !== null) {
    if (found[1] === 'if') names.push(found[2]);
  }
  return names;
}

function eachKindsIn(template) {
  const kinds = [];
  const tags = new RegExp(BLOCK_OPEN.source, 'g');
  let found;
  while ((found = tags.exec(template)) !== null) {
    if (found[1] === 'each') kinds.push(found[2]);
  }
  return kinds;
}

// ---------------------------------------------------------------------------
// Rendering. A scope is the values in hand and the page's copy; resolving a
// name finds a value first and a copy key second, and a copy string may itself
// name values.
function interpolate(text, scope, key) {
  if (NOT_A_VALUE_IN_COPY.test(text)) {
    fail(`the copy string ${key} carries a template tag: a copy string may name a value `
      + '({{site.address}}, {{date}}) and nothing else -- the page\'s shape is the template\'s');
  }
  return text.replace(VALUE, (whole, rich, name) => {
    if (!Object.hasOwn(scope.values, name)) {
      fail(`the copy string ${key} names {{${name}}}, which is not a value the generator works `
        + `out. The values are: ${Object.keys(scope.values).sort().join(', ')}`);
    }
    return scope.values[name];
  });
}

function resolve(name, scope, where) {
  if (Object.hasOwn(scope.values, name)) return scope.values[name];
  if (Object.hasOwn(scope.copy, name)) return interpolate(scope.copy[name], scope, name);
  return fail(`${where} calls {{${name}}}, which is neither a value the generator works out nor `
    + `a key in ${scope.copyFiles}`);
}

function renderValues(template, scope, where) {
  return template.replace(VALUE, (whole, rich, name) => {
    const value = resolve(name, scope, where);
    return rich ? inline(value) : escapeText(value);
  });
}

function render(template, scope, where) {
  const open = BLOCK_OPEN.exec(template);
  if (!open) return renderValues(template, scope, where);
  const before = renderValues(template.slice(0, open.index), scope, where);
  const [, keyword, name] = open;
  const { body, after } = splitBlock(template.slice(open.index + open[0].length), keyword, where);
  let middle = '';
  if (keyword === 'if') {
    if (!Object.hasOwn(scope.values, name)) {
      fail(`${where} asks {{#if ${name}}}, which is not a value the generator works out`);
    }
    middle = scope.values[name] === '' ? '' : render(body, scope, where);
  } else {
    middle = scope.rows(name, where)
      .map((row) => render(body, { ...scope, values: { ...scope.values, ...row } }, where))
      .join('');
  }
  return before + middle + render(after, scope, where);
}

// ---------------------------------------------------------------------------
// The facts, as the templates read them: every row of a kind in position
// order, each carrying its own columns plus the few things the page works out
// from them.
//
// The three kinds the page does more with than print are named here because
// the page does: a step's dot follows its state, so the two cannot disagree; a
// measure's standing is the class its figure is coloured by; and the newest
// record entry is the build the reader is looking at, so the mark is written
// on it and nowhere else -- the page cannot carry two of them.
function rowsOf(snapshot, kind, where, mark) {
  const rows = snapshot[kind];
  if (!Array.isArray(rows)) {
    fail(`${where} asks for {{#each ${kind}}}, and the snapshot holds no such kind of fact. `
      + `It holds: ${Object.keys(snapshot).filter((key) => key !== 'pulled_at').join(', ')}`);
  }
  rows.forEach((row, index) => {
    if (row === null || typeof row !== 'object' || Array.isArray(row)) {
      fail(`${kind}[${index}] is not a row`);
    }
    if (!Number.isInteger(row.position) || row.position < 1) {
      fail(`${kind}[${index}] has no position: ${JSON.stringify(row.position)}`);
    }
  });
  const ordered = [...rows].sort((a, b) => a.position - b.position);
  return ordered.map((row, index) => {
    const at = `${kind}[${row.position - 1}]`;
    const values = {};
    for (const [column, value] of Object.entries(row)) values[column] = String(value);
    values.date = longDate(row.as_of, `${at}.as_of`);
    values.dateShort = shortDate(row.as_of, `${at}.as_of`);
    if (kind === 'status_steps') {
      const done = /^\s*Done\b/.test(values.state);
      values.dot = done ? 'dot-green' : 'dot-open';
      values.stateClass = done ? '' : ' running';
    }
    if (kind === 'measures') {
      values.standingClass = values.standing === '' ? '' : ` ${values.standing}`;
    }
    if (kind === 'record_entries') {
      if (typeof mark !== 'string') {
        fail('no copy key record.thisBuild, which is the mark the record\'s newest entry carries');
      }
      if (index > 0 && values.headline === mark) {
        fail(`${at}.headline is "${mark}", and only the newest entry carries that mark: the page `
          + 'would claim two builds are this one. Move the mark off the older entry.');
      }
      values.mark = index === 0 ? mark : values.headline;
    }
    return values;
  });
}

// ---------------------------------------------------------------------------
/**
 * Builds every page.
 *
 * options.root is where the templates and the content live, options.out where
 * the pages are written, options.snapshot which facts to render, and
 * options.check compares instead of writing.
 *
 * @returns {{pages: string[]}}
 */
function build(options = {}) {
  const root = options.root ? path.resolve(options.root) : REPO;
  const out = options.out ? path.resolve(options.out) : root;
  const snapshotPath = options.snapshot
    ? path.resolve(options.snapshot)
    : path.join(root, 'content', 'facts.json');

  const site = readJson(path.join(root, 'content', 'site.json'));
  for (const field of ['title', 'address']) {
    if (typeof site[field] !== 'string' || site[field].trim() === '') {
      fail(`content/site.json has no ${field}`);
    }
  }
  const snapshot = readJson(snapshotPath);
  if (snapshot === null || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
    fail(`${path.relative(REPO, snapshotPath)} is not a snapshot`);
  }
  // The day the facts were last read, which dates a section that has no row of
  // its own to date it.
  dateParts(snapshot.pulled_at, `${path.relative(REPO, snapshotPath)}: pulled_at`);
  const common = readJson(path.join(root, 'content', 'copy', 'en', 'common.json'));

  const layout = tidy(read(path.join(root, 'templates', 'layout.html')));
  const pagesDir = path.join(root, 'templates', 'pages');
  const templates = fs.readdirSync(pagesDir).filter((name) => name.endsWith('.html')).sort();
  if (templates.length === 0) fail('templates/pages holds no page template at all');

  const siteValues = {
    'site.title': site.title,
    'site.address': site.address,
    'site.shown': shownAddress(site.address),
  };
  const dateOf = (kind) => newestAsOf(snapshot[kind], snapshot.pulled_at);
  const dates = {
    'dates.status': longDate(dateOf('status_steps'), 'the newest status step'),
    'dates.measures': longDate(dateOf('measures'), 'the newest measure'),
    'dates.measuresShort': shortDate(dateOf('measures'), 'the newest measure'),
  };

  const pages = [];
  const stale = [];
  const calledInCommon = new Set();

  for (const file of templates) {
    const name = file.slice(0, -'.html'.length);
    const where = `templates/pages/${file}`;
    if (!PAGE_NAME.test(name)) {
      fail(`${where}: a page's name is also its URL, so it is lowercase words and hyphens `
        + '(templates/pages/record.html is served at /record/)');
    }
    const copyFile = path.join(root, 'content', 'copy', 'en', `${name}.json`);
    if (!fs.existsSync(copyFile)) {
      fail(`${where} has no wording: every page reads content/copy/en/common.json and its own `
        + `content/copy/en/${name}.json, and there is no such file`);
    }
    const own = readJson(copyFile);
    for (const key of Object.keys(own)) {
      if (Object.hasOwn(common, key)) {
        fail(`the key ${key} is defined in both common.json and ${name}.json, so which string `
          + 'the page shows depends on the order they are read');
      }
    }
    const copy = { ...common, ...own };
    const copyFiles = `content/copy/en/common.json or content/copy/en/${name}.json`;

    const partials = (partialName) => {
      if (partialName === 'page') return tidy(read(path.join(pagesDir, file)));
      return tidy(read(path.join(root, 'templates', 'partials', `${partialName}.html`)));
    };
    const template = expand(layout, partials, where);

    // Before a fact is read: every kind an {{#each}} asks for is a kind the
    // snapshot holds, every name called where the values are all known is one
    // of them or a copy key, and every key these files define is called by
    // something. The names inside an {{#each}} belong to the row being
    // rendered, so they are checked as it renders.
    const settled = outsideEach(template, where);
    for (const named of new Set([...namesIn(settled), ...ifNamesIn(settled)])) {
      if (!Object.hasOwn(siteValues, named) && !Object.hasOwn(dates, named)
          && !Object.hasOwn(copy, named)) {
        fail(`${where} calls {{${named}}}, which is neither a value the generator works out nor `
          + `a key in ${copyFiles}`);
      }
    }
    const names = new Set([...namesIn(template), ...ifNamesIn(template), ...GENERATOR_KEYS]);
    for (const key of Object.keys(own)) {
      if (!names.has(key)) {
        fail(`content/copy/en/${name}.json defines ${key}, and nothing on that page shows it: `
          + 'a string nobody reads is a string nobody reviews, so it goes or it is called');
      }
    }
    for (const key of Object.keys(common)) if (names.has(key)) calledInCommon.add(key);

    const scope = {
      values: { ...siteValues, ...dates },
      copy,
      copyFiles,
      rows: (kind, at) => rowsOf(snapshot, kind, at, copy['record.thisBuild']),
    };
    const html = render(template, scope, where);

    const target = name === 'index' ? 'index.html' : path.join(name, 'index.html');
    const pageFile = path.join(out, target);
    if (options.check) {
      let committed = null;
      try {
        committed = fs.readFileSync(pageFile, 'utf8');
      } catch {
        committed = null;
      }
      if (committed !== html) stale.push(target);
    } else {
      fs.mkdirSync(path.dirname(pageFile), { recursive: true });
      fs.writeFileSync(pageFile, html, 'utf8');
    }
    pages.push(target);
  }

  for (const key of Object.keys(common)) {
    if (!calledInCommon.has(key)) {
      fail(`content/copy/en/common.json defines ${key}, and no page shows it: a string nobody `
        + 'reads is a string nobody reviews, so it goes or it is called');
    }
  }

  if (stale.length > 0) {
    fail(`${stale.join(', ')} ${stale.length === 1 ? 'is' : 'are'} not what a fresh build writes. `
      + 'The pages are generated and committed, so they are never edited by hand: run '
      + '`npm run build` and commit the result.');
  }
  return { pages };
}

function main(argv) {
  const options = {};
  for (let at = 0; at < argv.length; at += 1) {
    const flag = argv[at];
    if (flag === '--check') {
      options.check = true;
    } else if (['--root', '--out', '--snapshot'].includes(flag)) {
      at += 1;
      if (at >= argv.length) fail(`${flag} needs a path`);
      options[flag.slice(2)] = argv[at];
    } else {
      fail(`unknown option ${flag}. Usage: generate.js [--check] [--root DIR] [--out DIR] `
        + '[--snapshot PATH]');
    }
  }
  // The pages this generator is responsible for, one per line, so check.sh
  // runs html-validate and stylelint over whatever the site serves today
  // rather than over a list of names it has to keep up to date.
  const { pages } = build(options);
  process.stdout.write(`${pages.join('\n')}\n`);
}

if (require.main === module) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    if (!(error instanceof BuildError)) throw error;
    console.error(`generate: ${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = { build };
