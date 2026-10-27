"""Tests for the four sections at the foot of the page: how a change flows,
what this is not, the measures table and the record of this build.

- The flow section: a labelled list of roles, each with a name and a
  description, and it points at the ops-watch check in this repo so a reader
  can go and read it.
- The limits section: one named thing this site has no evidence for, with
  prose and no link.
- The measures table: one row per measure, each with a bar and a current
  figure, headed by a dated column.
- The record: one entry per event, each with a dated "when" and some text,
  newest first -- checked by parsing every date and asserting the list never
  goes forward in time as it's read top to bottom -- with exactly one entry
  marked "this build", the newest.

Dropped in issue #12: the tests that read the page's CSS as text to check
each block of prose was capped to the measure (a real browser now measures
the rendered width of every one of them, at 390px and 1440px, in
tests/browser/page.spec.js), and the two that pinned today's wording -- the
number of review rounds issue #6's entry claims, and the record mentioning
the rename. Neither can be checked from the page; a record entry's own prose
is the one thing this suite has to take on trust.
"""

import re
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

REPO_PREFIX = "https://github.com/todd450/founding-agents-site/"
BLOB_PREFIX = REPO_PREFIX + "blob/main/"

DATE_RE = re.compile(r"\b(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})\b")

EXPECTED_ROLES = [
    "Product lead",
    "Designer",
    "Founder",
    "Builder",
    "Reviewer",
    "Integrator",
    "Ops watch",
]

EXPECTED_MEASURES = [
    "Merges not made by the integrator",
    "build",
    "propose",
    "Lines deleted within two weeks of being written",
    "Age of the oldest open",
    "Stars, traffic, installs",
]


def read(path):
    return path.read_text(encoding="utf-8")


def section(html, name):
    match = re.search(rf'<section class="{name}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f'no <section class="{name}"> found'
    return match.group(1)


def strip_tags(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def ordered_list(html, name):
    match = re.search(rf'<ol class="{name}"[^>]*>(.*?)</ol>', html, re.DOTALL)
    assert match is not None, f'no <ol class="{name}"> found'
    return match.group(1)


# Every one of these matches an opening tag that may carry attributes: the
# page's house style is to put a class on these tags (<li class="step">,
# <td class="figure">), so a literal "<li>" or "<tr>" makes exactly the rows
# someone is most likely to write next invisible to the checks below.
LI_OPEN_RE = re.compile(r"<li\b")
ROLE_RE = re.compile(
    r'<span class="who">([^<]+)</span><span class="does">(.*?)</span></li>', re.DOTALL)
ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)


class FlowSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.flow = section(self.html, "flow")

    def check_roles(self, rail):
        """Assert the rail is a *labelled* list of roles: every <li> in it
        carries a name and a description. Takes the rail's HTML so the same
        assertions can be run against a doctored rail below."""
        li_count = len(LI_OPEN_RE.findall(rail))
        self.assertTrue(li_count, "no roles found in the flow rail")
        entries = ROLE_RE.findall(rail)
        self.assertEqual(len(entries), li_count,
                          f"expected every one of the {li_count} roles in the rail to carry a "
                          f"name and a description, got {len(entries)}")
        for name, does in entries:
            self.assertTrue(name.strip(), "role has no name")
            self.assertTrue(strip_tags(does), f"role {name!r} has no description")
        return [name.strip() for name, _ in entries]

    def test_every_role_has_a_name_and_a_description(self):
        names = self.check_roles(ordered_list(self.flow, "rail"))
        self.assertEqual(names, EXPECTED_ROLES,
                          f"role list does not match the expected roles, got {names!r}")

    def test_a_role_with_no_label_is_not_invisible(self):
        # An unlabelled role used to be invisible rather than a failure: the
        # role findall simply did not see it, and the names it did find still
        # matched EXPECTED_ROLES. Issue #6 asks for a *labelled* list, so
        # both shapes -- bare <li> and the house style's attributed <li> --
        # have to fail.
        rail = ordered_list(self.flow, "rail")
        for unlabelled in ('<li><span class="does">Marketing sells it to nobody.</span></li>',
                           '<li class="x"><span class="does">Unlabelled step.</span></li>'):
            with self.subTest(unlabelled=unlabelled):
                with self.assertRaises(AssertionError):
                    self.check_roles(rail + unlabelled)

    def test_the_flow_points_at_the_check_a_reader_can_read(self):
        # The section's claim is that the nightly check is in this repo and
        # can be read, so it has to link at least one file in it. That the
        # files those links name are really there is checked page-wide, in
        # test_shell.py.
        files = [link for link in re.findall(r'href="([^"]+)"', self.flow)
                 if link.startswith(BLOB_PREFIX)]
        self.assertTrue(files,
                         "the flow section links no file in this repo, so its claim that the "
                         "check can be read is unsupported")

    def test_every_link_in_flow_stays_in_this_repo(self):
        links = re.findall(r'href="([^"]+)"', self.flow)
        self.assertTrue(links, "expected at least one link in the flow section")
        for link in links:
            self.assertTrue(link.startswith(REPO_PREFIX),
                             f"flow link leaves this repo: {link!r}")


class LimitsSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.limits = section(self.html, "limits")

    def test_names_one_thing_there_is_no_evidence_for(self):
        headings = re.findall(r"<h3[^>]*>(.*?)</h3>", self.limits, re.DOTALL)
        self.assertEqual(len(headings), 1,
                          f"expected the limits section to name exactly one thing, got "
                          f"{[strip_tags(h) for h in headings]!r}")
        self.assertTrue(strip_tags(headings[0]), "the limit named has no text")
        paragraphs = [strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>",
                                                        self.limits, re.DOTALL)]
        self.assertTrue(any(paragraphs), "the limits section has no prose under its heading")

    def test_no_link_in_the_limits_section(self):
        # The one thing this bet does not have evidence for is not a claim
        # that can be checked against a file, issue or PR -- it carries no
        # link, same rule as the four costs.
        self.assertNotIn("<a ", self.limits)


class MeasuresSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.measures = section(self.html, "measures")

    def test_header_row_has_measure_bar_and_a_dated_column(self):
        header_match = re.search(r"<thead>(.*?)</thead>", self.measures, re.DOTALL)
        self.assertIsNotNone(header_match, "measures table has no header row")
        headers = re.findall(r"<th[^>]*>(.*?)</th>", header_match.group(1), re.DOTALL)
        self.assertEqual(len(headers), 3, f"expected 3 header cells, got {headers!r}")
        self.assertEqual(strip_tags(headers[0]), "Measure")
        self.assertEqual(strip_tags(headers[1]), "Bar")
        self.assertRegex(strip_tags(headers[2]), DATE_RE, "third header has no date")

    def _tbody(self):
        body_match = re.search(r"<tbody>(.*?)</tbody>", self.measures, re.DOTALL)
        self.assertIsNotNone(body_match, "measures table has no body")
        return body_match.group(1)

    def check_rows(self, body):
        """Assert every row in the table body names a measure and carries a
        bar and a current figure. Takes the body's HTML so the same
        assertions can be run against a doctored body below."""
        rows = ROW_RE.findall(body)
        self.assertEqual(len(rows), len(EXPECTED_MEASURES),
                          f"expected {len(EXPECTED_MEASURES)} measures, got {len(rows)}")
        for row, expected in zip(rows, EXPECTED_MEASURES):
            cells = CELL_RE.findall(row)
            self.assertEqual(len(cells), 3, f"expected 3 cells in row, got {cells!r}")
            label, bar, figure = (strip_tags(cell) for cell in cells)
            self.assertIn(expected, label, f"row does not name measure {expected!r}: {label!r}")
            self.assertTrue(bar, f"measure {expected!r} has no bar")
            self.assertTrue(figure, f"measure {expected!r} has no current figure")

    def test_every_measure_has_a_bar_and_a_figure(self):
        self.check_rows(self._tbody())

    def test_a_measure_row_with_no_bar_or_figure_is_not_invisible(self):
        # A row carrying an attribute used to be skipped entirely, which left
        # the row count equal to EXPECTED_MEASURES and put a measure on the
        # page with no bar and no dated value. Both shapes -- bare <tr> and
        # the house style's attributed <tr> -- have to fail.
        body = self._tbody()
        for extra in ("<tr><td>Outside users who asked for this</td></tr>",
                      '<tr class="new"><td>Outside users who asked for this</td></tr>'):
            with self.subTest(extra=extra):
                with self.assertRaises(AssertionError):
                    self.check_rows(body + extra)


class RecordSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.record = section(self.html, "record")

    @staticmethod
    def _parse_date(text):
        day, month, year = DATE_RE.search(text).groups()
        return datetime.strptime(f"{day} {month} {year}", "%d %b %Y")

    def _li_count(self):
        return len(LI_OPEN_RE.findall(self.record))

    def test_every_entry_has_a_dated_when_and_some_text(self):
        li_count = self._li_count()
        self.assertTrue(li_count, "no record entries found")
        entries = re.findall(r'<span class="when">(.*?)</span>\s*<p>(.*?)</p>',
                              self.record, re.DOTALL)
        self.assertEqual(len(entries), li_count,
                          f"expected every one of the {li_count} <li> entries to produce a "
                          f"dated when and some text, got {len(entries)}")
        for when, body in entries:
            self.assertRegex(when, DATE_RE, f"record entry has no date: {when!r}")
            self.assertTrue(strip_tags(body), f"record entry has no text: {when!r}")

    def test_record_is_newest_first(self):
        li_count = self._li_count()
        self.assertTrue(li_count, "no record entries found")
        whens = re.findall(r'<span class="when">(.*?)</span>', self.record, re.DOTALL)
        self.assertEqual(len(whens), li_count,
                          f"expected every one of the {li_count} <li> entries to produce a "
                          f"parsed, dated when, got {len(whens)}")
        dates = [self._parse_date(when) for when in whens]
        self.assertEqual(dates, sorted(dates, reverse=True),
                          "record entries are not in newest-first date order")

    def _entries(self):
        entries = re.findall(
            r'<li\b[^>]*>\s*<span class="when">(.*?)</span>\s*<p>(.*?)</p>',
            self.record, re.DOTALL)
        self.assertEqual(len(entries), self._li_count(),
                          "not every record entry parses as a when and a paragraph")
        return [(strip_tags(when), strip_tags(body)) for when, body in entries]

    def test_only_the_newest_entry_is_marked_this_build(self):
        # "this build" is the entry for the merge the reader is looking at.
        # Once a later build adds its own entry the older one has to stop
        # claiming the label, or the record carries two of them and one is
        # false.
        whens = [when.lower() for when, _ in self._entries()]
        marked = [index for index, when in enumerate(whens) if "this build" in when]
        self.assertEqual(marked, [0],
                          f"expected only the newest entry to be marked 'this build', got "
                          f"entries {marked!r} of {whens!r}")


if __name__ == "__main__":
    unittest.main()
