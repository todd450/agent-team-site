#!/usr/bin/env python3
"""Ops watch's site check: the GitHub Pages deploy is green and every link on the site resolves.

A failure opens one `red` issue, which stays the only one and is kept current while the check
stays red; the next green run closes it. The run itself passes either way, since the issue is the
alarm; it fails only when the check cannot run or cannot reach the issue.

The Founding Agents `setup` skill copies this file into the product. Change it in the plugin's
skills/setup/ops_watch/ and rerun setup, so every product runs the same check.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, namedtuple
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

TITLE = "Ops watch: the site is down or has broken links"
MAX_PAGES = 200
MAX_PAGE_BYTES = 2_000_000
USER_AGENT = "founding-agents-ops-watch (+https://docs.github.com/pages)"

Response = namedtuple("Response", "status kind body url reason")


def gh(*args, input=None):
    result = subprocess.run(["gh", *args], input=input, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def deploy_problems(repo, gh=gh):
    """Return the site's address and what is wrong with its latest Pages deploy."""
    code, out, err = gh("api", f"repos/{repo}/pages")
    if code != 0:
        return None, [f"GitHub Pages is not on for {repo}: {err or 'no Pages site'}"]
    pages = json.loads(out)
    url = pages.get("html_url")

    if pages.get("build_type") == "workflow":
        code, out, err = gh("api", f"repos/{repo}/deployments?environment=github-pages&per_page=1")
        deployments = json.loads(out) if code == 0 else []
        if not deployments:
            return url, ["No Pages deployment has run yet"]
        code, out, err = gh("api", f"repos/{repo}/deployments/{deployments[0]['id']}/statuses?per_page=1")
        statuses = json.loads(out) if code == 0 else []
        state = statuses[0]["state"] if statuses else "unknown"
        if state in ("error", "failure", "inactive", "unknown"):
            return url, [f"The latest Pages deployment is {state}"]
        return url, []

    code, out, err = gh("api", f"repos/{repo}/pages/builds/latest")
    if code != 0:
        return url, [f"Could not read the latest Pages build: {err}"]
    build = json.loads(out)
    if build.get("status") == "errored":
        message = (build.get("error") or {}).get("message") or "no message"
        return url, [f"The latest Pages build failed: {message}"]
    return url, []


class LinkParser(HTMLParser):
    ATTRIBUTE = {"a": "href", "link": "href", "img": "src", "script": "src", "source": "src",
                 "iframe": "src"}
    # These name a host to connect to early, not a page; the bare host often answers 404.
    HINTS = {"preconnect", "dns-prefetch"}

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and set((attrs.get("rel") or "").lower().split()) & self.HINTS:
            return
        value = attrs.get(self.ATTRIBUTE.get(tag, ""))
        if value:
            self.links.append(value.strip())


def links_in(html):
    parser = LinkParser()
    parser.feed(html)
    return parser.links


def resolve(base, link):
    url, _ = urldefrag(urljoin(base, link))
    return url if urlparse(url).scheme in ("http", "https") else None


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            kind = response.headers.get_content_type()
            body = response.read(MAX_PAGE_BYTES) if kind == "text/html" else b""
            return Response(response.status, kind, body, response.geturl(), None)
    except urllib.error.HTTPError as error:
        return Response(error.code, None, b"", url, None)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return Response(None, None, b"", url, str(getattr(error, "reason", error)))


def ok(response):
    return response.status is not None and response.status < 400


def broken_links(root, fetch=fetch, sleep=time.sleep, retry_after=10):
    """Crawl the site's own pages from root and check every link on them, once more if it fails."""
    root = root if root.endswith("/") else root + "/"
    checked = {root: fetch(root)}
    linked_from = defaultdict(set, {root: {"the site's address"}})
    queue, crawled = [root], set()

    while queue and len(crawled) < MAX_PAGES:
        page = queue.pop(0)
        if page in crawled:
            continue
        crawled.add(page)
        response = checked[page]
        if not ok(response) or response.kind != "text/html":
            continue
        for link in links_in(response.body.decode("utf-8", "replace")):
            url = resolve(response.url, link)
            if url is None:
                continue
            linked_from[url].add(page)
            if url not in checked:
                checked[url] = fetch(url)
            if url.startswith(root) and url not in crawled:
                queue.append(url)

    failed = [url for url, response in checked.items() if not ok(response)]
    if failed:
        sleep(retry_after)  # one pause for every failure, so a blip does not raise red
        for url in failed:
            checked[url] = fetch(url)

    problems = []
    for url in sorted(failed):
        response = checked[url]
        if ok(response):
            continue
        answer = f"HTTP {response.status}" if response.status else response.reason
        pages = ", ".join(sorted(linked_from[url])[:3])
        problems.append(f"{url} answers {answer} (linked from {pages})")
    return problems


def issue_body(problems, run):
    lines = ["Ops watch's site check failed:", ""]
    lines += [f"- {problem}" for problem in problems]
    lines += ["", f"Seen in {run}. This issue is kept current while the check stays red and closes "
                  "when it passes again. The integrator merges nothing while a `red` issue is open."]
    return "\n".join(lines)


def report_issue(repo, problems, run, gh=gh):
    """Open, update or close the one `red` issue. Returns False if GitHub refused."""
    code, out, err = gh("issue", "list", "--repo", repo, "--label", "red", "--state", "open",
                        "--json", "number,title", "--limit", "100")
    if code != 0 and "not found" not in err.lower():
        print(f"Could not list the red issues: {err}", file=sys.stderr)
        return False
    numbers = [str(issue["number"]) for issue in json.loads(out or "[]") if issue["title"] == TITLE]
    code = 0

    if problems:
        body = issue_body(problems, run)
        if numbers:
            code, _, err = gh("issue", "edit", numbers[0], "--repo", repo, "--body-file", "-", input=body)
        else:
            gh("label", "create", "red", "--repo", repo, "--color", "B60205",
               "--description", "A scheduled check failed; nothing merges until it is fixed")
            code, _, err = gh("issue", "create", "--repo", repo, "--title", TITLE, "--label", "red",
                              "--body-file", "-", input=body)
    else:
        for number in numbers:
            gh("issue", "comment", number, "--repo", repo, "--body", f"Green again in {run}.")
            code, _, err = gh("issue", "close", number, "--repo", repo)
            if code != 0:
                break
    if code != 0:
        print(f"GitHub refused the red issue change: {err}", file=sys.stderr)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/name")
    parser.add_argument("--url", help="check this address instead of the Pages one")
    parser.add_argument("--dry-run", action="store_true", help="print the result; leave issues alone")
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo is needed outside GitHub Actions")

    url, problems = deploy_problems(args.repo)
    url = args.url or url
    if url:
        problems += broken_links(url)

    print(f"Red: {len(problems)} problem(s)" if problems else f"Green: {url} is up and its links resolve")
    for problem in problems:
        print(f"- {problem}")

    if args.dry_run:
        return 0
    server, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_RUN_ID")
    run = f"{server}/{args.repo}/actions/runs/{run_id}" if server and run_id else "a run by hand"
    return 0 if report_issue(args.repo, problems, run) else 1


if __name__ == "__main__":
    sys.exit(main())
