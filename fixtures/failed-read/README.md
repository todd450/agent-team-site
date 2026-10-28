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

What makes a row usable is written down in one place, `scripts/pull-facts.js` (decided on #27,
28 Oct 2026). Each scenario here is a class of row it refuses; the suite checks the committed
snapshot with that same definition, through `tests/rules.py`, so a read the puller accepts can
never be a snapshot the checks then reject.

The characters a fact may carry are an allowlist -- letters, marks, numbers, punctuation, symbols
and ordinary spaces, and nothing Unicode calls invisible by default -- rather than a list of the
ones already found to be trouble. The scenarios from `control-character-in-text.json` to
`variation-selector-in-text.json` are all one rule: each is a character a reader of the page and a
reviewer reading the diff both see nothing of, and the list of those was never finished.

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
| `empty-required-column.json` | a step whose `state` is the empty string, which `not null` in the tables does not stop |
| `whitespace-only-required-column.json` | a measure whose `label` is spaces: not empty, and still nothing a reader can see |
| `tab-only-required-column.json` | a record entry of one tab: a control character, outside the characters a fact may carry |
| `control-character-in-text.json` | a step whose `state` is one `U+001F`, which shows a reader nothing while passing for a value |
| `zero-width-character-in-text.json` | a measure whose `label` is zero-width characters: neither empty nor whitespace, and nothing on the page |
| `direction-override-in-text.json` | a fact carrying `U+202E`, which reorders what follows it, so the page and the snapshot say different things |
| `bidi-mark-in-text.json` | the same reordering from a bidirectional mark, `U+200F`, which has no width of its own either |
| `bidi-marks-around-a-figure.json` | a `bar` of `3-5` between two `U+061C` marks, which displays as `5-3` while the diff shows `3-5` |
| `soft-hyphen-in-text.json` | a `unit` carrying `U+00AD`, which draws nothing unless the line happens to break on it |
| `variation-selector-in-text.json` | a headline carrying `U+FE0F`: a mark, which is a category a fact may carry, and invisible by default |
| `as-of-after-the-day-of-the-pull.json` | a good read on a day earlier than the facts are dated: a fact cannot be true as of a day that has not happened |
| `empty-optional-column.json` | the other side of that rule: `unit` and `standing` spelled `''` on purpose, which must still succeed |
| `standing-outside-the-list.json` | a `standing` that is not `hit`, `pending` or `miss`, which the snapshot test would then refuse |
| `standing-in-another-letter-case.json` | the same list in another letter case: `Hit` is not `hit` |
| `markup-in-text.json` | a row with a tag outside backticks, which would put markup on the page |
| `off-site-link.json` | a row linking off this site, which the nightly link check would open `red` on |
| `script-scheme-link.json` | a row whose link points at a `javascript:` scheme rather than at a page |
| `link-with-a-dot-dot-segment.json` | a link that starts with this repo's address and walks out of it on `..` |
| `link-with-backslashes.json` | the same walk on backslashes, refused for the backslash before its links are read at all |
| `link-with-escaped-dot-segments.json` | the same walk on `%2e%2e`, with no literal `..` to be seen |
| `link-with-dot-character-references.json` | the same walk on `&period;&period;`, which a renderer decodes into dots inside the destination |
| `link-with-a-control-character.json` | the same walk past a tab, which a browser strips before it reads the address |
| `pages-link-with-a-dot-dot-segment.json` | the same walk out of the Pages site, onto another site on the same account |
| `link-text-with-brackets.json` | a link whose text holds brackets, which a renderer reads as a link and the reader finds no target in at all |
| `linked-image-link.json` | a link wrapped around an image: the reader sees the image's target and not the one that reaches the href |
| `link-text-with-an-escaped-bracket.json` | the same miss past a backslash-escaped bracket, refused for the backslash before its links are read at all |
| `link-target-with-parentheses.json` | a link whose target holds a balanced pair of parentheses, which a renderer keeps in the address and the reader stops at |
| `bare-url-with-parentheses.json` | the same address written bare, which a renderer autolinks whole |
| `bare-url-with-a-bracket.json` | the same walk written bare past a `]`, which a renderer escapes and keeps in the address |
| `bare-url-on-this-site.json` | a bare address that does land on this site: a fact writes a link as `[text](url)` and in no other form |
| `bare-www-address.json` | a bare `www.` host, which a renderer autolinks with no scheme in the text at all |
| `bare-email-address.json` | a bare email address, which a renderer autolinks as `mailto:` |
| `reference-style-link.json` | a link written as a reference, whose destination is named in a definition elsewhere, so the reader finds no target to check at all |
| `character-reference-in-text.json` | a tag spelled `&#60;img …&#62;`, which a renderer decodes into brackets before anything reads the text |
| `backticks-across-a-blank-line.json` | a tag between two backticks a blank line apart, which no renderer pairs, so what looks quoted is markup |
| `backticks-across-a-list-item.json` | the same tag with a list item between the backticks: a blank line is not the only thing that ends a paragraph |
| `backticks-across-a-carriage-return.json` | the same break written as two lone `\r` characters, which CommonMark counts as line endings too |
| `markup-in-a-table-row.json` | the same tag inside backticks in what a renderer reads as a table row, where a cell boundary splits the span before it forms |
| `pipe-inside-backticks.json` | the same tag after a pipe inside backticks on one line: a cell separator needs no line break to be read first |
| `backticks-in-a-run.json` | a tag after a run of three backticks, which a renderer closes only with another run of three |
| `escaped-backtick.json` | a tag between two backslash-escaped backticks, which are literal characters and not delimiters at all |

A scenario file:

```json
{
  "name": "...",            // what the read does
  "outcome": "fail",        // "fail": exit non-zero, snapshot untouched. "ok": write the snapshot.
  "runs": 1,                // how many times to run the puller
  "today": "2026-10-20",    // optional: the day the pull happens on, for a read whose facts are
                            // dated relative to it. Left out, it is the day the suite runs.
  "fails": "...",           // optional: a fragment of the reason, which must appear on stderr,
                            // so a scenario drifting onto some other rule is a test failure
  "responses": {            // per table; every table not named answers from rows.json
    "costs": { "status": 500, "body": "..." }
  }
}
```

A response is one of: `{"refused": true}`, `{"delay_ms": n}`, `{"status": n, "body": "..."}`,
`{"body": "..."}` (raw, 200), or `{"rows": [...]}` with an optional `"count": n` for the
`content-range` header when it should disagree with the rows.
