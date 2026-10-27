# founding-agents-site

The website for [Founding Agents](https://github.com/todd450/founding-agents), built by the team as its first product and its test bed. It is served at https://todd450.github.io/founding-agents-site/.

- **The page** is `index.html`, with its CSS inline. Colour, font and spacing come only from `tokens.css`. How the site is built and deployed is being decided in #14.
- **Checks:** `sh ./check.sh` runs every check: the unit tests in `tests/`, html-validate, stylelint, and Playwright with axe at phone and desktop widths. CI runs it on every pull request, and `main` merges nothing that fails it. The npm packages are for the checks only; none of them ships to the page.
- **Ops watch** checks the Pages deploy and every link each night, and opens a `red` issue when one fails.
- **How changes arrive:** an idea goes through `/founding-agents:propose` to a `needs-founder` issue. Once the founder adds `decided`, `/founding-agents:build` builds it, and the integrator merges it.
