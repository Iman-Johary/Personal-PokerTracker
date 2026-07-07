"""Offline importer: read solver range screenshots and write tools/screenshot_ranges.py.

Requires Pillow (dev-only; NOT imported by the analysis pipeline). The screenshots
live in "screenshots/", sorted by filename = capture order. That order is,
by construction (see project notes):
  0-4  : RFI for UTG, HJ, CO, BTN, SB
  5-9  : facing an open, opener=UTG, hero = HJ, CO, BTN, SB, BB
  10-13: opener=HJ, hero = CO, BTN, SB, BB
  14-16: opener=CO, hero = BTN, SB, BB
  17-18: opener=BTN, hero = SB, BB
  19   : opener=SB, hero = BB
  20-54: facing a 3-bet (35 shots). For each opener o (UTG..SB) and each
         3-bettor t seated after o, the deciders in capture order are the cold
         seats after t (in seat order) and then the opener o itself acting again.
         So opener=UTG/3bet=HJ -> CO,BTN,SB,BB,UTG ; opener=UTG/3bet=CO ->
         BTN,SB,BB,UTG ; ... ; opener=SB/3bet=BB -> SB.  A decider equal to the
         opener is the "opener facing a 3-bet" chart (has a flat-call option);
         the cold ones are 4-bet-or-fold.
Colours: red=raise/3bet/4bet, dark red=all-in, green=call, blue=fold.

Run:  python3 tools/screenshot_import.py   (regenerates tools/screenshot_ranges.py)
"""
import glob, os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = sorted(glob.glob(os.path.join(ROOT, "screenshots", "*.png")))
RANKS = "AKQJT98765432"

# file index -> spot.  ("RFI", pos) or ("VS", hero, opener)
SPOTS = [
    ("RFI", "UTG"), ("RFI", "HJ"), ("RFI", "CO"), ("RFI", "BTN"), ("RFI", "SB"),
    ("VS", "HJ", "UTG"), ("VS", "CO", "UTG"), ("VS", "BTN", "UTG"), ("VS", "SB", "UTG"), ("VS", "BB", "UTG"),
    ("VS", "CO", "HJ"), ("VS", "BTN", "HJ"), ("VS", "SB", "HJ"), ("VS", "BB", "HJ"),
    ("VS", "BTN", "CO"), ("VS", "SB", "CO"), ("VS", "BB", "CO"),
    ("VS", "SB", "BTN"), ("VS", "BB", "BTN"),
    ("VS", "BB", "SB"),
]

# Facing a 3-bet: opener o, 3-bettor t (seated after o). Deciders in capture
# order = cold seats after t (seat order), then the opener o acting again.
_POS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]
for _oi, _o in enumerate(_POS):
    for _ti in range(_oi + 1, len(_POS)):
        _t = _POS[_ti]
        for _di in range(_ti + 1, len(_POS)):   # cold deciders after the 3-bettor
            SPOTS.append(("VS3", _POS[_di], _o, _t))
        SPOTS.append(("VS3", _o, _o, _t))        # opener faces the 3-bet

def label(i, j):
    hi, lo = RANKS[i], RANKS[j]
    return hi + lo if i == j else (hi + lo + "s" if i < j else lo + hi + "o")

def classify(r, g, b):
    if r > g + 45 and r > b + 40:            # reddish
        return "a" if r < 190 else "r"       # dark red = all-in
    if g > r and g >= b - 8 and g > 115:     # green
        return "c"
    if b > 115 and b > g - 10 and b > r:     # blue
        return "f"
    return None

def parse(path):
    im = Image.open(path).convert("RGB"); px = im.load(); W, H = im.size
    isc = lambda p: classify(*p) is not None
    x = int(W * 0.05); top = bot = None
    for y in range(int(H * 0.10), H):
        if isc(px[x, y]): top = y; break
    for y in range(H - 1, int(H * 0.5), -1):
        if isc(px[x, y]): bot = y; break
    ymid = (top + bot) // 2; left = right = None
    for xx in range(0, W):
        if isc(px[xx, ymid]): left = xx; break
    for xx in range(W - 1, 0, -1):
        if isc(px[xx, ymid]): right = xx; break
    cw = (right - left) / 13.0; ch = (bot - top) / 13.0
    out = {}
    for i in range(13):
        for j in range(13):
            x0 = int(left + j * cw); x1 = int(left + (j + 1) * cw)
            y0 = int(top + i * ch); y1 = int(top + (i + 1) * ch)
            mx = int(cw * 0.1); my = int(ch * 0.12)
            cnt = {"r": 0, "c": 0, "f": 0, "a": 0}; tot = 0
            for yy in range(y0 + my, y1 - my, 2):
                for xx in range(x0 + mx, x1 - mx, 2):
                    k = classify(*px[xx, yy])
                    if k: cnt[k] += 1; tot += 1
            if tot == 0:
                continue
            out[label(i, j)] = {k: 100.0 * cnt[k] / tot for k in cnt}
    return out

def clean(d):
    """Drop <4% noise, renormalise to integers summing to 100."""
    d = {k: v for k, v in d.items() if v >= 4}
    s = sum(d.values())
    if s <= 0:
        return {}
    d = {k: round(100 * v / s) for k, v in d.items()}
    # fix rounding to sum 100
    diff = 100 - sum(d.values())
    if diff and d:
        mk = max(d, key=d.get); d[mk] += diff
    return d

def _split(grid):
    out = {}
    for h, v in grid.items():
        c = clean(v)
        if c.get("f", 0) >= 100:   # pure fold -> omit (absent = fold)
            continue
        out[h] = {"r": c.get("r", 0), "c": c.get("c", 0), "f": c.get("f", 0), "a": c.get("a", 0)}
    return out

RFI = {}
VSOPEN = {}
VS3BET = {}
for path, spot in zip(SHOTS, SPOTS):
    grid = parse(path)
    if spot[0] == "RFI":
        RFI[spot[1]] = _split(grid)
    elif spot[0] == "VS":
        _, hero, opener = spot
        VSOPEN.setdefault(hero, {})[opener] = _split(grid)
    else:   # VS3: (hero, opener, tbettor)
        _, hero, opener, tbet = spot
        VS3BET.setdefault(hero, {}).setdefault(opener, {})[tbet] = _split(grid)

with open(os.path.join(HERE, "screenshot_ranges.py"), "w", encoding="utf-8") as fp:
    fp.write('"""AUTO-GENERATED by tools/screenshot_import.py from solver range screenshots.\n')
    fp.write('Do not edit by hand; re-run the importer to refresh.\n')
    fp.write('SHOT_RFI[pos] = {hand: {r,c,f,a}}.  SHOT_VSOPEN[hero][opener] = {hand: {r,c,f,a}}.\n')
    fp.write('SHOT_VS3BET[hero][opener][tbettor] = {hand: {r,c,f,a}} (hero==opener = opener facing a 3-bet).\n"""\n\n')
    fp.write("SHOT_RFI = {\n")
    for pos in ("UTG", "HJ", "CO", "BTN", "SB"):
        fp.write("    %r: %r,\n" % (pos, RFI.get(pos, {})))
    fp.write("}\n\n")
    fp.write("SHOT_VSOPEN = {\n")
    for hero in VSOPEN:
        fp.write("    %r: {\n" % hero)
        for opener in VSOPEN[hero]:
            fp.write("        %r: %r,\n" % (opener, VSOPEN[hero][opener]))
        fp.write("    },\n")
    fp.write("}\n\n")
    fp.write("SHOT_VS3BET = {\n")
    for hero in VS3BET:
        fp.write("    %r: {\n" % hero)
        for opener in VS3BET[hero]:
            fp.write("        %r: {\n" % opener)
            for tbet in VS3BET[hero][opener]:
                fp.write("            %r: %r,\n" % (tbet, VS3BET[hero][opener][tbet]))
            fp.write("        },\n")
        fp.write("    },\n")
    fp.write("}\n")
print("wrote tools/screenshot_ranges.py")
