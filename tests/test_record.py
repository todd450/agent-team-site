"""Tests for the record's own page, served at /record/ (issue #29).

D6 and P6, decided on #14, give the record a page of its own, written by
`scripts/generate.js` to `record/index.html` -- the one URL shape every static
host serves identically, with no rewrite config, so a later host change cannot
break it. The entries live on that page and nowhere else, so there is no
second surface that can show a record which has fallen behind this one.

What the cases here pin -- the record's rules, moved out of
`tests/test_finish.py`, which keeps the three sections it still covers:

- The page carries every entry in the snapshot, newest first, each with a
  dated "when" and some text. The order is read from the parsed dates and not
  from the order the entries happen to be written in, and every `<li>` in the
  list has to parse: an entry this suite cannot read is a failure, not an
  entry it silently skips.
- Exactly one entry is marked "this build", and it is the newest.
- The home page's record section points at the page with a relative link and
  lists no entry of its own, and no other page the site serves lists one
  either.
- The record page links back to the home page relatively, so both pages work
  under the github.io subpath and under a domain root.
- Every link on it is one this site may carry, and every file in this repo it
  links is there to be served -- a 404 opens a `red` issue and freezes every
  merge.
- No entries, one entry and the most there can be all render the page, with
  the "this build" mark on the newest entry when there is one and on nothing
  when there is not (`fixtures/empty-and-one`).

The record page at 390px and 1440px -- axe, and every block of prose inside
the measure -- is `tests/browser/page.spec.js`, in a real browser.

Nothing here pins today's wording: the mark is read from the copy file that
defines it, and the entries are compared against the snapshot they are
rendered from. Nothing here makes a network call either -- the committed pages
are read as files, and the fixtures are rendered by the generator, which reads
only the snapshot it is given.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# "Where a link may point", asked of the one place it is written down:
# scripts/pull-facts.js, through tests/rules.py.
from rules import REPO_PREFIX
# The pages the site serves, that allowlist asked about the addresses on one of
# them, and which file a relative address on a page asks the site for -- which
# depends on the page's own URL, and this page is not served from the root.
from pages import link_problems, links_on, served_from, served_pages

ROOT = Path(__file__).resolve().parent.parent

# The record page, as the generator names it and as the site serves it.
RECORD_PAGE = "record/index.html"
RECORD_HTML = ROOT / "record" / "index.html"
INDEX_PAGE = "index.html"
INDEX_HTML = ROOT / INDEX_PAGE

SNAPSHOT = ROOT / "content" / "facts.json"
RECORD_COPY = ROOT / "content" / "copy" / "en" / "record.json"
EMPTY_AND_ONE = ROOT / "fixtures" / "empty-and-one"
GENERATE_JS = ROOT / "scripts" / "generate.js"
NODE = shutil.which("node")

DATE_RE = re.compile(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})\b")

# The record list, and the entries in it. LI_OPEN_RE matches an opening tag
# that may carry attributes: this site's house style puts a class on these
# tags, so a literal "<li>" would make exactly the entry someone is most
# likely to write next invisible to the checks below.
LIST_RE = re.compile(r'<ol class="record-list"[^>]*>(.*?)</ol>', re.DOTALL)
LI_OPEN_RE = re.compile(r"<li\b")
ENTRY_RE = re.compile(
    r'<li\b[^>]*>\s*<span class="when">(.*?)</span>\s*<p>(.*?)</p>\s*</li>', re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def read(path):
    return path.read_text(encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def section(html, name):
    match = re.search(rf'<section class="{name}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f'no <section class="{name}"> found'
    return match.group(1)


def strip_tags(text):
    return TAG_RE.sub("", text).strip()


def record_list(html):
    """The record list on this page, or None when the page carries none."""
    found = LIST_RE.search(html)
    return found.group(1) if found else None


def mark():
    """The string the newest entry is marked with, read from the copy file that
    defines it rather than written out here."""
    return load(RECORD_COPY)["record.thisBuild"]


def relative(links):
    """The addresses a browser would resolve against the page it found them
    on, rather than follow straight out to another host."""
    return [link for link in links
            if not link.startswith(("http://", "https://", "//", "#"))]


class RecordPageTests(unittest.TestCase):
    """The page itself: every entry in the snapshot, newest first."""

    def setUp(self):
        self.assertTrue(RECORD_HTML.is_file(),
                        f"the record is served at /record/ by {RECORD_PAGE}, and there is no "
                        f"such page: run `npm run build`")
        self.html = read(RECORD_HTML)
        self.list = record_list(self.html)
        self.assertIsNotNone(self.list, f"{RECORD_PAGE} carries no record list at all")

    def entries(self):
        """Every entry on the page as (when, text), with every `<li>` in the
        list held to parsing as one."""
        li_count = len(LI_OPEN_RE.findall(self.list))
        found = ENTRY_RE.findall(self.list)
        self.assertEqual(len(found), li_count,
                         f"expected every one of the {li_count} entries in the record list to "
                         f"carry a dated when and some text, got {len(found)}")
        return found

    @staticmethod
    def parse_date(text):
        day, month, year = DATE_RE.search(text).groups()
        return datetime.strptime(f"{day} {month} {year}", "%d %b %Y")

    def test_the_page_carries_every_entry_in_the_snapshot(self):
        entries = self.entries()
        expected = load(SNAPSHOT)["record_entries"]
        self.assertEqual(len(entries), len(expected),
                         f"the page shows {len(entries)} entries and the snapshot holds "
                         f"{len(expected)}")
        self.assertTrue(entries, "the snapshot holds no record entry at all")

    def test_every_entry_has_a_dated_when_and_some_text(self):
        entries = self.entries()
        self.assertTrue(entries, "no record entries found")
        for when, body in entries:
            self.assertRegex(when, DATE_RE, f"record entry has no date: {when!r}")
            self.assertTrue(strip_tags(body), f"record entry has no text: {when!r}")

    def test_the_record_is_newest_first(self):
        # Read from the parsed dates, not from the order the entries were
        # written in: an entry put at the top with an older date than the one
        # below it is the failure this catches.
        entries = self.entries()
        self.assertTrue(entries, "no record entries found")
        dates = [self.parse_date(when) for when, _ in entries]
        self.assertEqual(dates, sorted(dates, reverse=True),
                         "record entries are not in newest-first date order")

    def test_only_the_newest_entry_is_marked_this_build(self):
        # "this build" is the entry for the merge the reader is looking at.
        # Once a later build adds its own entry the older one has to stop
        # claiming the mark, or the record carries two of them and one is
        # false.
        whens = [strip_tags(when).lower() for when, _ in self.entries()]
        marked = [index for index, when in enumerate(whens) if mark().lower() in when]
        self.assertEqual(marked, [0],
                         f"expected only the newest entry to carry the mark {mark()!r}, got "
                         f"entries {marked!r} of {whens!r}")

    def test_it_links_back_to_the_home_page_relatively(self):
        # Relative, so the pair works under the github.io subpath and under a
        # domain root alike: the home page is what this page's own directory
        # resolves up to.
        home = [link for link in relative(links_on(self.html))
                if served_from(RECORD_PAGE, link) in (".", INDEX_PAGE)]
        self.assertTrue(home,
                        f"{RECORD_PAGE} carries no relative link back to the home page: "
                        f"{relative(links_on(self.html))!r}")

    def test_every_link_on_it_is_one_this_site_may_carry(self):
        links = links_on(self.html)
        self.assertTrue(links, f"expected at least one link on {RECORD_PAGE}")
        self.assertEqual(link_problems(RECORD_PAGE, links), [],
                         f"{RECORD_PAGE} carries a link this site may not carry")

    def test_every_file_in_this_repo_it_links_is_there_to_be_served(self):
        blob_prefix = f"{REPO_PREFIX}/blob/main/"
        paths = [link[len(blob_prefix):].split("#")[0].split("?")[0]
                 for link in links_on(self.html) if link.startswith(blob_prefix)]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(),
                                f"{RECORD_PAGE} links {path!r}, which is not in this repo: a 404 "
                                f"on the nightly check opens a red issue and freezes every merge")


class OnePageOnlyTests(unittest.TestCase):
    """The home page points at the record; it does not show it.

    Two surfaces showing the same record is two records: the one a reader
    lands on first is the one that falls behind.
    """

    def setUp(self):
        self.html = read(INDEX_HTML)
        self.record = section(self.html, "record")

    def test_the_home_page_points_at_the_record_page_relatively(self):
        pointing = [link for link in relative(re.findall(r'href="([^"]+)"', self.record))
                    if served_from(INDEX_PAGE, link) in ("record", RECORD_PAGE)]
        self.assertTrue(pointing,
                        f"the home page's record section does not link {RECORD_PAGE} relatively: "
                        f"{re.findall(r'href=.([^\"]+).', self.record)!r}")

    def test_the_home_page_record_section_still_has_a_heading_and_a_paragraph(self):
        # The section stays on the home page -- a link into the record area
        # has to keep landing somewhere, and the page's six sections stay six.
        headings = re.findall(r"<h2[^>]*>(.*?)</h2>", self.record, re.DOTALL)
        self.assertEqual(len(headings), 1,
                         f"expected one heading in the home page's record section, got "
                         f"{[strip_tags(h) for h in headings]!r}")
        self.assertTrue(strip_tags(headings[0]), "the record section's heading has no text")
        paragraphs = [strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>",
                                                        self.record, re.DOTALL)]
        self.assertTrue(any(paragraphs),
                        "the home page's record section has no prose under its heading")

    def test_the_home_page_lists_no_entry(self):
        self.assertIsNone(record_list(self.html),
                          "the home page lists record entries as well as the record page, so the "
                          "site has two records and one of them will fall behind")

    def test_no_page_but_the_record_page_lists_an_entry(self):
        # Asked of every page the generator writes, so a page added later
        # cannot quietly become a second record.
        listing = [page for page in served_pages()
                   if record_list(read(ROOT / page)) is not None]
        self.assertEqual(listing, [RECORD_PAGE],
                         f"the record is listed on {listing!r}, and it is one page's to show")


class EmptyAndOneTests(unittest.TestCase):
    """fixtures/empty-and-one, asked of the record page: no entries, one entry
    and far more than it has ever carried.

    The record gains an entry with every merge and has no cap, so a page that
    only holds together at today's count is a page that breaks on the next
    pull. The mark is the case the counts decide: it goes on the newest entry
    when there is one, and on nothing at all when there is none.
    """

    @classmethod
    def setUpClass(cls):
        if NODE is None:  # pragma: no cover - CI and check.sh both have Node
            raise unittest.SkipTest("node is needed: the generator writes the page")

    def rendered(self, snapshot):
        """The record page as the generator writes it from this snapshot."""
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        result = subprocess.run(
            [NODE, str(GENERATE_JS), "--root", str(ROOT), "--out", str(out),
             "--snapshot", str(snapshot)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0,
                         f"{snapshot.name} did not render: {result.stderr}")
        page = out / "record" / "index.html"
        self.assertTrue(page.is_file(), f"{snapshot.name} rendered no {RECORD_PAGE}")
        return read(page)

    def scenarios(self):
        found = sorted(EMPTY_AND_ONE.glob("*.json"))
        self.assertTrue(found, f"no scenarios in {EMPTY_AND_ONE}")
        return found

    def whens(self, html):
        """The dated line above each entry, with every `<li>` held to parsing
        as an entry."""
        listed = record_list(html)
        self.assertIsNotNone(listed, "the rendered record page carries no record list")
        found = ENTRY_RE.findall(listed)
        self.assertEqual(len(found), len(LI_OPEN_RE.findall(listed)),
                         "not every entry on the rendered page carries a when and some text")
        return [strip_tags(when) for when, _ in found]

    def test_every_scenario_renders_every_entry_it_holds(self):
        for path in self.scenarios():
            with self.subTest(scenario=path.name):
                whens = self.whens(self.rendered(path))
                self.assertEqual(len(whens), len(load(path)["record_entries"]),
                                 f"{path.name}: the page does not show every entry")
                for when in whens:
                    self.assertRegex(when, DATE_RE, f"{path.name}: an entry has no date")

    def test_the_mark_goes_on_the_newest_entry_or_on_nothing(self):
        for path in self.scenarios():
            with self.subTest(scenario=path.name):
                whens = [when.lower() for when in self.whens(self.rendered(path))]
                marked = [index for index, when in enumerate(whens) if mark().lower() in when]
                self.assertEqual(marked, [0] if whens else [],
                                 f"{path.name}: the mark {mark()!r} is on entries {marked!r} of "
                                 f"{len(whens)}")


if __name__ == "__main__":
    unittest.main()
