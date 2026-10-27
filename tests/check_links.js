#!/usr/bin/env node
//
// Asks the puller's own rules for a fact's text -- the link rule, the link
// reader and the code-span reader -- about the cases handed to it.
//
//   echo '{"links": ["https://example.test/"], "texts": ["[a](b)"],
//          "markup": ["a `<li>` here"]}' | node tests/check_links.js
//
// Reads a JSON object on stdin with a list of links, a list of texts and a
// list of texts to read the markup of, and writes, in the same order,
// true/false for each link, {"targets": [...], "unreadable": bool} for each
// text, and the brackets reported for each markup case.
//
// tests/test_links.py uses it to run the puller's copies and the Python
// copies over the same cases, so the two cannot drift apart while one of them
// still has a hole.

'use strict';

const fs = require('node:fs');

const { isAllowedLink, linkTargets, markupOutsideBackticks } = require('../scripts/pull-facts.js');

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
if (cases === null || typeof cases !== 'object' || Array.isArray(cases)) {
  throw new Error('expected a JSON object of {links, texts, markup} on stdin');
}
const links = cases.links || [];
const texts = cases.texts || [];
const markup = cases.markup || [];
process.stdout.write(JSON.stringify({
  links: links.map((link) => isAllowedLink(link)),
  texts: texts.map((text) => linkTargets(text)),
  markup: markup.map((text) => markupOutsideBackticks(text)),
}));
