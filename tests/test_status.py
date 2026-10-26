"""Tests for the status band and the four costs, added in issue #5.

- The status band: one line on whether a `red` issue is open and anything is
  blocked, then one entry per build step, each with a name, a state and a
  date, linked to this repo's issue or PR where there is one.
- The verdict line is checked against this repo's live, open `red` issues
  over GitHub's public API. When there is no network access -- this suite
  also runs in offline sandboxes -- that one check is skipped, not failed;
  every other check here is a plain, offline read of the page.
- The four costs: four figures with the values from the mockup (issue #1,
  option B), each with its own date and no link.
"""

import json
import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

REPO = "todd450/founding-agents-site"
REPO_PREFIX = f"https://github.com/{REPO}/"

# e.g. "24 Oct 2026" -- matches the one date format used across the page.
DATE_RE = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b")

EXPECTED_FIGURES = ["5,410", "61,365", "731", "28"]


def read(path):
    return path.read_text(encoding="utf-8")


def section(html, name):
    match = re.search(rf'<section class="{name}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f'no <section class="{name}"> found'
    return match.group(1)


class StatusBandTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.band = section(self.html, "band")

    def test_band_has_a_verdict_with_a_dot_and_a_dated_stamp(self):
        self.assertRegex(self.band, r'class="verdict">\s*<span class="dot dot-(green|red)">')
        stamp_match = re.search(r'<span class="stamp">(.*?)</span>', self.band, re.DOTALL)
        self.assertIsNotNone(stamp_match, "band has no stamp")
        self.assertRegex(stamp_match.group(1), DATE_RE, "stamp has no date")

    def test_every_step_has_a_name_a_dated_state_and_a_check(self):
        steps = re.findall(r'<li class="step">(.*?)</li>\s*', self.band, re.DOTALL)
        self.assertEqual(len(steps), 4, "expected exactly 4 build steps")
        for step in steps:
            name_match = re.search(r'<span class="step-name">([^<]+)</span>', step)
            self.assertIsNotNone(name_match, "step has no step-name")
            self.assertTrue(name_match.group(1).strip(), "step-name is empty")

            state_match = re.search(r'<span class="step-state[^"]*">([^<]+)</span>', step)
            self.assertIsNotNone(state_match, "step has no step-state")
            self.assertRegex(state_match.group(1), DATE_RE, "step-state has no date")

            check_match = re.search(r'<span class="step-check">(.*?)</span>', step, re.DOTALL)
            self.assertIsNotNone(check_match, "step has no step-check")
            self.assertTrue(re.sub(r"<[^>]+>", "", check_match.group(1)).strip(),
                             "step-check has no text")

    def test_every_link_in_the_band_stays_in_this_repo(self):
        links = re.findall(r'href="([^"]+)"', self.band)
        self.assertTrue(links, "expected at least one link in the status band")
        for link in links:
            self.assertTrue(link.startswith(REPO_PREFIX),
                             f"status band link leaves this repo: {link!r}")

    def test_red_line_agrees_with_the_repo_live(self):
        url = f"https://api.github.com/repos/{REPO}/issues?state=open&labels=red"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                open_red_issues = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise unittest.SkipTest(f"no live network access to check the repo: {exc}")

        verdict_match = re.search(
            r'class="verdict">\s*<span class="dot dot-(green|red)"></span>([^<]+)', self.band)
        self.assertIsNotNone(verdict_match, "band has no verdict line to check")
        dot_colour, verdict_text = verdict_match.groups()

        if open_red_issues:
            self.assertEqual(dot_colour, "red",
                              "a red issue is open live, but the band's dot says otherwise")
            self.assertIn("blocked", verdict_text.lower())
        else:
            self.assertEqual(dot_colour, "green",
                              "no red issue is open live, but the band's dot says otherwise")
            self.assertIn("No open red issue", verdict_text)
            self.assertIn("Nothing is blocked", verdict_text)


class FourCostsTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.costs = section(self.html, "costs")

    def test_four_costs_each_with_a_figure_a_claim_and_a_dated_source(self):
        entries = re.findall(r'<div class="ev">(.*?)</div>', self.costs, re.DOTALL)
        self.assertEqual(len(entries), 4, "expected exactly 4 costs")

        figures = []
        for entry in entries:
            figure_match = re.search(r'<span class="ev-figure">\s*([\d,]+)', entry)
            self.assertIsNotNone(figure_match, "cost has no ev-figure")
            figures.append(figure_match.group(1))

            claim_match = re.search(r'<p class="ev-claim">(.*?)</p>', entry, re.DOTALL)
            self.assertIsNotNone(claim_match, "cost has no ev-claim")
            claim_text = re.sub(r"<[^>]+>", "", claim_match.group(1)).strip()
            self.assertTrue(claim_text, "ev-claim has no text")

            src_match = re.search(r'<span class="ev-src">(.*?)</span>', entry, re.DOTALL)
            self.assertIsNotNone(src_match, "cost has no ev-src")
            src_text = src_match.group(1)
            self.assertRegex(src_text, DATE_RE, "ev-src has no date")
            self.assertNotIn("<a ", src_text, "ev-src carries a link; costs must carry none")

        self.assertEqual(figures, EXPECTED_FIGURES,
                          "cost figures do not match the mockup's numbers")

    def test_no_link_anywhere_in_the_costs_section(self):
        self.assertNotIn("<a ", self.costs, "the four costs must carry dates, never links")


if __name__ == "__main__":
    unittest.main()
