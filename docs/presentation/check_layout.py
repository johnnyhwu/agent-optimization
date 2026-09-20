#!/usr/bin/env python3
"""Catch the layout faults PowerPoint will not tell you about.

Measures every run with the real Liberation Sans metrics (metric-compatible with
Arial, which is what the deck specifies), wraps each paragraph to its shape's
width, and reports any text frame that needs more height than its box has — plus
body text under the 20pt floor, slides over the ~40-word cap, and anything
crossing the 0.5in margin.

    python3 check_layout.py [deck.pptx]     # exit 1 if anything is reported
"""
import pathlib, sys
from pptx import Presentation
from pptx.util import Emu
from PIL import ImageFont

HERE = pathlib.Path(__file__).resolve().parent
DECK = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "Skill-Studio.pptx"
FONTS = pathlib.Path("/usr/share/fonts/truetype/liberation")
EMU_IN = 914400.0
MARGIN, SW, SH = 0.5, 10.0, 5.625
BODY_FLOOR, WORD_CAP = 20.0, 40
_cache = {}


SCALE = 8          # render 8x and divide, so 12.5pt is not rounded to 12px
SAFETY = 1.015     # PowerPoint's hinting is not PIL's; leave a little room


def font(pt, bold=False, mono=False):
    key = (round(pt * SCALE), bold, mono)
    if key not in _cache:
        name = ("LiberationMono" if mono else "LiberationSans") + ("-Bold" if bold else "-Regular")
        _cache[key] = ImageFont.truetype(str(FONTS / f"{name}.ttf"), max(1, round(pt * SCALE)))
    return _cache[key]


def width_pt(text, pt, bold=False, mono=False):
    return font(pt, bold, mono).getlength(text) / SCALE * SAFETY


def wrap_lines(runs, width_in):
    """Greedy word wrap across runs that share a paragraph. Returns line count."""
    limit = width_in * 72.0
    lines, x = 1, 0.0
    for text, pt, bold, mono in runs:
        for chunk in text.split("\n"):
            if chunk is not text.split("\n")[0]:
                lines += 1
                x = 0.0
            for i, word in enumerate(chunk.split(" ")):
                if not word and i == 0:
                    continue
                piece = (" " if x > 0 else "") + word
                w = width_pt(piece, pt, bold, mono)
                if x + w > limit and x > 0:
                    lines += 1
                    x = width_pt(word, pt, bold, mono)
                else:
                    x += w
    return lines


def check(deck):
    prs = Presentation(deck)
    found = []
    for n, slide in enumerate(prs.slides, 1):
        words = 0
        for shape in slide.shapes:
            L, T = shape.left / EMU_IN, shape.top / EMU_IN
            W, H = shape.width / EMU_IN, shape.height / EMU_IN
            if L < -0.01 or T < -0.01 or L + W > SW + 0.01 or T + H > SH + 0.01:
                found.append((n, "off-slide", f"{shape.shape_type}, "
                                              f"{L:.2f},{T:.2f} {W:.2f}x{H:.2f}"))
            if not shape.has_text_frame or not shape.text_frame.text.strip():
                continue
            tf = shape.text_frame
            is_title = shape.name == "action-title"
            inner = W - (tf.margin_left + tf.margin_right) / EMU_IN
            need = 0.0
            last = tf.paragraphs[-1]
            for p in tf.paragraphs:
                runs = []
                for r in p.runs:
                    pt = r.font.size.pt if r.font.size else 18.0
                    mono = (r.font.name or "").lower().startswith(("consol", "courier"))
                    runs.append((r.text, pt, bool(r.font.bold), mono))
                    if pt >= BODY_FLOOR - 4 and not is_title:
                        words += len(r.text.split())
                if not runs:
                    continue
                biggest = max(pt for _, pt, _, _ in runs)
                spacing = p.line_spacing if isinstance(p.line_spacing, float) else 1.0
                nlines = wrap_lines(runs, inner)
                need += nlines * biggest * 1.2 * spacing / 72.0
                need += (p.space_before.pt if p.space_before else 0) / 72.0
                if p is not last:      # trailing space_after pushes nothing off screen
                    need += (p.space_after.pt if p.space_after else 0) / 72.0
            box = H - (tf.margin_top + tf.margin_bottom) / EMU_IN
            if need > box + 0.02:
                found.append((n, "overflows", f'needs {need:.2f}" in {box:.2f}" — '
                                              f'"{tf.text.strip()[:58]}"'))
        if words > WORD_CAP:
            found.append((n, "wordy", f"{words} body words (cap {WORD_CAP})"))
    return found


if __name__ == "__main__":
    issues = check(DECK)
    for n, kind, detail in sorted(issues):
        print(f"slide {n:>2}  {kind:<10} {detail}")
    print(f"\n{len(issues)} finding(s)" if issues else "\nclean")
    sys.exit(1 if issues else 0)
