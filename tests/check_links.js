#!/usr/bin/env node
//
// Asks the puller's own link rule and link reader about the cases handed to
// it.
//
//   echo '{"links": ["https://example.test/"], "texts": ["[a](b)"]}' \
//     | node tests/check_links.js
//
// Reads a JSON object on stdin with a list of links and a list of texts, and
// writes, in the same order, true/false for each link and
// {"targets": [...], "unreadable": bool} for each text.
//
// tests/test_links.py uses it to run the puller's copies and the Python
// copies over the same cases, so the two cannot drift apart while one of them
// still has a hole.

'use strict';

const fs = require('node:fs');

const { isAllowedLink, linkTargets } = require('../scripts/pull-facts.js');

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
if (cases === null || typeof cases !== 'object' || Array.isArray(cases)) {
  throw new Error('expected a JSON object of {links, texts} on stdin');
}
const links = cases.links || [];
const texts = cases.texts || [];
process.stdout.write(JSON.stringify({
  links: links.map((link) => isAllowedLink(link)),
  texts: texts.map((text) => linkTargets(text)),
}));
