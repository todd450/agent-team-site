"""The one rule for where a link on this site may point, in Python.

This page's links -- the ones written into index.html and the ones a fact
carries in content/facts.json -- may go to this repository or to its Pages
site, and nowhere else. A link anywhere else is one the nightly check cannot
vouch for, and a link into the private plugin repo answers 404, opens `red`
and freezes every merge.

scripts/pull-facts.js holds the same rule, because it is the gate on what
enters the snapshot; tests/test_links.py runs both copies over the same cases
so they cannot drift apart.
"""

import re

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
        if not PLAIN_URL_CHARS_RE.match(rest):
            return False
        if SEPARATOR_ESCAPE_RE.search(rest) or "%" in ESCAPE_RE.sub("", rest):
            return False
        return not any(segment in (".", "..") for segment in rest.split("/"))
    return False
