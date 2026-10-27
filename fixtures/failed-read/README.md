# failed-read

A read that fails, is slow, or happens twice -- here, the build pulling the four fact tables out of
Supabase with `scripts/pull-facts.js`.

The one rule every scenario tests: **the puller writes `content/facts.json` only when all four
reads came back whole and usable.** Anything else leaves the snapshot exactly as it was and exits
non-zero, so a Supabase outage, a half-sent answer or a bad row can never half-write the snapshot,
freeze a merge, or put something on the page that was never reviewed.

`rows.json` holds one good read of all four tables. Each scenario file names only what it changes;
every other table answers with those rows. `tests/test_facts.py` runs them through
`tests/stub_pull.js`, which hands the puller a stubbed `fetch` and makes the real one throw, so the
suite never touches the network.

| Scenario | The read |
| --- | --- |
| `same-read-twice.json` | the same four reads, run twice -- the only one that must succeed, and twice over it must write byte-identical files |
| `refused-connection.json` | the connection is refused: the project is paused, or there is no network |
| `slow-read.json` | the read never answers, and must time out rather than hang the build |
| `server-error.json` | a 500 from the API |
| `not-found.json` | a 404: the table was renamed or the key may no longer read it |
| `unauthorized.json` | a 401: the key was rotated or revoked |
| `truncated-body.json` | 200, but the body is cut off mid-JSON |
| `partial-rows.json` | 200 and valid JSON, but fewer rows than the count header promises |
| `empty-table.json` | 200 and an empty table, which would silently empty a section of the page |
| `gap-in-positions.json` | positions 1 and 3: a row is missing, so the page's order has a hole |
| `missing-column.json` | a row without its `as_of`, so a fact arrives with no date |
| `markup-in-text.json` | a row with a tag outside backticks, which would put markup on the page |
| `off-site-link.json` | a row linking off this site, which the nightly link check would open `red` on |
| `script-scheme-link.json` | a row whose link points at a `javascript:` scheme rather than at a page |

A scenario file:

```json
{
  "name": "...",            // what the read does
  "outcome": "fail",        // "fail": exit non-zero, snapshot untouched. "ok": write the snapshot.
  "runs": 1,                // how many times to run the puller
  "responses": {            // per table; every table not named answers from rows.json
    "costs": { "status": 500, "body": "..." }
  }
}
```

A response is one of: `{"refused": true}`, `{"delay_ms": n}`, `{"status": n, "body": "..."}`,
`{"body": "..."}` (raw, 200), or `{"rows": [...]}` with an optional `"count": n` for the
`content-range` header when it should disagree with the rows.
