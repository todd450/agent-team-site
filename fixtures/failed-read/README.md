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
| `same-read-twice.json` | the same four reads, run twice -- it must succeed, and twice over it must write byte-identical files, whatever day the second run happens on |
| `same-read-with-one-fact-changed.json` | the same reads with one fact changed: it must succeed, and the snapshot must carry the day the change was found |
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
| `link-with-a-dot-dot-segment.json` | a link that starts with this repo's address and walks out of it on `..` |
| `link-with-backslashes.json` | the same walk on backslashes, which a browser folds into slashes |
| `link-with-escaped-dot-segments.json` | the same walk on `%2e%2e`, with no literal `..` to be seen |
| `link-with-a-control-character.json` | the same walk past a tab and a newline, which a browser strips before it reads the address |
| `pages-link-with-a-dot-dot-segment.json` | the same walk out of the Pages site, onto another site on the same account |
| `link-text-with-brackets.json` | a link whose text holds brackets, which a renderer reads as a link and the reader finds no target in at all |
| `linked-image-link.json` | a link wrapped around an image: the reader sees the image's target and not the one that reaches the href |
| `link-text-with-an-escaped-bracket.json` | the same miss past a backslash-escaped bracket, an escape a renderer honours and the reader cannot see |
| `link-target-with-parentheses.json` | a link whose target holds a balanced pair of parentheses, which a renderer keeps in the address and the reader stops at |
| `bare-url-with-parentheses.json` | the same address written bare, which a renderer autolinks whole |
| `bare-url-with-a-bracket.json` | the same walk written bare past a `]`, which a renderer escapes and keeps in the address |
| `bare-url-on-this-site.json` | a bare address that does land on this site: a fact writes a link as `[text](url)` and in no other form |
| `bare-www-address.json` | a bare `www.` host, which a renderer autolinks with no scheme in the text at all |
| `bare-email-address.json` | a bare email address, which a renderer autolinks as `mailto:` |
| `reference-style-link.json` | a link written as a reference and defined further down, so the reader finds no target to check at all |
| `backticks-across-a-blank-line.json` | a tag between two backticks a blank line apart, which no renderer pairs, so what looks quoted is markup |
| `backticks-across-a-list-item.json` | the same tag with a list item between the backticks: a blank line is not the only thing that ends a paragraph |
| `backticks-in-a-run.json` | a tag after a run of three backticks, which a renderer closes only with another run of three |
| `escaped-backtick.json` | a tag between two backslash-escaped backticks, which are literal characters and not delimiters at all |

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
