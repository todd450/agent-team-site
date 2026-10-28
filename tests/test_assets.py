"""Tests for the assets the pages of this site ship alongside them.

The founder's decisions on #14 are what these hold to: a page loads a
vendored CSS reset (D1, P1: modern-normalize, committed as one file), and
every dependency that ships is self-hosted, committed to this repo, with no
third-party request made when the page loads (D5).

Asked of every page the site serves, which is the generator's answer and not
a filename written here (tests/pages.py): a page is a template and a copy
file from issue #28 on, and the assets it loads are resolved from its own
URL, because a page served from `<name>/` is not served from the root.

So, for every asset a page loads:

- A vendored copy matches a SHA-256 digest pinned here, so editing it fails
  the check instead of drifting from upstream unnoticed, and it says which
  upstream version it is.
- It defines no custom property, so tokens.css stays the one token source
  the stylelint rule guards.
- It is a file that is actually in this repo, and nothing it loads in turn
  (an @import, a url(...)) reaches off this site.

Nothing here pins today's wording, and nothing here makes a network call:
the digest is compared against the bytes already committed.
"""

import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from pages import served_from, served_pages

ROOT = Path(__file__).resolve().parent.parent

# Each asset vendored into this repo, with the upstream release it is a copy
# of and the SHA-256 of that release's bytes. To take a new upstream version:
# replace the file, change the version and digest here, and say in the PR
# where the bytes came from.
VENDORED = {
    "modern-normalize.css": {
        "version": "3.0.1",
        # sindresorhus/modern-normalize v3.0.1, identical on npm and at
        # https://github.com/sindresorhus/modern-normalize (tag v3.0.1).
        "sha256": "b4ad31da9b1b17de182a08e4c39159496574d90a82464c6d5afeda7133f0e110",
    },
}

# The attributes that make the browser fetch something.
ASSET_ATTRS = {"link": "href", "img": "src", "script": "src", "source": "src", "iframe": "src"}

CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# A custom property being *defined*: at the start of a declaration, not
# somewhere inside a value.
CUSTOM_PROPERTY_RE = re.compile(r"(?:^|[;{])\s*(--[\w-]+)\s*:")
# Anything a stylesheet pulls in on its own: url(...) wherever it appears,
# and an @import whether or not it is written as a url(...).
CSS_URL_RES = (
    re.compile(r"""url\(\s*['"]?([^'")]+)""", re.IGNORECASE),
    re.compile(r"""@import\s+(?:url\(\s*)?['"]?([^'")\s;]+)""", re.IGNORECASE),
)
STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)


class AssetParser(HTMLParser):
    """Collects what the page tells the browser to fetch."""

    def __init__(self):
        super().__init__()
        self.assets = []  # (tag, attrs dict, url) in document order

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        url = attrs.get(ASSET_ATTRS.get(tag, ""))
        if url and url.strip():
            self.assets.append((tag, attrs, url.strip()))


def page_assets(page):
    parser = AssetParser()
    parser.feed((ROOT / page).read_text(encoding="utf-8"))
    parser.close()
    return parser.assets


def is_off_site(url):
    """True if fetching this would leave this site. A data: URI is inline
    bytes, not a request, so it does not."""
    if url.startswith("//"):
        return True
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    return bool(scheme) and scheme.group(1).lower() != "data"


def urls_in_css(text):
    text = CSS_COMMENT_RE.sub("", text)
    return [url.strip() for pattern in CSS_URL_RES for url in pattern.findall(text)]


def local_assets(page):
    """Every asset this page fetches from this repo, as (tag, attrs, url, path
    from the repo root) -- the address the page writes and the file the site
    would hand back for it."""
    found = []
    for tag, attrs, url in page_assets(page):
        if is_off_site(url) or url.startswith("#"):
            continue
        found.append((tag, attrs, url, served_from(page, url)))
    return found


class VendoredFileTests(unittest.TestCase):
    """The rules a copy of someone else's file has to keep."""

    def test_every_vendored_file_is_in_this_repo(self):
        for name in VENDORED:
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(),
                                f"{name} is pinned here but is not in this repo")

    def test_every_vendored_file_matches_its_pinned_digest(self):
        for name, pin in VENDORED.items():
            with self.subTest(name=name):
                digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                self.assertEqual(
                    digest, pin["sha256"],
                    f"{name} is not the upstream v{pin['version']} bytes any more. Either put "
                    f"the upstream file back, or take a new upstream version and change the "
                    f"version and digest in {Path(__file__).name} with it.")

    def test_every_vendored_file_says_which_version_it_is(self):
        # The digest says the bytes are unchanged; this says a reader opening
        # the file can tell what they are a copy of, and of what release.
        for name, pin in VENDORED.items():
            with self.subTest(name=name):
                head = (ROOT / name).read_text(encoding="utf-8")[:500]
                self.assertIn(pin["version"], head,
                              f"{name} does not name its upstream version near the top")

    def test_no_vendored_file_defines_a_custom_property(self):
        # tokens.css is the one place a token may be defined (the stylelint
        # property-disallowed-list rule guards the files it lints). A
        # vendored file is not linted -- it is held to its upstream bytes --
        # so the same rule is kept here.
        for name in VENDORED:
            if not name.endswith(".css"):
                continue
            with self.subTest(name=name):
                css = CSS_COMMENT_RE.sub("", (ROOT / name).read_text(encoding="utf-8"))
                defined = CUSTOM_PROPERTY_RE.findall(css)
                self.assertEqual(defined, [],
                                 f"{name} defines {defined}, a second source of tokens")


class PageAssetTests(unittest.TestCase):
    """What a page is allowed to load."""

    def test_every_asset_each_page_loads_is_a_file_in_this_repo(self):
        for page in served_pages():
            assets = local_assets(page)
            self.assertTrue(assets, f"{page} loads no asset of its own at all")
            for tag, _attrs, url, path in assets:
                with self.subTest(page=page, asset=url):
                    self.assertFalse(url.startswith("/"),
                                     f"<{tag}> on {page} loads {url!r}, a root-relative path that "
                                     f"leaves this project site on GitHub Pages")
                    self.assertFalse(path.startswith(".."),
                                     f"<{tag}> on {page} loads {url!r}, from outside this site")
                    self.assertTrue((ROOT / path).is_file(),
                                    f"<{tag}> on {page} loads {url!r}, which the site serves from "
                                    f"{path!r} and this repo does not hold: the page would 404 "
                                    f"on it")

    def test_no_stylesheet_any_page_loads_fetches_from_off_this_site(self):
        # Self-hosted means self-hosted all the way down: a committed file
        # that opens with @import url(https://fonts...) still makes the page
        # ask a third party for bytes at load (#14, D5).
        sources = {}
        for page in served_pages():
            sources[page] = "\n".join(
                STYLE_BLOCK_RE.findall((ROOT / page).read_text(encoding="utf-8")))
            for tag, attrs, _url, path in local_assets(page):
                # A stylesheet that is missing is the other test's failure to
                # report, not a crash in this one.
                if tag == "link" and attrs.get("rel") == "stylesheet" and (ROOT / path).is_file():
                    sources[path] = (ROOT / path).read_text(encoding="utf-8")
        self.assertTrue(any(name.endswith(".css") for name in sources),
                        "no stylesheet of this site was read at all")
        for name, text in sources.items():
            for url in urls_in_css(text):
                with self.subTest(name=name, url=url):
                    self.assertFalse(is_off_site(url),
                                     f"{name} fetches {url!r} from off this site when the page "
                                     f"loads")


if __name__ == "__main__":
    unittest.main()
