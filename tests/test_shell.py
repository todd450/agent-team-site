"""Tests for the page shell: index.html and tokens.css.

What is left here is only what no standard tool checks -- this site's own
rules:

- Every link on the page goes only to this repo's public files, issues and
  PRs, or to the site's own address (never into the private plugin repo or
  anywhere else, and never a root-relative path that leaves the project
  site's subdirectory on GitHub Pages).
- A link into a file in this repo points at a file that is actually there to
  be served: a 404 opens a `red` issue and freezes every merge.
- The page is built from the six sections the brief names, none of them a
  leftover placeholder, and tokens.css is its one stylesheet.
- Every var(...) the page calls is defined in tokens.css.

Dropped in issue #12, because a standard tool in check.sh now does the job:
the hand-written tag parser and its nesting test, the doctype and
one-html/head/body checks (html-validate), and the colour, font, spacing and
"no second token source" checks (stylelint, with
stylelint-declaration-strict-value and property-disallowed-list). Nothing
here pins today's wording, and nothing here makes a network call.
"""

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

# "Where a link may point", asked of the one place it is written down:
# scripts/pull-facts.js, through tests/rules.py. The page and the facts
# snapshot are held to the same allowlist, by the same code.
from rules import PAGES_PREFIX, REPO_PREFIX, allowed_links

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
TOKENS_CSS = ROOT / "tokens.css"

LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
              "iframe": "src"}

SECTION_CLASSES = ["band", "costs", "flow", "limits", "measures", "record"]


class PageParser(HTMLParser):
    """Collects the tags, attributes and links on the page. Nothing here
    checks that the markup is well formed -- html-validate does that."""

    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, attrs dict) in document order
        self.links = []  # every href/src found on the page

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        value = attrs.get(LINK_ATTRS.get(tag, ""))
        if value:
            self.links.append(value.strip())


def parse(html):
    parser = PageParser()
    parser.feed(html)
    parser.close()
    return parser


def read(path):
    return path.read_text(encoding="utf-8")


class FilesExistTests(unittest.TestCase):
    """Kept in its own class, with no setUp that reads the files, so this
    test can actually fail on its own if a file goes missing rather than
    always being pre-empted by another test's setUp."""

    def test_files_exist(self):
        self.assertTrue(INDEX_HTML.is_file())
        self.assertTrue(TOKENS_CSS.is_file())


class PageShapeTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX_HTML)
        self.page = parse(self.html)

    def test_links_the_one_token_source(self):
        stylesheet_links = [attrs.get("href") for tag, attrs in self.page.tags
                             if tag == "link" and attrs.get("rel") == "stylesheet"]
        self.assertEqual(stylesheet_links, ["tokens.css"])

    def test_header_has_one_headline_with_text(self):
        headers = [attrs for tag, attrs in self.page.tags if tag == "header"]
        self.assertEqual(len(headers), 1)
        headlines = re.findall(r"<h1[^>]*>(.*?)</h1>", self.html, re.DOTALL)
        self.assertEqual(len(headlines), 1, "the page has no single h1 headline")
        self.assertTrue(re.sub(r"<[^>]+>", "", headlines[0]).strip(),
                         "the h1 headline has no text")

    def test_footer_says_where_the_site_is_served_from(self):
        footers = re.findall(r"<footer[^>]*>(.*?)</footer>", self.html, re.DOTALL)
        self.assertEqual(len(footers), 1)
        links = re.findall(r'href="([^"]+)"', footers[0])
        self.assertTrue(any(link.startswith(PAGES_PREFIX) for link in links),
                         f"the footer does not link the address it is served from: {links!r}")

    def test_all_six_sections_are_present(self):
        # Issue #1's mockup carried a seventh section, an open question about
        # the product's name. Issue #6 settled that question (the founder
        # renamed the repo) before this page was built, so there is no
        # question section to fill -- the rename is a record entry instead.
        for name in SECTION_CLASSES:
            match = re.search(
                rf'<section class="{name}"[^>]*>(.*?)</section>', self.html, re.DOTALL)
            self.assertIsNotNone(match, f"no section found for {name!r}")

    def test_no_question_section(self):
        # Kept separate from the six-sections test above so this has its own
        # failure message: a stray leftover "question" section (reviving the
        # mockup's now-settled naming question) would otherwise only show up
        # as an unrelated miscount.
        self.assertNotIn('<section class="question"', self.html)

    def test_no_placeholder_sections_remain(self):
        # Every section issue #4 stubbed out is now filled in: "band" and
        # "costs" by issue #5, the rest by issue #6. None should still be an
        # empty "<h2>...</h2>" shell or carry a "Filled in issue #N" comment.
        self.assertNotIn("Filled in issue #", self.html)
        for name in SECTION_CLASSES:
            match = re.search(
                rf'<section class="{name}"[^>]*>(.*?)</section>', self.html, re.DOTALL)
            self.assertIsNotNone(match, f"no section found for {name!r}")
            body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL)
            body = re.sub(r"<h2[^>]*>.*?</h2>", "", body, flags=re.DOTALL)
            self.assertNotEqual(body.strip(), "",
                                 f"section {name!r} is still an empty placeholder")


class LinksAllowedTests(unittest.TestCase):
    def setUp(self):
        self.page = parse(read(INDEX_HTML))

    def test_every_link_is_relative_or_to_this_repo_or_its_pages_site(self):
        self.assertTrue(self.page.links, "expected at least one link on the page")
        # The absolute ones go to the puller's allowlist in one call: it asks
        # where a browser would land, so an address that begins with this
        # repo's own and then walks out of it on a ".." segment is not inside
        # it.
        absolute = [link for link in self.page.links if link.startswith(("http://", "https://"))]
        allowed = dict(zip(absolute, allowed_links(absolute)))
        for link in self.page.links:
            if link.startswith("//"):
                self.fail(f"protocol-relative link leaves this site: {link!r}")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", link) and not link.startswith(("http://", "https://")):
                self.fail(f"link uses a disallowed scheme: {link!r}")
            if link in allowed:
                self.assertTrue(allowed[link],
                                f"link leaves this repo's public pages: {link!r}")
            else:
                # A relative link must stay inside this site: no "../"
                # escape, and no leading "/" either -- this site is served
                # from a project subdirectory on GitHub Pages
                # (todd450.github.io/founding-agents-site/), so a
                # root-relative link such as "/about" resolves to
                # todd450.github.io/about, outside the site entirely, and
                # 404s on the nightly link check.
                self.assertFalse(link.startswith(".."), f"relative link escapes the site: {link!r}")
                self.assertFalse(link.startswith("/"),
                                  f"root-relative link leaves the project site: {link!r}")

    def test_every_file_this_page_links_in_this_repo_exists(self):
        # Links to an issue or a PR are checked live by ops watch every
        # night. A link to a file in this repo is the one kind this suite can
        # check offline, and the one most likely to rot: check_site.py is
        # copied in by the plugin's setup skill and can move without anyone
        # touching this page.
        blob_prefix = f"{REPO_PREFIX}/blob/main/"
        paths = [link[len(blob_prefix):].split("#")[0].split("?")[0]
                 for link in self.page.links if link.startswith(blob_prefix)]
        self.assertTrue(paths, "expected the page to link at least one file in this repo")
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(),
                                 f"the page links {path!r}, which is not in this repo: a 404 on "
                                 f"the nightly check opens a red issue and freezes every merge")

    # No separate "not the private plugin repo" test: the allowlist in
    # The allowlist already requires every absolute link to match one of its
    # two prefixes exactly, with a boundary check on the character
    # that follows, so a link into the private repo (todd450/founding-agents,
    # without "-site") can never pass it. A standalone substring check for
    # "founding-agents" would now be a false positive on this repo's own,
    # legitimate address -- founding-agents-site contains that substring.


STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class TokensDefinedTests(unittest.TestCase):
    """stylelint requires every colour, font and spacing value on the page to
    come from a custom property, and allows custom properties to be defined
    only in tokens.css. It cannot tell whether the property a var(...) call
    names is one that exists -- a typo such as var(--inkk) is valid CSS that
    silently resolves to nothing -- so that cross-file check lives here."""

    def setUp(self):
        self.tokens_css = read(TOKENS_CSS)
        self.html = read(INDEX_HTML)
        self.page = parse(self.html)
        self.style_text = self._collect_style_text(self.html, self.page)

    @staticmethod
    def _collect_style_text(html, page):
        # Every <style> block (not just the first) plus every style=
        # attribute found anywhere on the page -- those are the only two
        # places CSS can live on this page.
        blocks = STYLE_BLOCK_RE.findall(html)
        style_attrs = [attrs["style"] for _, attrs in page.tags if attrs.get("style")]
        text = "\n".join(blocks + style_attrs)
        # Strip comments so a comment that mentions a var(...) name can't
        # stand in for a real call to it.
        return CSS_COMMENT_RE.sub("", text)

    def _token_names(self):
        return set(re.findall(r"--([a-z0-9-]+)\s*:", self.tokens_css))

    def test_tokens_css_defines_colour_type_and_spacing(self):
        names = self._token_names()
        self.assertIn("ink", names)
        self.assertIn("paper", names)
        self.assertIn("font-text", names)
        self.assertIn("font-mono", names)
        self.assertIn("s4", names)

    def test_every_var_the_page_uses_is_defined_in_tokens_css(self):
        # Match the token name whether the call is var(--name) or has a
        # fallback, var(--name, literal) -- a fallback's name must still
        # exist in tokens.css, not just the call's syntax.
        used = set(re.findall(r"var\(--([a-z0-9-]+)\s*[,)]", self.style_text))
        self.assertTrue(used, "the page calls no var(...) at all")
        undefined = used - self._token_names()
        self.assertEqual(undefined, set(), f"uses var(...) not defined in tokens.css: {undefined}")


if __name__ == "__main__":
    unittest.main()
