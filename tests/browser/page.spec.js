// Browser checks for index.html, in a real Chromium at the mockup's two
// widths. These replace the layout tests that read the page's CSS as text and
// passed while the rendered page at 1440px had no gutter between the status
// band's verdict line and the STEP 1 column (issue #10).
//
// Everything here measures what the browser actually laid out: boxes, not
// declarations. The page is loaded over file://, so these make no network
// call.
const path = require("path");
const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

const PAGE_URL = "file://" + path.resolve(__dirname, "..", "..", "index.html");

// 390px is the mockup's phone width, 1440px its desktop width.
const WIDTHS = [
  { name: "phone", width: 390, height: 844 },
  { name: "desktop", width: 1440, height: 900 },
];

// A gutter narrower than this is not a gutter: the two columns read as one.
const MIN_GUTTER = 8;

// Every block of prose on the page: each one is capped to the --measure token,
// so none of them runs the full width of the shell while the paragraph above
// it stays capped (issue #6's review found four that did). The grids and the
// table are not prose and are meant to be wider.
const PROSE = [".lede", ".costs .col", ".record .col", ".limits-box", ".record-list li",
               ".rail", ".flow .note"];

/** The smallest box containing all of the given elements, in page coordinates. */
async function unionBox(page, selectors) {
  return page.evaluate((sels) => {
    const boxes = sels.map((sel) => {
      const el = document.querySelector(sel);
      if (!el) throw new Error(`no element matches ${sel}`);
      const r = el.getBoundingClientRect();
      return {
        left: r.left + window.scrollX,
        right: r.right + window.scrollX,
        top: r.top + window.scrollY,
        bottom: r.bottom + window.scrollY,
      };
    });
    return {
      left: Math.min(...boxes.map((b) => b.left)),
      right: Math.max(...boxes.map((b) => b.right)),
      top: Math.min(...boxes.map((b) => b.top)),
      bottom: Math.max(...boxes.map((b) => b.bottom)),
    };
  }, selectors);
}

for (const size of WIDTHS) {
  test.describe(`${size.name} (${size.width}px)`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.goto(PAGE_URL);
    });

    test("nothing overflows sideways", async ({ page }) => {
      const overflow = await page.evaluate(() => {
        const viewport = document.documentElement.clientWidth;
        const wide = [];
        for (const el of document.querySelectorAll("body *")) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.right > viewport + 1) {
            wide.push(`${el.tagName.toLowerCase()}.${el.className || "-"} right=${Math.round(r.right)}`);
          }
        }
        return { viewport, scrollWidth: document.documentElement.scrollWidth, wide };
      });
      expect(overflow.wide, "elements reaching past the right edge of the viewport").toEqual([]);
      expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.viewport);
    });

    test("the band's first column keeps a gutter before step 1", async ({ page }) => {
      // The first column is the verdict line and its dated stamp. The gutter
      // is measured to the edge of the STEP 1 *column*, not to its text: the
      // bug issue #10 fixed was the verdict line ending at the exact left
      // edge of that column, with only step 1's own padding standing in for
      // a gutter the first column never had.
      const first = await unionBox(page, [".verdict", ".stamp"]);
      const step = await unionBox(page, [".step"]);
      const sideBySide = step.left >= first.right;
      const gutter = sideBySide ? step.left - first.right : step.top - first.bottom;
      expect(
        gutter,
        `the band's first column runs into the step 1 column (laid out ${
          sideBySide ? "beside" : "above"
        } it)`
      ).toBeGreaterThanOrEqual(MIN_GUTTER);
    });

    test("the band's first column keeps a divider before step 1", async ({ page }) => {
      const first = await unionBox(page, [".verdict", ".stamp"]);
      const step = await unionBox(page, [".step"]);
      const sideBySide = step.left >= first.right;
      const divider = await page.evaluate((wantSide) => {
        const el = document.querySelector(".band-head");
        const style = getComputedStyle(el);
        const side = wantSide ? "Right" : "Bottom";
        const box = el.getBoundingClientRect();
        return {
          width: parseFloat(style[`border${side}Width`]),
          style: style[`border${side}Style`],
          color: style[`border${side}Color`],
          // Where the browser drew it, in page coordinates.
          at: wantSide
            ? box.right + window.scrollX - parseFloat(style.borderRightWidth) / 2
            : box.bottom + window.scrollY - parseFloat(style.borderBottomWidth) / 2,
        };
      }, sideBySide);

      expect(divider.width, "the first column has no divider before step 1").toBeGreaterThanOrEqual(1);
      expect(divider.style).not.toBe("none");
      expect(divider.color).not.toBe("rgba(0, 0, 0, 0)");
      // The divider has to fall in the gutter -- between the two columns --
      // not somewhere else on the band.
      if (sideBySide) {
        expect(divider.at).toBeGreaterThan(first.right);
        expect(divider.at).toBeLessThanOrEqual(step.left);
      } else {
        expect(divider.at).toBeGreaterThan(first.bottom);
        expect(divider.at).toBeLessThanOrEqual(step.top);
      }
    });

    test("the dated stamp stays with the verdict line", async ({ page }) => {
      // Stretching the first column so its divider runs the full height of
      // the steps beside it leaves it a column of free space, and the phone
      // layout's space-between would spread the verdict line and the stamp
      // over it, dropping the stamp to the foot of the band (issue #10). The
      // two are a gap apart, nothing more, so the column's own gap is the
      // bar -- no pixel figure of this test's own.
      const verdict = await unionBox(page, [".verdict"]);
      const stamp = await unionBox(page, [".stamp"]);
      const gap = await page.evaluate(() =>
        parseFloat(getComputedStyle(document.querySelector(".band-head")).rowGap));
      expect(stamp.top - verdict.bottom,
             "the stamp has been pushed away from the verdict line")
        .toBeLessThanOrEqual(gap + 1);
    });

    test("every block of prose is capped to the measure", async ({ page }) => {
      const tooWide = await page.evaluate((selectors) => {
        const token = getComputedStyle(document.documentElement)
          .getPropertyValue("--measure").trim();
        if (!/^[\d.]+em$/.test(token)) {
          throw new Error(`--measure is "${token}", which this check cannot read as em`);
        }
        const measure = parseFloat(token);
        const wide = [];
        for (const selector of selectors) {
          const found = document.querySelectorAll(selector);
          if (!found.length) throw new Error(`no element matches ${selector}`);
          found.forEach((el, index) => {
            // --measure is in em, so each block's cap is in its own type size.
            const cap = measure * parseFloat(getComputedStyle(el).fontSize);
            const width = el.getBoundingClientRect().width;
            if (width > cap + 1) {
              wide.push(`${selector}[${index}] is ${Math.round(width)}px wide, measure is ${Math.round(cap)}px`);
            }
          });
        }
        return wide;
      }, PROSE);
      expect(tooWide, "prose running wider than the measure").toEqual([]);
    });

    test("axe finds no serious or critical accessibility violation", async ({ page }) => {
      const results = await new AxeBuilder({ page }).analyze();
      const bad = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.impact}: ${v.id} (${v.nodes.length} node(s)) ${v.helpUrl}`);
      expect(bad, "axe violations").toEqual([]);
    });
  });
}
