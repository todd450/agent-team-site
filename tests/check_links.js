#!/usr/bin/env node
//
// Asks the puller's own link rule about each link handed to it.
//
//   echo '["https://example.test/"]' | node tests/check_links.js
//
// Reads a JSON list of links on stdin and writes a JSON list of true/false in
// the same order. tests/test_links.py uses it to run the puller's copy of the
// rule and the Python copy over the same cases, so the two cannot drift apart
// while one of them still has a hole.

'use strict';

const fs = require('node:fs');

const { isAllowedLink } = require('../scripts/pull-facts.js');

const links = JSON.parse(fs.readFileSync(0, 'utf8'));
if (!Array.isArray(links)) throw new Error('expected a JSON list of links on stdin');
process.stdout.write(JSON.stringify(links.map((link) => isAllowedLink(link))));
