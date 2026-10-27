"""At desktop widths, the status band's head never touches step 1."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").read_text(encoding="utf-8")
TOKENS = (ROOT / "tokens.css").read_text(encoding="utf-8")


def desktop_rule(selector):
    """The declarations for `selector` inside the desktop (min-width:860px) media query."""
    media = re.search(r"@media \(min-width:860px\)\{(.*?)\}\s*</style>", PAGE, re.S).group(1)
    match = re.search(rf"{re.escape(selector)}\{{([^}}]*)\}}", media)
    return match.group(1) if match else ""


def token_px(name):
    match = re.search(rf"{re.escape(name)}\s*:\s*(\d+)px", TOKENS)
    return int(match.group(1)) if match else None


class GutterTest(unittest.TestCase):
    def test_band_has_a_non_zero_gutter_between_head_and_steps_at_desktop_width(self):
        band = desktop_rule(".band")
        gap = re.search(r"(?:column-)?gap\s*:\s*(?:0\s+)?var\((--[\w-]+)\)", band)
        self.assertIsNotNone(
            gap,
            "the desktop .band grid needs a gap (from a design token) so the head's "
            "column doesn't touch step 1",
        )
        width = token_px(gap.group(1))
        self.assertIsNotNone(width, f"token {gap.group(1)} is not defined in tokens.css")
        self.assertGreater(width, 0, "a zero-width gap is no gutter at all")


if __name__ == "__main__":
    unittest.main()
