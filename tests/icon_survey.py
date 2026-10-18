# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A running count of the pictures the hub draws as icons: emoji (the box's icons are emoji, there is no icon font),
and anything else that could weigh on a page (inline SVG, <img>, CSS url(), @font-face, icon-font classes, icons from
another site). It reads the code, so run it when icons are added or changed and compare the numbers.
It reports and never fails: it is a survey, not a guard. emoji-data.js (the picker's list of every emoji) and the
picker's own tab list in emoji.js are not icons the hub uses, so they are left out of the totals.
python3 tests/icon_survey.py            the tally
python3 tests/icon_survey.py --list     and every distinct emoji with where it is used
python3 tests/icon_survey.py --json     the same numbers as JSON, to keep and compare"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Where icons come from, by kind of file.
GROUPS = {
    "pages and scripts (web/)": [p for p in sorted((REPO / "web").rglob("*")) if p.suffix in (".html", ".js")],
    "styles (web/)": sorted((REPO / "web").rglob("*.css")),
    "manifests (apps.d, addons, toolkits)": [
        p for d in ("apps.d", "addons", "toolkits") for p in sorted((REPO / d).rglob("*.json"))
    ],
    "hub and helper code (irate_box/)": sorted((REPO / "irate_box").rglob("*.py")),
}
NOT_ICONS = {"emoji-data.js", "emoji.js"}  # the picker's data and tab list

# Emoji: Emoji_Presentation=Yes in the BMP, everything in the pictograph planes, and any symbol made an emoji by U+FE0F.
BMP_EMOJI = "⌚⌛⏩-⏳◽◾☔☕♈-♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓-❕❗➕-➗➰➿⬛⬜⭐⭕"
BASE = f"(?:[\U0001f000-\U0001faff]|[{BMP_EMOJI}]|[©®‼⁉™ℹ←-⇿⌀-⏿Ⓜ■-➿⤀-⥿⬀-⯿〰〽㊗㊙](?=️))"
PART = "[\U0001f3fb-\U0001f3ff]|️|[\U000e0020-\U000e007f]"
ONE = f"(?:[\U0001f1e6-\U0001f1ff]{{2}}|[0-9#*]️?⃣|{BASE}(?:{PART})*)"
EMOJI = re.compile(f"{ONE}(?:‍{ONE})*")

SVG = re.compile(r"<svg\b|createElementNS\([^)]*svg|\.svg\b", re.I)
IMG = re.compile(r"<img\b|\.(?:png|webp|gif|jpe?g|ico)\b", re.I)
URL = re.compile(r"url\(", re.I)
FONT = re.compile(r"@font-face", re.I)
ICONFONT = re.compile(
    r"""class=["'][^"']*\b(?:mif|fa[srbl]?|bi|mdi|material-icons|glyphicon)[ -][\w-]+|\b(?:mif|fa|bi|mdi)-[a-z0-9-]+"""
)
REMOTE = re.compile(
    r"""(?:src|href)=["']https?://[^"']*(?:icon|font|fontawesome|cdnjs|jsdelivr|unpkg|googleapis)[^"']*""", re.I
)


def strip_vs(e):
    return e.replace("️", "")


def main():
    emoji = defaultdict(lambda: defaultdict(int))  # group -> emoji -> uses
    where = defaultdict(lambda: defaultdict(set))  # group -> emoji -> files
    other = defaultdict(lambda: defaultdict(int))  # kind -> file -> hits
    for group, files in GROUPS.items():
        for f in files:
            if f.name in NOT_ICONS:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(f.relative_to(REPO))
            for m in EMOJI.finditer(text):
                e = strip_vs(m.group())
                emoji[group][e] += 1
                where[group][e].add(rel)
            for kind, rx in (
                ("inline SVG or .svg", SVG),
                ("<img> or image file", IMG),
                ("CSS url()", URL),
                ("@font-face", FONT),
                ("icon-font class (mif-, fa-, bi-, mdi-)", ICONFONT),
                ("icon or font from another site", REMOTE),
            ):
                n = len(rx.findall(text))
                if n:
                    other[kind][rel] += n
    art = [
        p for p in (REPO / "web").rglob("*") if p.suffix in (".webp", ".webm", ".png", ".svg", ".gif", ".jpg", ".ico")
    ]
    everywhere = set().union(*[set(d) for d in emoji.values()]) if emoji else set()

    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "distinct_emoji": len(everywhere),
                    "emoji_by_group": {g: {"distinct": len(d), "uses": sum(d.values())} for g, d in emoji.items()},
                    "other": {k: {"files": len(v), "hits": sum(v.values())} for k, v in other.items()},
                    "image_files_in_web": len(art),
                    "image_bytes_in_web": sum(p.stat().st_size for p in art),
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"Distinct emoji used as icons, whole hub: {len(everywhere)}")
    for g in GROUPS:
        d = emoji.get(g, {})
        print(f"  {g:42} {len(d):4} distinct  {sum(d.values()):5} uses")
    print("Other pictures and icon sources (files, hits):")
    for kind in (
        "inline SVG or .svg",
        "<img> or image file",
        "CSS url()",
        "@font-face",
        "icon-font class (mif-, fa-, bi-, mdi-)",
        "icon or font from another site",
    ):
        v = other.get(kind, {})
        print(f"  {kind:42} {len(v):4} files  {sum(v.values()):5} hits")
        if "--list" in sys.argv:
            for f, n in sorted(v.items()):
                print(f"      {f}: {n}")
    print(f"Image files under web/: {len(art)}, {sum(p.stat().st_size for p in art) / 1024:.0f} KB (art, favicon)")
    if "--list" in sys.argv:
        print("Every distinct emoji, most used first:")
        tot = defaultdict(int)
        for d in emoji.values():
            for e, n in d.items():
                tot[e] += n
        for e, n in sorted(tot.items(), key=lambda x: (-x[1], x[0])):
            files = sorted({f for g in where.values() for f in g.get(e, ())})
            print(f"  {e:8} {n:4}  {', '.join(files[:4])}{' …' if len(files) > 4 else ''}")
    return 0


sys.exit(main())
