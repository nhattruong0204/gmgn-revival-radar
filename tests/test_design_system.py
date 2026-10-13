"""Check semantic colors for readable text and actionable boundaries, plus local assets."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def luminance(color):
    rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))


def ratio(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y + 0.05) / (x + 0.05)


def test_semantic_text_and_focus_contrast():
    css = (ROOT / "web/src/design-tokens.css").read_text()
    tokens = dict(re.findall(r"--research-([\w-]+):\s*(#[0-9a-f]{6});", css))
    assert ratio(tokens["on-accent"], tokens["accent"]) >= 4.5
    for background in ("bg", "surface"):
        for foreground in ("text", "muted", "accent", "warning", "danger"):
            assert ratio(tokens[foreground], tokens[background]) >= 4.5, (foreground, background)
        for foreground in ("focus", "control-border"):
            assert ratio(tokens[foreground], tokens[background]) >= 3, (foreground, background)


def test_font_asset_is_local_licensed_and_has_variable_font_header():
    css = (ROOT / "web/src/design-tokens.css").read_text()
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert urls and all(url.startswith("/fonts/") for url in urls)
    for url in urls:
        assert (ROOT / "web/public" / url.lstrip("/")).read_bytes()[:4] == b"wOF2"
    assert "SIL OPEN FONT LICENSE" in (ROOT / "web/public/fonts/OFL-DM-Sans.txt").read_text()
