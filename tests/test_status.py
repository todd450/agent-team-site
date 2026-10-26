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
- Added in issue #10: the step states are read, not just counted (step 4 is
  done, and no step's state disagrees with its dot), and the band's first
  column is checked in the CSS for the gutter and divider the step columns
  have, at the mockup's two widths.

Known gap in the live red-issue check: it skips on *any* `OSError` (not just
no network), and nothing in `.github/workflows` runs this suite, so on a
rate-limited or fully offline runner it silently never runs. When it does
run live, an open `red` issue turns this whole suite red -- including on
the very PR that fixes that issue -- since the check has no way to tell
"the band is wrong" from "ops watch is already on it". Left as-is here;
worth fixing if that happens in practice.
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
        # Issue #10: step 4's done condition is one real run of propose on
        # this site's brief, and that run is the queue item on issue #1. The
        # shape checks above pass just as happily on a state that is no
        # longer true, so this one reads the state itself.
        step = self._steps()[3]
        self.assertIn("propose reaches the founder as one item", step)
        state_match = re.search(r'<span class="step-state([^"]*)">([^<]+)</span>', step)
        self.assertIsNotNone(state_match, "step 4 has no step-state")
        classes, state = state_match.groups()
        self.assertNotIn("running", classes,
                          "step 4 is done, but its state still carries the running class")
        self.assertRegex(state, r"^\s*Done\b", f"step 4's state is not Done: {state!r}")
        self.assertNotIn("Running", state)
        self.assertRegex(state, DATE_RE, "step 4's state has no date")
        self.assertIn(REPO_PREFIX + "issues/1", step,
                       "step 4 does not link the run that closed it, issue #1")
        self.assertNotIn("One more real run", step,
                          "step 4 is done, but it still says one more real run closes the step")

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
            lowered = verdict_text.lower()
            # The substring "blocked" alone proves nothing -- "Nothing is
            # blocked." contains it too. A red issue open live must make the
            # verdict say something is blocked, and must not leave the old
            # all-clear claim standing.
            self.assertNotIn(
                "nothing is blocked", lowered,
                "a red issue is open live, but the verdict still claims nothing is blocked",
            )
            self.assertIn(
                "blocked", lowered,
                "a red issue is open live, but the verdict does not say anything is blocked",
            )
            issue_numbers = {str(issue["number"]) for issue in open_red_issues}
            self.assertTrue(
                any(number in self.band for number in issue_numbers),
                "a red issue is open live, but the band does not name it",
            )
        else:
            self.assertEqual(dot_colour, "green",
                              "no red issue is open live, but the band's dot says otherwise")
            self.assertIn("No open red issue", verdict_text)
            self.assertIn("Nothing is blocked", verdict_text)


STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
MEDIA_OPEN_RE = re.compile(r"@media([^{]*)\{")
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
MEDIA_WIDTH_RE = re.compile(r"\((min|max)-width\s*:\s*(\d+)px\)")

# The four sides a padding shorthand expands to, in CSS's own order.
PADDING_SIDES = ("padding-top", "padding-right", "padding-bottom", "padding-left")
ZERO_VALUES = {"0", "0px", "0em", "0rem"}


def css_blocks(html):
    """Split the page's one <style> block into (media condition, css) pairs:
    the top-level rules first (condition ""), then each @media block in
    source order. Every top-level rule in this page precedes the first
    @media block, so that order is the cascade order."""
    match = STYLE_BLOCK_RE.search(html)
    assert match is not None, "no <style> block found"
    css = CSS_COMMENT_RE.sub("", match.group(1))
    top_level = []
    media = []
    position = 0
    while True:
        open_match = MEDIA_OPEN_RE.search(css, position)
        if open_match is None:
            top_level.append(css[position:])
            break
        top_level.append(css[position:open_match.start()])
        depth = 1
        index = open_match.end()
        while depth and index < len(css):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        assert depth == 0, "unbalanced @media block in the page's <style>"
        media.append((open_match.group(1).strip(), css[open_match.end():index - 1]))
        position = index
    return [("", "".join(top_level))] + media


def media_applies(condition, width):
    """True when an @media condition holds at a viewport of `width` px. Only
    the min-width/max-width conditions this page uses are understood; any
    other condition raises rather than being quietly treated as applying."""
    if condition == "":
        return True
    found = MEDIA_WIDTH_RE.findall(condition)
    assert found, f"unsupported @media condition: {condition!r}"
    leftover = MEDIA_WIDTH_RE.sub("", condition).replace("and", "").strip()
    assert leftover == "", f"unsupported @media condition: {condition!r}"
    return all((width >= int(px)) if kind == "min" else (width <= int(px))
               for kind, px in found)


def declarations(html, selector, width):
    """Every declaration that applies to `selector` at a viewport of `width`
    px, later rules winning, with the padding shorthand expanded so a
    padding-right set by shorthand is visible to the checks below."""
    resolved = {}
    for condition, block in css_blocks(html):
        if not media_applies(condition, width):
            continue
        for selectors, body in CSS_RULE_RE.findall(block):
            if selector not in [part.strip() for part in selectors.split(",")]:
                continue
            for declaration in body.split(";"):
                if ":" not in declaration:
                    continue
                prop, _, value = declaration.partition(":")
                prop, value = prop.strip(), value.strip()
                if prop == "padding":
                    parts = value.split()
                    top = parts[0]
                    right = parts[1] if len(parts) > 1 else top
                    bottom = parts[2] if len(parts) > 2 else top
                    left = parts[3] if len(parts) > 3 else right
                    resolved.update(dict(zip(PADDING_SIDES, (top, right, bottom, left))))
                else:
                    resolved[prop] = value
    return resolved


class BandGutterTests(unittest.TestCase):
    """Issue #10: at 1440px the band's first column -- the red-issue verdict
    line -- ended at the exact left edge of the STEP 1 column, because only
    the step columns carried the gutter and the 1px rule that divide them
    from each other. Checked in the CSS at both of the mockup's widths:
    nothing in this suite renders the page, so a width is only ever the set
    of rules that apply at it."""

    DESKTOP = 1440
    PHONE = 390

    def setUp(self):
        self.html = read(INDEX_HTML)

    def test_first_column_has_the_step_columns_gutter_at_1440(self):
        head = declarations(self.html, ".band-head", self.DESKTOP)
        step = declarations(self.html, ".step", self.DESKTOP)
        gutter = step.get("padding-right")
        self.assertTrue(gutter and gutter not in ZERO_VALUES,
                         "the step columns have no gutter to match")
        self.assertEqual(
            head.get("padding-right"), gutter,
            "at 1440px the band's first column has no gutter: the verdict line ends at the "
            "exact left edge of the STEP 1 column")

    def test_first_column_has_a_divider_at_1440(self):
        head = declarations(self.html, ".band-head", self.DESKTOP)
        divider = head.get("border-right", "")
        self.assertIn("1px", divider,
                       "at 1440px the band's first column has no 1px divider between it and the "
                       "STEP 1 column")
        self.assertIn("var(--rule)", divider,
                       "the first column's divider is not the rule colour the step columns use")

    def test_a_full_height_divider_does_not_spread_the_first_column_at_1440(self):
        # The first column is a flex column with justify-content:space-between
        # from the phone layout, where its height is its content. Stretching
        # it so the divider runs the full height of the steps beside it hands
        # space-between a column of free space to spread into, which drops the
        # dated stamp to the bottom of the band.
        head = declarations(self.html, ".band-head", self.DESKTOP)
        if head.get("align-self") == "stretch":
            self.assertNotEqual(
                head.get("justify-content"), "space-between",
                "the first column is stretched to run its divider the full height of the "
                "steps, so space-between pushes the stamp away from the verdict line")

    def test_first_column_keeps_its_gutter_and_divider_at_390(self):
        # At 390px the band stacks, so the gutter and divider are below the
        # verdict line rather than beside it. This already held before issue
        # #10; it is here so the desktop fix cannot be made by moving the
        # phone's divider instead of adding one.
        head = declarations(self.html, ".band-head", self.PHONE)
        bottom = head.get("padding-bottom")
        self.assertTrue(bottom and bottom not in ZERO_VALUES,
                         "at 390px the verdict line sits flush against step 1 with no gutter")
        divider = head.get("border-bottom", "")
        self.assertIn("1px", divider, "at 390px the verdict line has no divider below it")
        self.assertIn("var(--rule)", divider,
                       "the first column's divider is not the rule colour the steps use")


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
