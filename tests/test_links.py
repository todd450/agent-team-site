"""Tests for the two rules about a link on this site: where it may point, and
how a fact's text is read to find it.

Both are written twice, because the two places that need them cannot share
code: `scripts/pull-facts.js` is the gate on what enters the facts snapshot,
and `tests/links.py` is what the suite checks index.html and the snapshot
with. So the cases live here once and both copies answer all of them, and a
hole opened in one is a failure here rather than a link on the page.

What the cases pin:

- A link is judged by where a browser would land, not by what the string
  starts with. An address that begins with this repo's own and then walks out
  of it -- on `..`, on backslashes, on escaped dots, or on a tab a browser
  strips before it parses -- is a link off this site.
- A reader that cannot read a link whole says so instead of finding nothing.
  A renderer reads more as a link than the reader's pattern does, and every
  such link is one whose destination no allowlist would ever see.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from links import PAGES_PREFIX, REPO_PREFIX, allowed_link, link_targets

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

# A fact's text, and every link a reader has to find in it. These are texts
# the reader can read whole, so what it finds is what a renderer would.
# (text, targets)
READABLE = [
    ("Nothing here links anywhere.", []),
    (f"Built in [PR #35]({REPO_PREFIX}/pull/35).", [f"{REPO_PREFIX}/pull/35"]),
    (f"[the issue]({REPO_PREFIX}/issues/27) and [the site]({PAGES_PREFIX}/),",
     [f"{REPO_PREFIX}/issues/27", f"{PAGES_PREFIX}/"]),
    ("A bracketed aside [like this one] that links nowhere.", []),
    ("An empty target [here]() points at the page itself.", [""]),
    ("A link off this site [evil](https://evil.example/x) is still read whole.",
     ["https://evil.example/x"]),
]

# A text that writes a link this reader cannot read whole. A renderer sees a
# link in each of these; the reader sees no target, or only the inner one, so
# the destination that would reach the href is one nothing checked. Each must
# be reported as unreadable rather than quietly passing.
# (text, what a renderer makes of it)
UNREADABLE = [
    ("[the writeup [part two]](javascript:alert(1))",
     "a link to javascript:, since link text may hold balanced brackets"),
    (f"[![chart]({REPO_PREFIX}/chart.png)](javascript:alert(1))",
     "an image wrapped in a link to javascript:; only the image is read"),
    (r"[the run \]log](javascript:alert(1))",
     "a link to javascript:, past a backslash-escaped bracket"),
    (f"Read [the run]({REPO_PREFIX}/pull/35), then [see [this]](javascript:alert(1)).",
     "one link the reader does read, and one it does not"),
    (f"[a]({REPO_PREFIX}/x)](javascript:alert(1))",
     "a target left over once the link the reader did read is taken out"),
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


class LinkReaderTests(unittest.TestCase):
    """tests/links.py again: finding the links in a fact's text, and saying
    so when a link cannot be read whole."""

    def test_every_link_in_a_text_it_can_read_is_found(self):
        for text, targets in READABLE:
            with self.subTest(text=text):
                self.assertEqual(link_targets(text), (targets, False))

    def test_a_link_it_cannot_read_whole_is_reported_rather_than_missed(self):
        for text, renders_as in UNREADABLE:
            with self.subTest(text=text):
                _, unreadable = link_targets(text)
                self.assertTrue(unreadable, f"a renderer makes {renders_as} of this")


class PullerAgreesTests(unittest.TestCase):
    """scripts/pull-facts.js, the copies that decide what may enter the
    snapshot. They must answer every case the same way."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def ask_the_puller(self, links=(), texts=()):
        self.assertIsNotNone(self.node, "node is needed to run the puller's copy of the rule")
        result = subprocess.run(
            [self.node, str(CHECK_LINKS_JS)],
            input=json.dumps({"links": list(links), "texts": list(texts)}),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_puller_answers_every_case_the_same_way(self):
        cases = [(link, True) for link, _ in ALLOWED] + [(link, False) for link, _ in REFUSED]
        verdicts = self.ask_the_puller(links=[link for link, _ in cases])["links"]
        self.assertEqual(len(verdicts), len(cases))
        for (link, expected), answer in zip(cases, verdicts):
            with self.subTest(link=link):
                self.assertEqual(answer, expected,
                                 "the puller's copy of the link rule and tests/links.py disagree")

    def test_the_puller_reads_every_text_the_same_way(self):
        texts = [text for text, _ in READABLE] + [text for text, _ in UNREADABLE]
        answers = self.ask_the_puller(texts=texts)["texts"]
        self.assertEqual(len(answers), len(texts))
        for text, answer in zip(texts, answers):
            with self.subTest(text=text):
                targets, unreadable = link_targets(text)
                self.assertEqual([answer["targets"], answer["unreadable"]],
                                 [targets, unreadable],
                                 "the puller's link reader and tests/links.py disagree")


if __name__ == "__main__":
    unittest.main()
