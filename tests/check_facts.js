#!/usr/bin/env node
//
// Asks scripts/pull-facts.js -- the one definition of a valid fact value
// (decided on #27, 28 Oct 2026) -- about the cases handed to it, so the
// Python suite can check the page and the snapshot by the same rules a pull
// enforces instead of holding a second copy of them.
//
//   echo '{"snapshot": "content/facts.json"}' | node tests/check_facts.js
//   echo '{"links": ["https://example.test/"], "texts": ["[a](b)"],
//          "markup": ["a `<li>` here"], "characters": ["a b"]}' \
//     | node tests/check_facts.js
//
// Reads a JSON object on stdin:
//
//   snapshot   a path to a snapshot to check whole, or a snapshot itself
//   today      the day to check it as of, which no date in it may be after
//   links      links to judge against the allowlist
//   texts      texts to find the links in
//   markup      texts to read the markup of
//   characters  texts to list the refused characters of
//
// and writes one, in the same order:
//
//   kinds       the four kinds and their columns, as the puller defines them
//   prefixes    the two addresses a link in this repo may point inside
//   day         the day a pull run here and now would reckon it is
//   snapshot    why the snapshot is not sound, or [] when it is
//   links       true or false for each link
//   texts       {"targets": [...], "unreadable": bool} for each text
//   markup      the characters reported for each markup case
//   characters  the code points the allowlist refuses in each case
//
// tests/rules.py is the only thing that runs this, and the only way the
// Python suite asks any of these questions.

'use strict';

const fs = require('node:fs');

const {
  checkSnapshot, isAllowedLink, linkTargets, markupOutsideBackticks, refusedCharacters, today,
  ALLOWED_LINK_PREFIXES, KINDS,
} = require('../scripts/pull-facts.js');

// Nothing here reads Supabase -- the puller is asked about values, never run
// -- and the real fetch is replaced so that saying so is not a promise but a
// fact, as tests/stub_pull.js does.
globalThis.fetch = () => {
  throw new Error('the suite makes no network call: the puller is asked about values only');
};

const asked = JSON.parse(fs.readFileSync(0, 'utf8'));
if (asked === null || typeof asked !== 'object' || Array.isArray(asked)) {
  throw new Error('expected a JSON object of questions on stdin');
}

// Why this snapshot is not one the build may read, or nothing at all. One
// reason, not a list: the first thing wrong with a snapshot is the thing to
// fix, and a pull refuses on it too.
function snapshotProblems(snapshot, today) {
  if (snapshot === undefined) return [];
  const facts = typeof snapshot === 'string'
    ? JSON.parse(fs.readFileSync(snapshot, 'utf8'))
    : snapshot;
  try {
    checkSnapshot(facts, { today });
  } catch (error) {
    return [error.message];
  }
  return [];
}

process.stdout.write(JSON.stringify({
  kinds: KINDS,
  prefixes: ALLOWED_LINK_PREFIXES,
  // The day a pull would stamp a snapshot with, and the day no fact may be
  // dated after, as reckoned in this process's own time zone.
  day: today(),
  snapshot: snapshotProblems(asked.snapshot, asked.today),
  links: (asked.links || []).map((link) => isAllowedLink(link)),
  texts: (asked.texts || []).map((text) => linkTargets(text)),
  markup: (asked.markup || []).map((text) => markupOutsideBackticks(text)),
  characters: (asked.characters || []).map((text) => refusedCharacters(text)),
}));
