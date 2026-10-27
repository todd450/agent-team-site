#!/usr/bin/env node
//
// Runs scripts/pull-facts.js over one scenario in fixtures/failed-read,
// instead of over Supabase.
//
//   node tests/stub_pull.js <scenario.json> <out.json>
//
// The puller is handed a stubbed fetch built from the scenario, and the real
// fetch is replaced with one that throws, so the suite makes no network call
// and says so loudly if anything tries. Exits 0 when the pull wrote the
// snapshot, non-zero with the reason on stderr when it refused to.
//
// tests/test_facts.py runs every scenario in that folder through here.

'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { pull, KINDS } = require('../scripts/pull-facts.js');

const FIXTURES = path.join(__dirname, '..', 'fixtures', 'failed-read');
// Short, so "a read that never answers" is tested in milliseconds.
const STUB_TIMEOUT_MS = 300;

globalThis.fetch = () => {
  throw new Error('the suite makes no network call: every read is stubbed from the fixtures');
};

function load(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function contentRange(count) {
  return count === 0 ? '*/0' : `0-${count - 1}/${count}`;
}

function answer(status, body, count) {
  return {
    status,
    headers: { get: (name) => (name.toLowerCase() === 'content-range' ? contentRange(count) : null) },
    text: async () => body,
  };
}

function waitThenFail(delayMs, signal) {
  // A read that never answers: it resolves only long after the puller's
  // timeout, so what the puller sees is its own abort.
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(answer(200, '[]', 0)), delayMs);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason);
    }, { once: true });
  });
}

function stubFetch(scenario, rows) {
  return async (url, options) => {
    const table = new URL(url).pathname.split('/').pop();
    const response = (scenario.responses || {})[table] || {};

    if (response.refused) {
      throw Object.assign(new TypeError('fetch failed'), { cause: new Error('ECONNREFUSED') });
    }
    if (response.delay_ms) {
      return waitThenFail(response.delay_ms, options.signal);
    }

    const tableRows = response.rows || rows[table];
    if (tableRows === undefined) throw new Error(`no stubbed rows for ${table}`);
    const count = response.count === undefined ? tableRows.length : response.count;
    const body = response.body === undefined ? JSON.stringify(tableRows) : response.body;
    return answer(response.status || 200, body, count);
  };
}

async function main() {
  const [scenarioPath, out] = process.argv.slice(2);
  if (!scenarioPath || !out) {
    throw new Error('usage: node tests/stub_pull.js <scenario.json> <out.json>');
  }
  const scenario = load(scenarioPath);
  const rows = load(path.join(FIXTURES, 'rows.json'));
  for (const kind of KINDS) {
    if (!Array.isArray(rows[kind.table])) {
      throw new Error(`fixtures/failed-read/rows.json has no rows for ${kind.table}`);
    }
  }
  await pull({
    fetchImpl: stubFetch(scenario, rows),
    baseUrl: 'https://stub.invalid',
    key: 'sb_publishable_stub',
    timeoutMs: STUB_TIMEOUT_MS,
    out,
  });
}

main().catch((error) => {
  console.error(`stub_pull: ${error.message}`);
  process.exitCode = 1;
});
