"""Tests for the status band and the four costs.

- The status band: one verdict line with a dot and a dated stamp, then one
  entry per build step, each with a name, a state and a date, and every link
  in the band staying in this repo.
- The step states are read, not just counted: step 4 is done, and no step's
  state disagrees with its dot.
- The four costs: four figures with the values from the mockup (issue #1,
  option B), each with its own date and no link.

Dropped in issue #12:

- The check that asked GitHub's API whether a `red` issue is open live. Unit
  tests here make no network call; ops watch checks the live site every
  night, which is also the only place that can tell "the band is wrong" from
  "ops watch is already on it".
- The band's gutter and divider tests, which read the page's CSS as text and
  so passed while the rendered page had no gutter. A real browser now
  measures both, at 390px and 1440px, in tests/browser/page.spec.js.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

REPO = "todd450/founding-agents-site"
REPO_PREFIX = f"https://github.com/{REPO}/"

# e.g. "24 Oct 2026" -- matches the one date format used across the page.
DATE_RE = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b")

EXPECTED_FIGURES = ["5,410", "61,365", "731", "28"]

# One name per build step, in order -- used to check that all four steps are
# actually present (not, say, the same step repeated four times).
EXPECTED_STEP_NAMES = [
    "main requires a pull request",
    "Ops watch checks the site nightly",
    "build runs to merge on real issues",
    "propose reaches the founder as one item",
]


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

    def _steps(self):
        steps = re.findall(r'<li class="step">(.*?)</li>\s*', self.band, re.DOTALL)
        self.assertEqual(len(steps), 4, "expected exactly 4 build steps")
        return steps

    def test_band_has_a_verdict_with_a_dot_and_a_dated_stamp(self):
        self.assertRegex(self.band, r'class="verdict">\s*<span class="dot dot-(green|red)">')
        stamp_match = re.search(r'<span class="stamp">(.*?)</span>', self.band, re.DOTALL)
        self.assertIsNotNone(stamp_match, "band has no stamp")
        self.assertRegex(stamp_match.group(1), DATE_RE, "stamp has no date")

    def test_every_step_has_a_name_a_dated_state_and_a_check(self):
        steps = self._steps()
        names = []
        for step in steps:
            name_match = re.search(r'<span class="step-name">([^<]+)</span>', step)
            self.assertIsNotNone(name_match, "step has no step-name")
            name = name_match.group(1).strip()
            self.assertTrue(name, "step-name is empty")
            names.append(name)

            state_match = re.search(r'<span class="step-state[^"]*">([^<]+)</span>', step)
            self.assertIsNotNone(state_match, "step has no step-state")
            self.assertRegex(state_match.group(1), DATE_RE, "step-state has no date")

            check_match = re.search(r'<span class="step-check">(.*?)</span>', step, re.DOTALL)
            self.assertIsNotNone(check_match, "step has no step-check")
            self.assertTrue(re.sub(r"<[^>]+>", "", check_match.group(1)).strip(),
                             "step-check has no text")

        self.assertEqual(len(set(names)), 4,
                          f"expected 4 distinct step names, got {names!r}")
        self.assertEqual(names, EXPECTED_STEP_NAMES,
                          "step names do not match the 4 build steps issue #5 names")

    def test_step_four_is_done_and_names_the_run_that_closed_it(self):
        # Step 4's done condition is one real run of propose on this site's
        # brief, and that run is the queue item on issue #1. The shape checks
        # above pass just as happily on a state that is no longer true, so
        # this one reads the state itself.
        step = self._steps()[3]
        self.assertIn("propose reaches the founder as one item", step)
        state_match = re.search(r'<span class="step-state([^"]*)">([^<]+)</span>', step)
        self.assertIsNotNone(state_match, "step 4 has no step-state")
        classes, state = state_match.groups()
        self.assertNotIn("running", classes,
                          "step 4 is done, but its state still carries the running class")
        self.assertRegex(state, r"^\s*Done\b", f"step 4's state is not Done: {state!r}")
        self.assertRegex(state, DATE_RE, "step 4's state has no date")
        self.assertIn(REPO_PREFIX + "issues/1", step,
                       "step 4 does not link the run that closed it, issue #1")

    def test_each_step_state_agrees_with_its_dot(self):
        # A done step carries a green dot; a running one carries the open dot
        # and the running class. Guards the fix above against a state and a
        # dot that disagree.
        for index, step in enumerate(self._steps(), start=1):
            with self.subTest(step=index):
                dot_match = re.search(r'<span class="dot dot-([a-z]+)">', step)
                self.assertIsNotNone(dot_match, f"step {index} has no dot")
                state_match = re.search(r'<span class="step-state([^"]*)">([^<]+)</span>', step)
                self.assertIsNotNone(state_match, f"step {index} has no step-state")
                classes, state = state_match.groups()
                if re.match(r"\s*Done\b", state):
                    self.assertEqual(dot_match.group(1), "green")
                    self.assertNotIn("running", classes)
                else:
                    self.assertRegex(state, r"^\s*Running\b",
                                      f"step {index}'s state is neither Done nor Running: "
                                      f"{state!r}")
                    self.assertEqual(dot_match.group(1), "open")
                    self.assertIn("running", classes)

    def test_every_link_in_the_band_stays_in_this_repo(self):
        links = re.findall(r'href="([^"]+)"', self.band)
        self.assertTrue(links, "expected at least one link in the status band")
        for link in links:
            self.assertTrue(link.startswith(REPO_PREFIX),
                             f"status band link leaves this repo: {link!r}")


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
