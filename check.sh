#!/bin/sh
# The site's one command for every check. CI runs exactly this on every pull
# request and on main (.github/workflows/ci.yml), so nothing merges unless all
# of it passes; run it yourself before you push.
#
#   sh ./check.sh
#
# The tools are development-only. Nothing they need ships to the page, which
# stays plain HTML and CSS.
set -eu

cd "$(dirname "$0")"

echo "==> unit tests: this site's own rules, offline"
python3 -m unittest discover -s tests

echo "==> the generator: every committed page is a fresh build"
# The pages are generated output committed at the repo root (#28, P4 on #14), so
# the diff a reviewer reads is the bytes a reader gets. This fails when a page
# is not what `npm run build` writes -- edited by hand, or left behind by a
# change to a template, the copy or the facts snapshot -- and names the file.
# It writes nothing, so a stale page cannot be hidden by the check that found
# it.
#
# It also prints the pages the generator is responsible for, one per line, so
# everything below checks whatever the site serves today instead of a list of
# names kept up to date by hand. The paths carry no spaces: they are
# templates/pages/<name>.html turned into <name>/index.html.
PAGES=$(node scripts/generate.js --check)
echo "$PAGES" | sed 's/^/    /'

echo "==> npm ci: the pinned checking tools"
npm ci

echo "==> markdown-it: what a renderer makes of every fact value the puller accepts"
# The oracle for scripts/pull-facts.js, which reads a fact's text with Node's
# standard library alone: a real CommonMark + GFM renderer renders every text
# that gate accepts and must find no off-site href and no bracket outside a
# <code> element.
node tests/render_facts.js

echo "==> html-validate: every page the site serves is valid, well-formed HTML"
# shellcheck disable=SC2086 -- one argument per page, which is the point
npx --no-install html-validate $PAGES

echo "==> html-validate: a page rendered from each empty-and-one snapshot"
# The same pages with no rows, one row and far more rows than the site carries
# today (fixtures/empty-and-one): every list on the page grows and empties on
# its own, so a page that is only valid at today's counts is a page that breaks
# on the next pull. Rendered into a scratch directory of this run's own and
# thrown away.
RENDERED=$(mktemp -d)
trap 'rm -rf "$RENDERED"' EXIT
for SNAPSHOT in fixtures/empty-and-one/*.json; do
  SCENARIO="$RENDERED/$(basename "$SNAPSHOT" .json)"
  mkdir -p "$SCENARIO"
  node scripts/generate.js --snapshot "$SNAPSHOT" --out "$SCENARIO" >/dev/null
done
# --config, because html-validate looks for its configuration beside the file
# it is checking and these are not in the repo: without it the fixture pages
# would be judged by html-validate's defaults instead of this repo's rules.
find "$RENDERED" -name '*.html' -exec \
  npx --no-install html-validate --config .htmlvalidate.json {} +

echo "==> stylelint: colour, font and spacing come only from tokens.css"
# --report-needless-disables so a stylelint-disable comment left behind after
# the CSS it excused has gone is itself a failure.
# shellcheck disable=SC2086 -- one argument per page, which is the point
npx --no-install stylelint $PAGES tokens.css --report-needless-disables

echo "==> chromium for playwright"
if [ "$(uname -s)" = "Linux" ]; then
  # On Linux the browser needs its system libraries too; on a GitHub runner
  # this is the only place they come from.
  npx --no-install playwright install --with-deps chromium
else
  npx --no-install playwright install chromium
fi

echo "==> playwright + axe: the rendered page at 390px and 1440px"
npx --no-install playwright test

echo "==> all checks passed"
