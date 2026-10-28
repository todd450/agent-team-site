# empty-and-one

No items, one item, and the most there can be -- here, the facts snapshot the generator renders the
site's pages from (`scripts/generate.js`, issue #28).

The one rule every scenario tests: **the generator renders a valid page from any number of rows,
including none.** Every list on the page grows or empties on its own -- the record gains an entry
with every merge, a build step is closed, a measure is dropped -- so a page that only holds together
at today's counts is a page that breaks on the next pull.

Each file is a whole snapshot, in the shape `content/facts.json` has: `pulled_at`, then the four
kinds of fact in order. `tests/test_generate.py` renders every one of them, so a scenario added to
this folder is rendered without that file changing, and `check.sh` runs html-validate over each
rendered page.

| Scenario | The rows |
| --- | --- |
| `no-rows.json` | every kind empty: no steps, no costs, no measures and no record entries, so each section draws its own empty state and the sections dated by their newest row fall back to the day the facts were pulled |
| `one-row-of-each-kind.json` | one row of each kind -- the single row is also the newest, so it is the one the record's "this build" mark goes on, and its cost carries a unit so the small tail beside the figure is drawn |
| `the-most-there-can-be.json` | forty record entries and twelve of every other kind, far more than the page has ever carried, with a running step as well as done ones, every standing a measure may have, and costs with and without a unit |

There is no cap on any of these lists: a Postgres table has none and nobody writing a record entry
counts them. So "the most there can be" is "far more than the page has ever shown at once", which
is what a list with no limit can be tested against.

`no-rows.json` is not a snapshot a pull would ever write -- `scripts/pull-facts.js` refuses a read
that comes back with an empty table, because that would silently empty a section of the page. It is
a snapshot the generator still has to render, because the generator is also what renders a page
whose newest section has nothing in it yet, and because a template that assumes a first row is a
template that crashes the build the day one is dropped.
