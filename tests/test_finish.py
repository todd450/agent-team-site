"""Tests for the four sections filled in by issue #6: how a change flows,
what this is not, the measures table and the record of this build.

- The flow section: a labelled list of roles, each with a name and a
  description, including links to this repo's check_site.py and
  ops-watch.yml.
- The limits section: names the one thing this site has no evidence for.
- The measures table: one row per measure, each with a bar and a current
  figure, headed by a dated column.
- The record: one entry per event, each with a dated "when" and some text,
  newest first -- checked here by actually parsing every date and asserting
  the list never goes forward in time as it's read top to bottom.
- No "question" section: issue #6's hold says the naming question the
  mockup carried is settled (the repo was renamed), so there is nothing to
  make a placeholder catch -- that is covered in test_shell.py instead,
  next to the rest of this page's section-shape checks.
"""

import re
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

REPO_PREFIX = "https://github.com/todd450/founding-agents-site/"

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


class FlowSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.flow = section(self.html, "flow")

    def test_every_role_has_a_name_and_a_description(self):
        entries = re.findall(
            r'<span class="who">([^<]+)</span><span class="does">(.*?)</span></li>',
            self.flow, re.DOTALL)
        names = [name.strip() for name, _ in entries]
        self.assertEqual(names, EXPECTED_ROLES,
                          f"role list does not match the expected roles, got {names!r}")
        for name, does in entries:
            self.assertTrue(strip_tags(does), f"role {name!r} has no description")

    def test_links_check_site_and_ops_watch_workflow(self):
        self.assertIn(REPO_PREFIX + "blob/main/.github/ops-watch/check_site.py", self.flow)
        self.assertIn(REPO_PREFIX + "blob/main/.github/workflows/ops-watch.yml", self.flow)

    def test_linked_repo_files_actually_exist(self):
        # These two links point at files in this repo rather than at an
        # issue or PR, so nothing else checks the path is still there to be
        # served -- check_site.py in particular is copied in by the
        # plugin's setup skill and can move without anyone touching this
        # page.
        self.assertTrue((ROOT / ".github" / "ops-watch" / "check_site.py").is_file())
        self.assertTrue((ROOT / ".github" / "workflows" / "ops-watch.yml").is_file())

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

    def test_names_the_missing_evidence(self):
        text = strip_tags(self.limits)
        self.assertIn("No evidence", text)
        self.assertTrue(text, "limits section has no text")

    def test_no_link_in_the_limits_section(self):
        # The one thing this bet does not have evidence for is not a claim
        # that can be checked against a file, issue or PR -- it carries no
        # link, same rule as the four costs.
        self.assertNotIn("<a ", self.limits)


STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")


class ResponsiveMeasureTests(unittest.TestCase):
    """Every other paragraph on the page is capped to the mockup's measure
    (section > .body{max-width:var(--measure)}), but at >=860px this PR's
    two new blocks -- the limits box and the record's entries -- have
    nothing capping their width, so they'd run the full shell width while
    the paragraph right above each one stays capped to the measure."""

    def setUp(self):
        self.html = read(INDEX_HTML)
        match = STYLE_BLOCK_RE.search(self.html)
        self.assertIsNotNone(match, "no <style> block found")
        self.style = match.group(1)

    def _rule_bodies(self, selector_fragment):
        for selectors, body in CSS_RULE_RE.findall(self.style):
            parts = [p.strip() for p in selectors.split(",")]
            if any(selector_fragment in part for part in parts):
                yield body

    def _is_capped_to_the_measure(self, selector_fragment):
        return any("max-width" in body and "var(--measure)" in body
                   for body in self._rule_bodies(selector_fragment))

    def test_limits_box_prose_is_capped_to_the_measure(self):
        self.assertTrue(
            self._is_capped_to_the_measure(".limits-box"),
            ".limits-box has no max-width:var(--measure), so it runs the full shell width "
            "while the paragraph above it is capped")

    def test_record_list_entries_are_capped_to_the_measure(self):
        self.assertTrue(
            self._is_capped_to_the_measure(".record-list"),
            ".record-list has no max-width:var(--measure), so its entries run the full shell "
            "width while the record's own intro paragraph is capped")


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

    def test_every_measure_has_a_bar_and_a_figure(self):
        body_match = re.search(r"<tbody>(.*?)</tbody>", self.measures, re.DOTALL)
        self.assertIsNotNone(body_match, "measures table has no body")
        rows = re.findall(r"<tr>(.*?)</tr>", body_match.group(1), re.DOTALL)
        self.assertEqual(len(rows), len(EXPECTED_MEASURES),
                          f"expected {len(EXPECTED_MEASURES)} measures, got {len(rows)}")
        for row, expected in zip(rows, EXPECTED_MEASURES):
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            self.assertEqual(len(cells), 3, f"expected 3 cells in row, got {cells!r}")
            label, bar, figure = (strip_tags(cell) for cell in cells)
            self.assertIn(expected, label, f"row does not name measure {expected!r}: {label!r}")
            self.assertTrue(bar, f"measure {expected!r} has no bar")
            self.assertTrue(figure, f"measure {expected!r} has no current figure")


class RecordSectionTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.record = section(self.html, "record")

    @staticmethod
    def _parse_date(text):
        day, month, year = DATE_RE.search(text).groups()
        return datetime.strptime(f"{day} {month} {year}", "%d %b %Y")

    def _li_count(self):
        return len(re.findall(r"<li>", self.record))

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

    def test_record_includes_this_build(self):
        self.assertIn("this build", self.record.lower())
        self.assertIn("#6", self.record)

    def test_record_includes_the_rename(self):
        lowered = self.record.lower()
        self.assertIn("founding agents", lowered)
        self.assertIn("agent-team-site", lowered)
        self.assertIn("founding-agents-site", lowered)

    def test_record_keeps_the_unflattering_parts(self):
        # This record is supposed to keep what does not flatter the team,
        # not just the wins -- it should still carry the branch-protection
        # surprise and the review findings from earlier builds.
        self.assertIn("surprise", self.record.lower())
        self.assertIn("review", self.record.lower())


if __name__ == "__main__":
    unittest.main()
