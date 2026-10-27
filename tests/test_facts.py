"""Tests for the facts snapshot and the puller that writes it (issue #27).

The page's facts live in four Supabase tables, read by the build only.
`content/facts.json` is the reviewed snapshot of those tables in the repo, and
it is the only thing the build ever reads, so check.sh, CI and every deploy
stay offline and deterministic and a Supabase outage can never freeze a merge.

Two halves:

- The snapshot itself: all four kinds, every row dated and in place, no markup
  in any text column, and no link off this site.
- The puller, `scripts/pull-facts.js`: a read that is refused, slow, short or
  carrying an unusable row leaves the snapshot exactly as it was and exits
  non-zero. Every scenario in fixtures/failed-read is run, so a scenario added
  to that folder is run here without this file changing.

No network: tests/stub_pull.js hands the puller a stubbed fetch and makes the
real one throw.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

# The one Python copy of "where a link may point", shared with the page's own
# link tests in tests/test_shell.py and checked against the puller's copy in
# tests/test_links.py.
from links import allowed_link

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
SNAPSHOT = ROOT / "content" / "facts.json"
PULLER = ROOT / "scripts" / "pull-facts.js"
SCHEMA_SQL = ROOT / "supabase" / "schema.sql"
SEED_SQL = ROOT / "supabase" / "seed.sql"
# Every change the live project has already had, oldest first.
MIGRATIONS = ROOT / "supabase" / "migrations"
# Every statement this repo would run on the project, wherever it lives.
SUPABASE_SQL = sorted((ROOT / "supabase").glob("**/*.sql"))
FAILED_READ = ROOT / "fixtures" / "failed-read"
STUB_PULL = Path(__file__).resolve().parent / "stub_pull.js"

# The four kinds, in the order the snapshot holds them, and the columns each
# row carries. This is the shape the generator is written against.
KINDS = {
    "status_steps": ["position", "as_of", "name", "state", "detail"],
    # A cost's figure is the number alone and its unit the small tail beside
    # it ("61,365" and "lines, in 8 days"), because that is how the page shows
    # them -- two elements, two sizes. Folded into one string they could not
    # be told apart again without markup in the text, which no fact carries.
    "costs": ["position", "as_of", "figure", "unit", "lead", "claim", "source"],
    "measures": ["position", "as_of", "label", "bar", "current", "standing"],
    "record_entries": ["position", "as_of", "headline", "entry"],
}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s)\]]+|(?<![\w/])//[^\s)\]]+")
# What a [text](url) link points at, whatever its scheme: a target of
# "javascript:..." or a relative path carries no "://" for URL_RE to find.
LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
CODE_SPAN_RE = re.compile(r"`[^`]*`")
STANDINGS = {"hit", "pending", "miss", ""}
ADD_COLUMN_RE = re.compile(r"alter table (\w+) add column (\w+)")


def markup_outside_backticks(value):
    """The brackets this text would put on the page as markup. A fact is text:
    a link is [text](url) and a literal value is `code`, and the generator
    renders those two and escapes everything else, so a < or > inside
    backticks is safe and one anywhere else is not."""
    if value.count("`") % 2 != 0:
        return ["`"]  # an unclosed backtick leaves what is quoted unclear
    outside = CODE_SPAN_RE.sub("", value)
    return [bracket for bracket in ("<", ">") if bracket in outside]


def links_outside_allowlist(value):
    """Everything in this text that would reach the page as a link: what each
    [text](url) points at, and any bare URL alongside them."""
    targets = LINK_TARGET_RE.findall(value) + URL_RE.findall(value)
    return [target for target in targets if not allowed_link(target)]


EV_BLOCK_RE = re.compile(r'<div class="ev">(.*?)</div>', re.DOTALL)
EV_FIGURE_RE = re.compile(r'<span class="ev-figure">(.*?)</span>', re.DOTALL)
EV_UNIT_RE = re.compile(r'<span class="ev-unit">(.*?)</span>', re.DOTALL)


def flat(text):
    """One line of text, however the page wrapped it."""
    return " ".join(text.split())


def costs_on_the_page(html):
    """The figure and the unit of each cost index.html shows today, in the
    order it shows them. The page renders the two as separate elements at
    separate sizes, so the snapshot has to carry them as separate values."""
    costs = []
    for block in EV_BLOCK_RE.findall(html):
        unit = EV_UNIT_RE.search(block)
        figure = EV_FIGURE_RE.search(EV_UNIT_RE.sub("", block))
        if figure is None:
            continue
        costs.append({"figure": flat(figure.group(1)),
                      "unit": flat(unit.group(1)) if unit else ""})
    return costs


# A comment or a string, whichever comes first: an apostrophe inside a comment
# ("#27's tables") begins no string, and reading the file left to right is what
# tells the two apart.
SQL_COMMENT_OR_STRING_RE = re.compile(r"--[^\n]*|'((?:[^']|'')*)'")


def sql_literals(block):
    """Every single-quoted string in a chunk of SQL, with '' unescaped."""
    return [match.group(1).replace("''", "'")
            for match in SQL_COMMENT_OR_STRING_RE.finditer(block)
            if match.group(1) is not None]


def parse_iso_date(value):
    """The date this fact is true as of, or None if it is not a date."""
    if not isinstance(value, str) or not ISO_DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class SnapshotRulesMixin:
    """The rules the snapshot must satisfy, however it was produced -- the one
    in the repo, and any the puller writes during these tests."""

    def assert_snapshot_is_sound(self, snapshot):
        self.assertIsInstance(snapshot, dict)
        self.assertIsNotNone(parse_iso_date(snapshot.get("pulled_at")),
                             f"pulled_at is not a date: {snapshot.get('pulled_at')!r}")
        for kind, columns in KINDS.items():
            with self.subTest(kind=kind):
                rows = snapshot.get(kind)
                self.assertIsInstance(rows, list, f"{kind} is missing from the snapshot")
                self.assertTrue(rows, f"{kind} has no rows")
                self.assert_rows_are_sound(kind, rows, columns)

    def assert_rows_are_sound(self, kind, rows, columns):
        positions = []
        for index, row in enumerate(rows):
            with self.subTest(kind=kind, row=index):
                self.assertEqual(list(row.keys()), columns,
                                 f"{kind} row {index} does not carry exactly its columns")
                self.assertIsInstance(row["position"], int)
                self.assertNotIsInstance(row["position"], bool)
                positions.append(row["position"])
                self.assertIsNotNone(parse_iso_date(row["as_of"]),
                                     f"{kind} row {index} has no as_of date: {row['as_of']!r}")
                for column in columns[2:]:
                    self.assertIsInstance(row[column], str,
                                          f"{kind}.{column} is not text: {row[column]!r}")
        self.assertEqual(positions, list(range(1, len(rows) + 1)),
                         f"{kind} positions are not 1..n in order, with no gap and no repeat: "
                         f"{positions!r}")

    def assert_no_markup(self, snapshot):
        for kind, columns in KINDS.items():
            for row in snapshot[kind]:
                for column in columns[2:]:
                    with self.subTest(kind=kind, position=row["position"], column=column):
                        self.assertEqual(
                            markup_outside_backticks(row[column]), [],
                            f"{kind}.{column} could put markup on the page: {row[column]!r}")

    def assert_links_are_allowed(self, snapshot):
        for kind, columns in KINDS.items():
            for row in snapshot[kind]:
                for column in columns[2:]:
                    with self.subTest(kind=kind, position=row["position"], column=column):
                        self.assertEqual(
                            links_outside_allowlist(row[column]), [],
                            f"{kind}.{column} links off this site: {row[column]!r}")


class SnapshotTests(unittest.TestCase, SnapshotRulesMixin):
    """The snapshot committed in the repo -- the only facts the build reads."""

    def setUp(self):
        self.assertTrue(SNAPSHOT.is_file(), f"no snapshot at {SNAPSHOT}")
        self.snapshot = load(SNAPSHOT)

    def test_all_four_kinds_dated_and_in_place(self):
        self.assert_snapshot_is_sound(self.snapshot)

    def test_no_text_column_carries_markup_outside_backticks(self):
        self.assert_no_markup(self.snapshot)

    def test_every_url_in_a_fact_is_in_the_page_s_allowlist(self):
        self.assert_links_are_allowed(self.snapshot)

    def test_each_cost_the_page_shows_keeps_its_figure_and_its_unit_apart(self):
        # The snapshot is what the page will be generated from, so whatever
        # the page shows today it has to be able to say. Two of the four
        # costs are a big number with a small tail beside it; a snapshot that
        # folded "61,365" and "lines, in 8 days" into one string could not
        # put that tail back without markup, and facts carry no markup.
        shown = costs_on_the_page(INDEX_HTML.read_text(encoding="utf-8"))
        self.assertTrue(shown, "no costs found on the page")
        self.assertEqual(len(shown), len(self.snapshot["costs"]),
                         "the page and the snapshot do not show the same number of costs")
        for cost, row in zip(shown, self.snapshot["costs"]):
            with self.subTest(position=row["position"]):
                self.assertEqual(row["figure"], cost["figure"])
                self.assertEqual(row["unit"], cost["unit"])

    def test_a_measure_s_standing_is_one_of_the_four_values(self):
        for row in self.snapshot["measures"]:
            with self.subTest(position=row["position"]):
                self.assertIn(row["standing"], STANDINGS)

    def test_the_snapshot_holds_the_four_kinds_and_nothing_else(self):
        self.assertEqual(list(self.snapshot.keys()), ["pulled_at"] + list(KINDS),
                         "the snapshot's keys are not pulled_at then the four kinds, in order")

    def test_the_file_ends_in_one_newline(self):
        text = SNAPSHOT.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))


class SchemaAndSeedTests(unittest.TestCase):
    """The tables in Supabase, the seed that filled them and the changes the
    live project has had since, against the shape the snapshot and the
    generator are written to. The seed is a starting state, not a mirror of
    the tables as they are now -- a later pull may carry facts it never had --
    so what is checked here is that running this folder on a fresh project
    gives tables this page's rules allow, and that a column the live project
    gained is one a fresh project would get too."""

    def setUp(self):
        self.schema = SCHEMA_SQL.read_text(encoding="utf-8")
        self.seed = SEED_SQL.read_text(encoding="utf-8")

    def _schema_columns(self, table):
        match = re.search(rf"create table {table} \((.*?)\n\);", self.schema, re.DOTALL)
        self.assertIsNotNone(match, f"schema.sql does not create {table}")
        return [line.strip().split()[0]
                for line in match.group(1).strip().splitlines() if line.strip()]

    def _seed_block(self, table):
        # Up to the next insert, not the next semicolon: a semicolon inside a
        # fact's own prose would cut the block short.
        match = re.search(rf"insert into {table} \(([^)]*)\) values(.*?)(?=\ninsert into |\Z)",
                          self.seed, re.DOTALL)
        self.assertIsNotNone(match, f"seed.sql does not insert into {table}")
        return [name.strip() for name in match.group(1).split(",")], match.group(2)

    def test_each_table_has_the_columns_the_snapshot_carries(self):
        for table, columns in KINDS.items():
            with self.subTest(table=table):
                self.assertEqual(self._schema_columns(table), columns)
                seed_columns, _ = self._seed_block(table)
                self.assertEqual(seed_columns, columns)

    def test_the_seed_fills_every_table_in_position_order(self):
        for table in KINDS:
            with self.subTest(table=table):
                _, values = self._seed_block(table)
                seeded = [int(n) for n in re.findall(r"^\s*\(\s*(\d+),", values, re.MULTILINE)]
                self.assertTrue(seeded, f"seed.sql seeds no rows into {table}")
                self.assertEqual(seeded, list(range(1, len(seeded) + 1)),
                                 f"{table} is seeded with a gap or a repeat in its positions")

    def test_no_fact_this_repo_writes_into_the_tables_carries_markup_or_an_off_site_link(self):
        # Every row this repo would put in the tables -- the seed's, and any a
        # change in supabase/migrations wrote since -- has to be a row the
        # puller would accept, or a pull fails from the start.
        for path in sorted(SUPABASE_SQL):
            for literal in sql_literals(path.read_text(encoding="utf-8")):
                with self.subTest(file=path.name, value=literal[:40]):
                    self.assertEqual(markup_outside_backticks(literal), [],
                                     f"a fact in {path.name} could put markup on the page: "
                                     f"{literal!r}")
                    self.assertEqual(links_outside_allowlist(literal), [],
                                     f"a fact in {path.name} links off this site: {literal!r}")

    def test_every_column_a_change_added_to_the_live_tables_is_in_the_schema(self):
        # schema.sql is the shape a fresh project gets and migrations/ is what
        # the live one has had done to it. A column added to the live tables
        # and missing from schema.sql is a project built from this repo that
        # cannot hold the facts the snapshot carries -- and the puller asking
        # for a column that is not there is a pull that fails on its only job.
        for path in sorted(MIGRATIONS.glob("*.sql")):
            for table, column in ADD_COLUMN_RE.findall(path.read_text(encoding="utf-8")):
                with self.subTest(file=path.name, column=f"{table}.{column}"):
                    self.assertIn(column, self._schema_columns(table),
                                  f"{path.name} added {table}.{column} to the live project, "
                                  f"and schema.sql does not create it")

    def test_row_level_security_is_on_and_the_anon_role_may_only_read(self):
        self.assertIn("enable row level security", self.schema)
        self.assertRegex(self.schema, r"create policy[^;]*for select to anon")
        self.assertRegex(self.schema, r"revoke insert, update, delete, truncate")
        for forbidden in ("for insert", "for update", "for delete", "for all"):
            self.assertNotIn(forbidden, self.schema,
                             f"the anon role must not be given {forbidden!r}")


class PullerSourceTests(unittest.TestCase):
    """The puller is Node and its standard library, and carries no secret."""

    def setUp(self):
        self.assertTrue(PULLER.is_file(), f"no puller at {PULLER}")
        self.source = PULLER.read_text(encoding="utf-8")

    def test_the_project_url_and_publishable_key_are_named_constants(self):
        url = re.search(r"^const SUPABASE_URL = '(\S+)';$", self.source, re.MULTILINE)
        key = re.search(r"^const SUPABASE_PUBLISHABLE_KEY = '(\S+)';$", self.source, re.MULTILINE)
        self.assertIsNotNone(url, "no SUPABASE_URL constant at the top of the puller")
        self.assertIsNotNone(key, "no SUPABASE_PUBLISHABLE_KEY constant at the top of the puller")
        self.assertTrue(url.group(1).startswith("https://"))
        self.assertTrue(key.group(1).startswith("sb_publishable_"),
                        "the committed key must be the publishable, read-only one")

    def test_no_service_role_key_is_stored(self):
        for secret in ("service_role", "sb_secret_", "SUPABASE_SERVICE"):
            self.assertNotIn(secret, self.source)
        # A legacy Supabase key is a JWT, which starts like this.
        self.assertNotRegex(self.source, r"eyJ[A-Za-z0-9_-]{10,}")

    def test_it_imports_only_node_s_standard_library(self):
        imports = re.findall(r"require\('([^']+)'\)", self.source)
        self.assertTrue(imports, "expected the puller to require something")
        for name in imports:
            with self.subTest(module=name):
                self.assertTrue(name.startswith("node:"),
                                f"the puller must use Node's standard library only: {name!r}")


def scenarios():
    return sorted(path for path in FAILED_READ.glob("*.json") if path.name != "rows.json")


class PullerTests(unittest.TestCase, SnapshotRulesMixin):
    """Every scenario in fixtures/failed-read, run through the puller with its
    reads stubbed."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if cls.node is None:  # pragma: no cover - CI and check.sh both have Node
            raise unittest.SkipTest("node is not installed")

    def tmpdir(self):
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_puller(self, scenario, out, today=None):
        command = [self.node, str(STUB_PULL), str(scenario), str(out)]
        if today is not None:
            command.append(today)
        return subprocess.run(command, capture_output=True, text=True, timeout=60, cwd=str(ROOT))

    def test_fixtures_cover_both_a_good_read_and_failing_ones(self):
        outcomes = [load(path)["outcome"] for path in scenarios()]
        self.assertIn("ok", outcomes)
        self.assertIn("fail", outcomes)

    def test_a_good_read_writes_a_sound_snapshot(self):
        out = self.tmpdir() / "facts.json"
        for path in scenarios():
            if load(path)["outcome"] != "ok":
                continue
            with self.subTest(scenario=path.name):
                out.unlink(missing_ok=True)
                result = self.run_puller(path, out)
                self.assertEqual(result.returncode, 0, result.stderr)
                snapshot = load(out)
                self.assert_snapshot_is_sound(snapshot)
                self.assert_no_markup(snapshot)
                self.assert_links_are_allowed(snapshot)

    def test_the_same_read_twice_writes_byte_identical_files(self):
        out = self.tmpdir() / "facts.json"
        for path in scenarios():
            scenario = load(path)
            if scenario["outcome"] != "ok" or scenario.get("runs", 1) < 2:
                continue
            with self.subTest(scenario=path.name):
                out.unlink(missing_ok=True)
                written = []
                for _ in range(scenario["runs"]):
                    result = self.run_puller(path, out)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    written.append(out.read_bytes())
                self.assertEqual(len(set(written)), 1,
                                 "a pull with nothing new in it must produce an empty diff")

    def test_pulled_at_moves_only_on_the_day_a_fact_changed(self):
        # The stamp says when these facts last moved. A pull weeks later that
        # finds the same rows writes the same bytes -- an empty diff, not a
        # date change to review -- and the first pull that does find
        # something new carries the day it found it.
        out = self.tmpdir() / "facts.json"
        same = FAILED_READ / "same-read-twice.json"
        changed = FAILED_READ / "same-read-with-one-fact-changed.json"

        self.assertEqual(self.run_puller(same, out, today="2026-03-01").returncode, 0)
        first = out.read_bytes()
        self.assertEqual(load(out)["pulled_at"], "2026-03-01")

        self.assertEqual(self.run_puller(same, out, today="2026-04-02").returncode, 0)
        self.assertEqual(out.read_bytes(), first,
                         "a pull that found nothing new must leave the file byte for byte")

        self.assertEqual(self.run_puller(changed, out, today="2026-04-02").returncode, 0)
        self.assertEqual(load(out)["pulled_at"], "2026-04-02",
                         "a pull that found a changed fact must carry that day")

    def test_a_bad_read_leaves_an_existing_snapshot_untouched(self):
        tmp = self.tmpdir()
        out = tmp / "facts.json"
        before = SNAPSHOT.read_bytes()
        for path in scenarios():
            if load(path)["outcome"] != "fail":
                continue
            with self.subTest(scenario=path.name):
                out.write_bytes(before)
                result = self.run_puller(path, out)
                self.assertNotEqual(result.returncode, 0,
                                    f"{path.name} should have failed the pull")
                self.assertEqual(out.read_bytes(), before,
                                 "a failed pull must leave the snapshot exactly as it was")
                self.assertTrue(result.stderr.strip(), "a failed pull must say why")
                self.assertEqual(list(tmp.iterdir()), [out],
                                 "a failed pull left a temporary file behind")

    def test_a_bad_read_writes_no_snapshot_at_all_when_there_is_none(self):
        tmp = self.tmpdir()
        out = tmp / "facts.json"
        for path in scenarios():
            if load(path)["outcome"] != "fail":
                continue
            with self.subTest(scenario=path.name):
                result = self.run_puller(path, out)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(out.exists(), "a failed pull must not half-write a snapshot")
                self.assertEqual(list(tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
