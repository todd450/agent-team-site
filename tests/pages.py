"""The pages this site serves, and the rules every one of them keeps.

From issue #28 the pages are generated output: `scripts/generate.js` writes
`templates/pages/<name>.html` to `<name>/index.html` (`index.html` at the
root), so which pages exist is the generator's answer and not a list of
filenames kept by hand -- the same list `check.sh` runs html-validate and
stylelint over. A check pinned to `index.html` by name stops applying the
moment a page is added, and the page added would be the one carrying a link
nobody looked at.

So this module holds the two things every page is held to, asked of whatever
pages the generator writes:

- where a link may point -- the allowlist in `scripts/pull-facts.js`, through
  `tests/rules.py`, so the page and the facts snapshot are held to it by the
  same code;
- which file a relative address on a page asks for, which depends on the
  page's own URL: a page served from `<name>/` is not served from the root.

Nothing here makes a network call: the generator reads the committed snapshot,
and the allowlist is asked about addresses as strings.
"""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from posixpath import normpath

from rules import allowed_links

ROOT = Path(__file__).resolve().parent.parent
GENERATE_JS = ROOT / "scripts" / "generate.js"
NODE = shutil.which("node")

# The attributes that point somewhere: a link to follow, or something the
# browser fetches as the page loads.
LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
              "iframe": "src"}

ABSOLUTE = ("http://", "https://")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

_SERVED = {}


def served_pages(root=ROOT):
    """Every page this site serves, as a path relative to `root`, asked of the
    generator -- which is also told to check that each is a fresh build."""
    if NODE is None:  # pragma: no cover - CI and check.sh both have Node
        raise RuntimeError("node is needed: the generator is what knows which pages there are")
    key = str(Path(root).resolve())
    if key not in _SERVED:
        result = subprocess.run(
            [NODE, str(GENERATE_JS), "--check", "--root", key, "--out", key],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        if result.returncode != 0:
            raise RuntimeError(f"the generator could not name its pages: {result.stderr.strip()}")
        _SERVED[key] = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return list(_SERVED[key])


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get(LINK_ATTRS.get(tag, ""))
        if value and value.strip():
            self.found.append(value.strip())


def links_on(html):
    """Every address on this page: a link to follow or an asset to fetch."""
    parser = _Links()
    parser.feed(html)
    parser.close()
    return parser.found


def link_problems(links):
    """Why these addresses are not ones a page of this site may carry, or []
    when they are.

    Every absolute one goes to the puller's allowlist in a single call: it
    asks where a browser would land, so an address that begins with this
    repo's own and then walks out of it on a ".." segment is not inside it.
    """
    absolute = [link for link in links if link.startswith(ABSOLUTE)]
    allowed = dict(zip(absolute, allowed_links(absolute))) if absolute else {}
    problems = []
    for link in links:
        if link.startswith("//"):
            problems.append(f"protocol-relative link leaves this site: {link!r}")
        elif SCHEME_RE.match(link) and not link.startswith(ABSOLUTE):
            problems.append(f"link uses a disallowed scheme: {link!r}")
        elif link in allowed:
            if not allowed[link]:
                problems.append(f"link leaves this repo's public pages: {link!r}")
        # A relative link must stay inside this site: no "../" escape, and no
        # leading "/" either -- this site is served from a project
        # subdirectory on GitHub Pages
        # (todd450.github.io/founding-agents-site/), so a root-relative link
        # such as "/about" resolves to todd450.github.io/about, outside the
        # site entirely, and 404s on the nightly link check.
        elif link.startswith(".."):
            problems.append(f"relative link escapes the site: {link!r}")
        elif link.startswith("/"):
            problems.append(f"root-relative link leaves the project site: {link!r}")
    return problems


def served_from(page, address):
    """The file, as a path from the repo root, that this relative address on
    this page asks the site for. A page at `<name>/index.html` is served from
    its own directory, so `tokens.css` on it is `<name>/tokens.css` and not
    the root's."""
    directory = Path(page).parent
    cleaned = address.split("#")[0].split("?")[0]
    return normpath(str(directory / cleaned) if str(directory) != "." else cleaned)
