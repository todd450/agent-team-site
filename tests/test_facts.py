"""Tests for the facts snapshot and the puller that writes it (issue #27).

The page's facts live in four Supabase tables, read by the build only.
`content/facts.json` is the reviewed snapshot of those tables in the repo, and
it is the only thing the build ever reads, so check.sh, CI and every deploy
stay offline and deterministic and a Supabase outage can never freeze a merge.

Two halves:

- The snapshot itself: all four kinds, every row dated and in place, no markup
  in any text column, no character a reader cannot see, and no link off this
  site.
- The puller, `scripts/pull-facts.js`: a read that is refused, slow, short or
  carrying an unusable row leaves the snapshot exactly as it was and exits
  non-zero. Every scenario in fixtures/failed-read is run, so a scenario added
  to that folder is run here without this file changing.

What a valid fact value is, this file does not say: the puller is the one
definition of it (decided on #27, 28 Oct 2026) and `tests/rules.py` asks it
through `node`. So the rules a pull enforces and the rules check.sh holds the
committed snapshot to are the same rules, and no pull can write a file the
suite then rejects.

No network: tests/stub_pull.js hands the puller a stubbed fetch and makes the
real one throw.
"""

import datetime
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# The puller's own rules, asked through node. Nothing in this file reimplements
# one.
from rules import (allowed_links, day_of_the_pull, kinds, links_in, markup_in,
                   refused_in, snapshot_problems)

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
RULES_PY = Path(__file__).resolve().parent / "rules.py"

# The four kinds, in the order the snapshot holds them, and the columns each
# row carries -- as the puller defines them, which is the shape the generator
# is written against. A cost's figure is the number alone and its unit the
# small tail beside it ("61,365" and "lines, in 8 days"), because that is how
# the page shows them: two elements, two sizes. Folded into one string they
# could not be told apart again without markup in the text, which no fact
# carries.
KINDS = kinds()

ADD_COLUMN_RE = re.compile(r"alter table (\w+) add column (\w+)")
# The working file write() moves over the snapshot, named for the process that
# wrote it. A run killed between the write and the rename leaves one behind.
WORKING_FILE = "facts.json.4242.tmp"
# A neighbour in the same directory whose name is close to a working file's and
# is not one: clearing the leftovers must not reach it.
NEIGHBOUR = "facts.json.bak"


def unchecked_links(values):
    """For each of these texts, every link in it that nothing can vouch for:
    what each [text](url) points at, when it lands off this site -- and the
    text itself when it writes a link in any other form, a bare address
    included, because then the destination a renderer would use is one no
    allowlist ever saw.

    Both questions go to the puller, in one call for all the values.
    """
    read = links_in(values)
    verdicts = iter(allowed_links([target for targets, _ in read for target in targets]))
    unchecked = []
    for targets, unreadable in read:
        # One verdict per target, in the order they were handed over.
        off_site = [target for target in targets if not next(verdicts)]
        unchecked.append((["a link written in a form the reader does not read"]
                          if unreadable else []) + off_site)
    return unchecked


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


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def day_after(day):
    """The next day on the calendar. A date in a snapshot is a day, so a test
    that needs the day after one says so rather than hard-coding a second
    date that drifts from the first."""
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()


def text_values(snapshot):
    """Every text value in a snapshot, with where it came from: one list, so
    the puller is asked about all of them in one call."""
    return [(f"{kind}[{index}].{column}", row[column])
            for kind, columns in KINDS.items()
            for index, row in enumerate(snapshot[kind])
            for column in columns[2:]]


class SnapshotRulesMixin:
    """The rules a snapshot must satisfy, however it was produced -- the one
    in the repo, and any the puller writes during these tests -- asked of the
    puller, which is the only place they are written down."""

    def assert_snapshot_is_sound(self, snapshot, today=None):
        self.assertEqual(snapshot_problems(snapshot, today), [],
                         "the puller's own rules reject this snapshot")

    def assert_no_markup(self, snapshot):
        where, values = zip(*text_values(snapshot))
        for place, value, reported in zip(where, values, markup_in(values)):
            with self.subTest(value=place):
                self.assertEqual(reported, [],
                                 f"{place} could put markup on the page: {value!r}")

    def assert_links_are_allowed(self, snapshot):
        where, values = zip(*text_values(snapshot))
        for place, value, unchecked in zip(where, values, unchecked_links(values)):
            with self.subTest(value=place):
                self.assertEqual(unchecked, [],
                                 f"{place} carries a link nothing can vouch for: {value!r}")


class SnapshotTests(unittest.TestCase, SnapshotRulesMixin):
    """The snapshot committed in the repo -- the only facts the build reads."""

    def setUp(self):
        self.assertTrue(SNAPSHOT.is_file(), f"no snapshot at {SNAPSHOT}")
        self.snapshot = load(SNAPSHOT)

    def test_all_four_kinds_dated_and_in_place(self):
        # The whole of it, by the puller's own rules: the four kinds in order,
        # every row carrying exactly its columns, positions 1..n with no gap
        # and no repeat, every value a value a reader can see, every date a
        # real day and none of them after today.
        self.assert_snapshot_is_sound(self.snapshot)

    def test_no_text_column_carries_markup_outside_backticks(self):
        self.assert_no_markup(self.snapshot)

    def test_every_url_in_a_fact_is_in_the_page_s_allowlist(self):
        self.assert_links_are_allowed(self.snapshot)

    def test_no_text_column_carries_a_character_outside_the_allowlist(self):
        # A character a reader cannot see is invisible in `git diff` as well as
        # on the page, so the review this snapshot's safety rests on cannot see
        # it either. The rule says which characters a fact may carry, rather
        # than listing the ones somebody has already been caught by.
        where, values = zip(*text_values(self.snapshot))
        for place, value, found in zip(where, values, refused_in(values)):
            with self.subTest(value=place):
                self.assertEqual(found, [], f"{place} carries {found}: {value!r}")

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

    def test_the_file_ends_in_one_newline(self):
        text = SNAPSHOT.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))


class HandEditedSnapshotTests(unittest.TestCase):
    """The snapshot is machine-written, and it is also a file a person can
    edit: a pull that picks up a fact the page has not caught up with is fixed
    by hand today. None of these is a file a pull would ever write, so this
    check is the only thing between each of them and the page -- and it is the
    puller's own, so what it refuses on the way in it refuses here too."""

    def setUp(self):
        self.snapshot = load(SNAPSHOT)
        # The day the committed file was pulled on: the snapshot has to be
        # sound as of its own day, whenever this suite happens to run.
        self.day = self.snapshot["pulled_at"]

    def assert_refused(self, edit, fragment):
        snapshot = json.loads(json.dumps(self.snapshot))
        edit(snapshot)
        problems = snapshot_problems(snapshot, self.day)
        self.assertTrue(problems, "this snapshot should have been refused")
        self.assertIn(fragment, problems[0])

    def test_the_committed_file_is_sound_as_of_the_day_it_was_pulled(self):
        self.assertEqual(snapshot_problems(self.snapshot, self.day), [])

    def test_a_kind_dropped_from_the_file_is_refused(self):
        # A section of the page with nothing to render, which a generator
        # would either skip or crash on.
        self.assert_refused(lambda facts: facts.pop("measures"), "the snapshot's keys are not")

    def test_rows_stored_out_of_position_order_are_refused(self):
        # The order the rows are stored in is the order the page shows them:
        # the record reads newest first, and the steps read first to last.
        self.assert_refused(lambda facts: facts["status_steps"].reverse(),
                            "is not stored in position order")

    def test_a_row_missing_one_of_its_columns_is_refused(self):
        self.assert_refused(lambda facts: facts["costs"][0].pop("unit"),
                            "does not carry exactly")

    def test_a_value_carrying_an_invisible_character_is_refused(self):
        self.assert_refused(
            lambda facts: facts["measures"][0].update(label="Merges\u200b nobody made"),
            "carries U+200B")

    def test_a_standing_off_the_list_is_refused(self):
        self.assert_refused(lambda facts: facts["measures"][0].update(standing="shipped"),
                            "is not one of hit, pending, miss")

    def test_a_fact_dated_after_the_day_of_the_pull_is_refused(self):
        self.assert_refused(lambda facts: facts["record_entries"][0].update(as_of="2099-01-01"),
                            "is dated after the day of the pull")

    def test_a_fact_dated_after_the_day_the_snapshot_was_pulled_is_refused(self):
        # The repair this class is here for: a stale fact fixed by hand and
        # dated the day it was fixed, with pulled_at left alone. The snapshot
        # then claims it was pulled before the fact in it was true, and the
        # reason has to name the stamp -- that is the stale half, and dating
        # the repair was the right thing to do. The day of the pull is later
        # still here, so the only thing out of order is the stamp.
        snapshot = json.loads(json.dumps(self.snapshot))
        fixed_on = day_after(self.day)
        snapshot["record_entries"][0]["as_of"] = fixed_on
        problems = snapshot_problems(snapshot, day_after(fixed_on))
        self.assertTrue(problems, "a fact dated after pulled_at should have been refused")
        self.assertIn("is dated after pulled_at", problems[0])


class DayOfThePullTests(unittest.TestCase):
    """The day a pull reckons it is. It is the day the snapshot is stamped
    with and the day no fact in it may be dated after, so it has to be the day
    of the person running the pull: east of UTC the UTC day is still yesterday
    for hours, and a fact they entered this morning would be refused as a day
    that has not happened; west of UTC it is already tomorrow, and the
    snapshot would be stamped with a day that has not started for them."""

    # Two zones 25 hours apart, so their calendar days differ at every instant
    # of every day -- which is what makes the second test below fail outright
    # on a pull that reckons by UTC, whatever the hour the suite runs at.
    # Neither has ever observed daylight saving, so a fixed offset is a sound
    # oracle for the day in each.
    ZONES = [("Pacific/Kiritimati", 14), ("Pacific/Pago_Pago", -11)]

    def test_the_day_of_the_pull_is_the_day_where_the_pull_is_run(self):
        for zone, hours in self.ZONES:
            with self.subTest(zone=zone):
                there = datetime.timezone(datetime.timedelta(hours=hours))
                # Either side of the question, because the day can turn over
                # while it is being asked.
                before = datetime.datetime.now(there).date().isoformat()
                answered = day_of_the_pull(zone)
                after = datetime.datetime.now(there).date().isoformat()
                self.assertIn(answered, (before, after),
                              f"a pull run in {zone} must reckon by that day, not the UTC one")

    def test_two_zones_a_day_apart_do_not_pull_on_the_same_day(self):
        answered = {zone: day_of_the_pull(zone) for zone, _ in self.ZONES}
        self.assertEqual(len(set(answered.values())), len(answered),
                         f"these zones are never on the same day: {answered}")

    def test_a_snapshot_pulled_a_day_ahead_of_the_checks_is_not_refused(self):
        # The other side of the same coin: whoever pulls and whatever checks
        # are not in the same place. A pull stamps the file with the day where
        # it was run, and CI runs in UTC, so for the hours their calendars
        # disagree the stamp is a day the checks have not reached -- and a
        # check that called that a date from the future would freeze every
        # merge for those hours. A date is from the future only when no
        # calendar anywhere has reached it.
        ahead, behind = "Pacific/Kiritimati", "Pacific/Pago_Pago"
        snapshot = load(SNAPSHOT)
        snapshot["pulled_at"] = day_of_the_pull(ahead)
        self.assertEqual(snapshot_problems(snapshot, zone=behind), [],
                         f"a snapshot pulled in {ahead} must pass the checks in {behind}")

    def test_a_day_no_calendar_has_reached_is_still_refused(self):
        # And the rule it must not give away: a stamp or a fact dated beyond
        # every calendar on Earth is a clock or a typo, whichever zone the
        # check runs in.
        ahead = "Pacific/Kiritimati"
        snapshot = load(SNAPSHOT)
        snapshot["pulled_at"] = day_after(day_of_the_pull(ahead))
        problems = snapshot_problems(snapshot, zone=ahead)
        self.assertTrue(problems, "a day no calendar has reached should have been refused")
        self.assertIn("pulled_at is dated after", problems[0])


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
            literals = sql_literals(path.read_text(encoding="utf-8"))
            for literal, markup, unchecked, refused in zip(
                    literals, markup_in(literals), unchecked_links(literals),
                    refused_in(literals)):
                with self.subTest(file=path.name, value=literal[:40]):
                    self.assertEqual(markup, [],
                                     f"a fact in {path.name} could put markup on the page: "
                                     f"{literal!r}")
                    self.assertEqual(unchecked, [],
                                     f"a fact in {path.name} carries a link nothing can "
                                     f"vouch for: {literal!r}")
                    self.assertEqual(refused, [],
                                     f"a fact in {path.name} carries a character a reader "
                                     f"cannot see: {literal!r}")

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


class OneDefinitionTests(unittest.TestCase):
    """What a valid fact value is, is written down once -- in the puller
    (decided on #27, 28 Oct 2026). The rules used to be written twice, here
    and there, and the two drifted: a value the pull accepted and this suite
    rejected was a reviewed snapshot that had to be hand-edited before it
    could ship."""

    def test_the_suite_asks_the_puller_rather_than_answering_for_itself(self):
        # tests/rules.py is the Python side of the one definition, and it
        # defines nothing: it hands questions to node and returns the
        # answers. A pattern of its own would be the second copy coming back,
        # so there is none to find.
        source = RULES_PY.read_text(encoding="utf-8")
        for answering in ("import re", "re.compile", "re.search", "re.match"):
            self.assertNotIn(answering, source,
                             f"tests/rules.py reads a fact's text itself ({answering!r}): the "
                             f"puller is the only place a rule about one lives")


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
        # A scenario may name the day the pull happens on, so a read whose
        # facts are dated after that day is a scenario in the folder like any
        # other. An explicit day wins over it.
        day = today or load(scenario).get("today")
        command = [self.node, str(STUB_PULL), str(scenario), str(out)]
        if day is not None:
            command.append(day)
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
                # By the same rules check.sh holds the committed snapshot to,
                # which is the one definition of them: no pull may write a
                # file this suite would then reject.
                self.assert_snapshot_is_sound(snapshot, load(path).get("today"))
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

        self.assertEqual(self.run_puller(same, out, today="2027-03-01").returncode, 0)
        first = out.read_bytes()
        self.assertEqual(load(out)["pulled_at"], "2027-03-01")

        self.assertEqual(self.run_puller(same, out, today="2027-04-02").returncode, 0)
        self.assertEqual(out.read_bytes(), first,
                         "a pull that found nothing new must leave the file byte for byte")

        self.assertEqual(self.run_puller(changed, out, today="2027-04-02").returncode, 0)
        self.assertEqual(load(out)["pulled_at"], "2027-04-02",
                         "a pull that found a changed fact must carry that day")

    def test_a_fact_dated_after_the_day_of_the_pull_is_refused(self):
        # The rows of fixtures/failed-read/rows.json are dated up to
        # 2026-10-26, so the same read is a good read on that day and a read
        # carrying a day that has not happened on the one before it. A fact
        # cannot be true as of a day in the future, whatever makes it so -- a
        # clock, a time zone, a typo in a table.
        out = self.tmpdir() / "facts.json"
        same = FAILED_READ / "same-read-twice.json"

        self.assertEqual(self.run_puller(same, out, today="2026-10-26").returncode, 0)

        out.unlink()
        refused = self.run_puller(same, out, today="2026-10-25")
        self.assertNotEqual(refused.returncode, 0,
                            "a fact dated after the day of the pull must be refused")
        self.assertIn("is dated after the day of the pull (2026-10-25)", refused.stderr)
        self.assertFalse(out.exists(), "a refused pull must write no snapshot")

    def test_a_stamp_in_the_future_is_not_carried_forward(self):
        # pulled_at is carried forward whenever a pull finds nothing new, so a
        # stamp that got into the file ahead of the calendar -- a fast clock,
        # a hand edit -- would be carried forward by every later pull and
        # never looked at again, and the page would show a reader a day that
        # has not come. The next pull replaces it with the day of the pull,
        # which is a one-line diff to review rather than a file to repair.
        out = self.tmpdir() / "facts.json"
        same = FAILED_READ / "same-read-twice.json"

        self.assertEqual(self.run_puller(same, out, today="2027-03-01").returncode, 0)
        ahead = load(out)
        ahead["pulled_at"] = "2099-01-01"
        out.write_text(json.dumps(ahead, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(snapshot_problems(out, "2027-03-02"),
                         ["pulled_at is dated after the day of the pull (2027-03-02): 2099-01-01"],
                         "a snapshot stamped in the future must fail this suite too")

        self.assertEqual(self.run_puller(same, out, today="2027-03-02").returncode, 0)
        self.assertEqual(load(out)["pulled_at"], "2027-03-02")
        self.assert_snapshot_is_sound(load(out), "2027-03-02")

    def test_a_stamp_left_behind_the_facts_is_not_carried_forward(self):
        # The other way the stamp and the facts can part company. pulled_at is
        # carried forward whenever a pull finds nothing new, so a stamp that
        # fell behind the facts it stamps -- a hand edit, a fact repaired and
        # dated the day it was repaired -- would be carried by every later
        # pull and never looked at again. The snapshot would say it was pulled
        # before the facts in it were true, which this suite refuses: a pull
        # that writes a file the repo's own checks reject has to be undone by
        # hand in a reviewed file, which is the one thing one definition of a
        # valid snapshot is for.
        out = self.tmpdir() / "facts.json"
        same = FAILED_READ / "same-read-twice.json"

        self.assertEqual(self.run_puller(same, out, today="2026-10-27").returncode, 0)
        behind = load(out)
        # The newest fact in these rows is dated 2026-10-26.
        behind["pulled_at"] = "2026-10-01"
        out.write_text(json.dumps(behind, indent=2) + "\n", encoding="utf-8")
        self.assertTrue(snapshot_problems(out, "2026-10-27"),
                        "a snapshot stamped before its own facts were true must fail this suite")

        self.assertEqual(self.run_puller(same, out, today="2026-10-27").returncode, 0)
        self.assertEqual(load(out)["pulled_at"], "2026-10-27",
                         "a stamp behind the facts must be replaced with the day of the pull")
        self.assert_snapshot_is_sound(load(out), "2026-10-27")

    def test_a_bad_read_leaves_an_existing_snapshot_untouched(self):
        tmp = self.tmpdir()
        out = tmp / "facts.json"
        before = SNAPSHOT.read_bytes()
        for path in scenarios():
            scenario = load(path)
            if scenario["outcome"] != "fail":
                continue
            with self.subTest(scenario=path.name):
                out.write_bytes(before)
                result = self.run_puller(path, out)
                self.assertNotEqual(result.returncode, 0,
                                    f"{path.name} should have failed the pull")
                self.assertEqual(out.read_bytes(), before,
                                 "a failed pull must leave the snapshot exactly as it was")
                self.assertTrue(result.stderr.strip(), "a failed pull must say why")
                # A scenario may name a fragment of the reason it exists for.
                # Without that, a scenario drifts onto some other rule --
                # which has happened three times -- and still passes.
                if "fails" in scenario:
                    self.assertIn(scenario["fails"], result.stderr,
                                  f"{path.name} no longer fails on the rule it tests")
                self.assertEqual(list(tmp.iterdir()), [out],
                                 "a failed pull left a temporary file behind")

    def test_a_pull_clears_the_working_file_a_killed_run_left_behind(self):
        # A pull killed between writing its working file and renaming it over
        # the snapshot leaves that file in the snapshot's own directory. The
        # snapshot survives whole, which is the important half, but the
        # leftover is untracked clutter beside a reviewed file, one per
        # process, that a `git add -A` can commit. A signal skips any cleanup
        # the killed run might have done, so the next pull is what clears it --
        # whether that pull goes on to succeed or to refuse the read.
        before = SNAPSHOT.read_bytes()
        for path in scenarios():
            scenario = load(path)
            with self.subTest(scenario=path.name):
                tmp = self.tmpdir()
                out = tmp / "facts.json"
                out.write_bytes(before)
                (tmp / WORKING_FILE).write_bytes(before)
                (tmp / NEIGHBOUR).write_bytes(before)
                result = self.run_puller(path, out)
                self.assertEqual(result.returncode == 0, scenario["outcome"] == "ok",
                                 result.stderr)
                self.assertEqual(sorted(p.name for p in tmp.iterdir()),
                                 ["facts.json", NEIGHBOUR],
                                 "a pull must clear a working file left in the snapshot's "
                                 "directory -- and nothing else that lives there")

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
