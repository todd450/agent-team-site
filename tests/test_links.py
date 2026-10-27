"""Tests for the rule that says where a link on this site may point.

The rule is written twice, because the two places that need it cannot share
code: `scripts/pull-facts.js` is the gate on what enters the facts snapshot,
and `tests/links.py` is what the suite checks index.html and the snapshot
with. So the cases live here once and both copies answer all of them, and a
hole opened in one is a failure here rather than a link on the page.

What the cases pin: a link is judged by where a browser would land, not by
what the string starts with. An address that begins with this repo's own and
then walks out of it -- on `..`, on backslashes, on escaped dots, or on a tab
a browser strips before it parses -- is a link off this site.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from links import PAGES_PREFIX, REPO_PREFIX, allowed_link

CHECK_LINKS_JS = Path(__file__).resolve().parent / "check_links.js"

# (link, why it is in the allowlist)
ALLOWED = [
    (REPO_PREFIX, "the repository itself"),
    (f"{REPO_PREFIX}/pull/35", "a pull request in it"),
    (f"{REPO_PREFIX}/issues/3#issuecomment-1", "an issue, at a comment"),
    (f"{REPO_PREFIX}/blob/main/check.sh", "a file in it"),
    (f"{REPO_PREFIX}/issues?q=open", "a query on it"),
    (f"{REPO_PREFIX}/pulls?q=is%3Apr+is%3Amerged", "a query whose escapes are ordinary text"),
    (PAGES_PREFIX, "the site itself"),
    (f"{PAGES_PREFIX}/", "the site's own page"),
    (f"{PAGES_PREFIX}/index.html", "a file on the site"),
]

# (link, where it really goes)
REFUSED = [
    (f"{REPO_PREFIX}/../../attacker/evil-repo", "github.com/attacker/evil-repo"),
    (f"{REPO_PREFIX}/..\\..\\attacker/evil", "github.com/attacker/evil, on backslashes"),
    (f"{REPO_PREFIX}/%2e%2e/%2e%2e/evil", "github.com/evil, on escaped dots"),
    (f"{REPO_PREFIX}/x%2F%2E%2E%2Fevil", "github.com, by a path only the server decodes: an "
                                         "escaped slash and dots that no reader can follow"),
    (f"{REPO_PREFIX}/%zz", "nowhere a reader can work out: that is not an escape"),
    (f"{REPO_PREFIX}/\t../../evil/x", "github.com/evil/x, past a stripped tab"),
    (f"{REPO_PREFIX}/\n../../evil/x", "github.com/evil/x, past a stripped newline"),
    (f"{REPO_PREFIX}/./evil", "github.com/todd450/founding-agents-site/evil, "
                              "which is inside the allowlist but is not the address written down"),
    # A dot segment does not need a slash after it to do its work: the query or
    # the fragment ends the path just as well, and so does the end of the
    # address itself.
    (f"{REPO_PREFIX}/..?q=1", "github.com/todd450/, on dots a query ends"),
    (f"{REPO_PREFIX}/..#readme", "github.com/todd450/, on dots a fragment ends"),
    (f"{REPO_PREFIX}/..", "github.com/todd450/, on dots the address ends"),
    (f"{REPO_PREFIX}/..\n", "github.com/todd450/, on dots a stripped newline ends"),
    (f"{REPO_PREFIX}/x/..?q=1", "github.com/todd450/founding-agents-site/, inside the allowlist "
                                "but not the address written down"),
    (f"{PAGES_PREFIX}/../../evil/x", "todd450.github.io/evil/x, another site on the account"),
    (f"{PAGES_PREFIX}/..?q=1", "todd450.github.io/, which answers 404"),
    (f"{PAGES_PREFIX}/..#top", "todd450.github.io/, which answers 404"),
    (f"{PAGES_PREFIX}/..\n", "todd450.github.io/, which answers 404"),
    (f"{REPO_PREFIX}-evil/pull/2", "a repository whose name merely starts with this one's"),
    (f"{REPO_PREFIX}@evil.example/x", "evil.example, with this repo's address as a username"),
    ("https://github.com/todd450/founding-agents", "the private plugin repo"),
    ("http://github.com/todd450/founding-agents-site", "this repo, but not over https"),
    ("https://github.com.evil.example/todd450/founding-agents-site", "a lookalike host"),
    ("https://evil.example/https://github.com/todd450/founding-agents-site", "evil.example"),
    ("javascript:alert(1)", "nowhere: a script, not a page"),
    ("//github.com/todd450/founding-agents-site", "whatever scheme the page was served over"),
    ("", "nowhere at all"),
]


class LinkRuleTests(unittest.TestCase):
    """tests/links.py, the copy the suite checks the page and the snapshot
    with."""

    def test_a_link_into_this_repo_or_its_site_is_allowed(self):
        for link, why in ALLOWED:
            with self.subTest(link=link):
                self.assertTrue(allowed_link(link), f"{why} is this site")

    def test_a_link_that_lands_anywhere_else_is_refused(self):
        for link, where in REFUSED:
            with self.subTest(link=link):
                self.assertFalse(allowed_link(link), f"this link goes to {where}")


class PullerAgreesTests(unittest.TestCase):
    """scripts/pull-facts.js, the copy that decides what may enter the
    snapshot. It must answer every case the same way."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def verdicts(self, links):
        self.assertIsNotNone(self.node, "node is needed to run the puller's copy of the rule")
        result = subprocess.run(
            [self.node, str(CHECK_LINKS_JS)], input=json.dumps(links),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_puller_answers_every_case_the_same_way(self):
        cases = [(link, True) for link, _ in ALLOWED] + [(link, False) for link, _ in REFUSED]
        verdicts = self.verdicts([link for link, _ in cases])
        self.assertEqual(len(verdicts), len(cases))
        for (link, expected), answer in zip(cases, verdicts):
            with self.subTest(link=link):
                self.assertEqual(answer, expected,
                                 "the puller's copy of the link rule and tests/links.py disagree")


if __name__ == "__main__":
    unittest.main()
