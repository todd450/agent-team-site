"""The one rule for where a link on this site may point, in Python, and the
one reader that finds the links in a fact's text.

This page's links -- the ones written into index.html and the ones a fact
carries in content/facts.json -- may go to this repository or to its Pages
site, and nowhere else. A link anywhere else is one the nightly check cannot
vouch for, and a link into the private plugin repo answers 404, opens `red`
and freezes every merge.

scripts/pull-facts.js holds the same rule and the same reader, because it is
the gate on what enters the snapshot; tests/test_links.py runs both copies
over the same cases so they cannot drift apart.
"""

import re
from urllib.parse import urljoin, urlsplit

ALLOWED_LINK_PREFIXES = (
    "https://github.com/todd450/founding-agents-site",
    "https://todd450.github.io/founding-agents-site",
)
REPO_PREFIX, PAGES_PREFIX = ALLOWED_LINK_PREFIXES

# What may follow an allowed prefix: the plain URL characters, and no more.
# A backslash, a space, a tab or a newline is rewritten or stripped by a
# browser before it works out where a link goes, so an address carrying one
# does not say plainly where it lands.
PLAIN_URL_CHARS_RE = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*$")
# A percent escape is fine when it stands for ordinary text (?q=is%3Apr), and
# is not when it stands for a character that decides where the address goes:
# %2e is a dot, %2f a slash, %5c a backslash, and %00-%1f and %7f are the
# control characters a browser strips. A % that begins no escape at all is
# refused with them: nobody can say where it points.
ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")
SEPARATOR_ESCAPE_RE = re.compile(r"%(?:2[eEfF]|5[cC]|[01][0-9A-Fa-f]|7[fF])")
# Where the path stops: a query or a fragment ends it, and so does the end of
# the address. A dot segment needs no slash after it to walk up a level.
PATH_ENDS_RE = re.compile(r"[?#]")

# A link, as this reader can read one: [text](target), with no bracket in the
# text and no ")" in the target.
LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
# A backslash before a bracket or a parenthesis: an escape a renderer honours
# and this reader cannot see, so the two disagree about where a link ends.
ESCAPED_BRACKET_RE = re.compile(r"\\[\[\]()]")


def link_targets(text):
    """Where every link in this text points, and whether it also writes one
    this reader could not read whole.

    A renderer reads more as a link than LINK_TARGET_RE does. CommonMark says
    link text may hold balanced brackets ("[see [this]](...)"), an image may
    be wrapped in a link ("[![chart](ok.png)](...)"), and a bracket may be
    backslash-escaped. In each of those this reader finds no target at all,
    or only the inner one, so the destination a renderer would put in the
    href is a destination nothing checked -- which is how a "javascript:"
    target walks past an allowlist.

    So the reader has to account for every "](" the text contains: whatever
    is left once the links it did read are taken out must hold none, and a
    backslash-escaped bracket anywhere is a link it cannot see the end of.
    When it cannot, it says so and the caller refuses the text rather than
    vouching for a link it never read.

    Returns (targets, unreadable).
    """
    targets = []

    def taken(match):
        targets.append(match.group(1))
        return " "  # a space, so taking a link out can join no new "]("

    residue = LINK_TARGET_RE.sub(taken, text)
    unreadable = "](" in residue or ESCAPED_BRACKET_RE.search(text) is not None
    return targets, unreadable


def allowed_link(link):
    """Whether a browser following this link would land inside the allowlist.

    The question is where the link goes, not what it starts with: an address
    beginning with this repo's own and carrying a "." or ".." segment resolves
    somewhere else entirely, such as
    https://github.com/todd450/founding-agents-site/../../attacker/evil ->
    https://github.com/attacker/evil.
    """
    for prefix in ALLOWED_LINK_PREFIXES:
        if not link.startswith(prefix):
            continue
        rest = link[len(prefix):]
        # A lookalike repository whose name merely starts with this one's is
        # not this one: what follows must end the address or begin a path,
        # query or fragment.
        if rest and rest[0] not in "/?#":
            continue
        # fullmatch, not match: Python's "$" also matches before a trailing
        # newline, and a newline is a character a browser strips.
        if not PLAIN_URL_CHARS_RE.fullmatch(rest):
            return False
        if SEPARATOR_ESCAPE_RE.search(rest) or "%" in ESCAPE_RE.sub("", rest):
            return False
        path = PATH_ENDS_RE.split(rest, maxsplit=1)[0]
        if any(segment in (".", "..") for segment in path.split("/")):
            return False
        # The URL parser has the last word on where the link lands: resolving
        # the address against itself drops the query and the fragment and
        # removes any dot segment, leaving the page a browser would open.
        try:
            parts = urlsplit(link)
        except ValueError:
            return False
        if parts.scheme != "https":
            return False
        landing = urljoin(link, parts.path)
        return landing == prefix or landing.startswith(prefix + "/")
    return False
