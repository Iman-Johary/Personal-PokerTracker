"""
6-max 100bb GTO preflop reference ranges.

Primary source is a solver-derived chart pack (pekarstas GGPoker pack, imported in
tools/gto_data.py). Where the pack lacks a spot, we fall back to the older
hand-authored approximations kept at the bottom of this file.

Position names: this project uses HJ; the chart pack calls that seat MP. We map
HJ<->MP transparently. Mixed cells (e.g. raise ~half the time) are tracked so the
classifier can avoid flagging a borderline hand as a mistake.
"""
from gto_data import CHARTS
from screenshot_ranges import SHOT_RFI, SHOT_VSOPEN

RANKS = "AKQJT98765432"
RANK_VAL = {r: i for i, r in enumerate(RANKS)}

# project position name -> chart-pack name
_TO_PACK = {"HJ": "MP"}
def _pack(p): return _TO_PACK.get(p, p)


# ---- cell helpers -----------------------------------------------------------
def _is_mix(cell):
    return isinstance(cell, list)

def _aggressive(cell):
    """Cell involves a raise/allin (open, 3-bet or 4-bet depending on scenario)."""
    if _is_mix(cell):
        return any(a in ("raise", "allin") for a in cell)
    return cell in ("raise", "allin")

def _in_range(cell):
    """Cell is 'in range' at all (any non-fold action present)."""
    if _is_mix(cell):
        return any(a != "fold" for a in cell)
    return cell != "fold"


def _chart(hero, scenario, villain=None):
    key = f"{_pack(hero)}-{scenario}" + (f"-{_pack(villain)}" if villain else "")
    return CHARTS.get(key)


# ---------------------------------------------------------------------------
# Screenshot-derived reference ranges (Cash 100bb, 6-max NL25, "with cold calls 2.5x"),
# read from range screenshots in "screenshots/" via tools/screenshot_import.py.
# Value = raise frequency %. A hand absent from a position's dict is a pure fold.
# When a position appears here it OVERRIDES the pekarstas pack for that RFI spot.
#   raise% >= SHOT_RAISE_TH -> pure open (folding it is flagged too_tight)
#   SHOT_MIX_TH <= raise% < SHOT_RAISE_TH -> mixed (raise OR fold both fine)
#   raise% < SHOT_MIX_TH -> fold (raising it is flagged too_loose)
# ---------------------------------------------------------------------------
SHOT_RAISE_TH = 85
SHOT_MIX_TH = 8
# Facing an open: fold% < VS_FOLD_HI -> "defend" set; fold% in [VS_FOLD_LO, VS_FOLD_HI]
# -> mix (neither folding nor defending flagged); fold% > VS_FOLD_HI -> fold.
VS_FOLD_LO = 35
VS_FOLD_HI = 65


# ---- RFI (raise-first-in) ---------------------------------------------------
def _build_rfi():
    # ranges = pure open (folding is a leak); mixes = played but not pure raise
    # (raise/limp/fold all fine); limps = GTO completes/limps (SB) -> a limp is OK.
    # SHOT_RFI values are {r,c,f,a} splits.
    ranges, mixes, limps = {}, {}, {}
    for pos in ("UTG", "HJ", "CO", "BTN", "SB"):
        if pos in SHOT_RFI:  # screenshot-derived override
            fr = SHOT_RFI[pos]
            rp = lambda v: v.get("r", 0) + v.get("a", 0)
            played = lambda v: 100 - v.get("f", 0)
            ranges[pos] = {h for h, v in fr.items() if rp(v) >= SHOT_RAISE_TH}
            mixes[pos] = {h for h, v in fr.items()
                          if rp(v) < SHOT_RAISE_TH and played(v) >= SHOT_MIX_TH}
            limps[pos] = {h for h, v in fr.items() if v.get("c", 0) >= SHOT_MIX_TH}
            continue
        ch = _chart(pos, "RFI")
        if ch:
            ranges[pos] = {h for h, c in ch.items() if _aggressive(c)}
            mixes[pos] = {h for h, c in ch.items() if _aggressive(c) and _is_mix(c)}
        else:  # fallback
            ranges[pos] = expand(_LEGACY_RFI[pos]); mixes[pos] = set()
        limps[pos] = set()
    return ranges, mixes, limps


def hand_to_169(c1, c2):
    r1, s1 = c1[0], c1[1]; r2, s2 = c2[0], c2[1]
    if RANK_VAL[r1] > RANK_VAL[r2]:
        r1, s1, r2, s2 = r2, s2, r1, s1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def gto_action(position, hand169):
    """RAISE (pure open), MIX (part-time open) or FOLD."""
    if hand169 in RFI_MIX.get(position, set()):
        return "MIX"
    return "RAISE" if hand169 in RANGES.get(position, set()) else "FOLD"


# ---- facing a single open ---------------------------------------------------
def defend_range(hero_pos, opener_pos):
    """(defend_set, threebet_set) for Hero facing exactly one open."""
    g = SHOT_VSOPEN.get(hero_pos, {}).get(opener_pos)
    if g:
        defend = {h for h, v in g.items() if v.get("f", 0) < VS_FOLD_HI}
        three = {h for h, v in g.items() if v.get("r", 0) + v.get("a", 0) >= 50}
        return defend, three
    ch = _chart(hero_pos, "vs-open", opener_pos)
    if ch:
        defend = {h for h, c in ch.items() if _in_range(c)}
        three = {h for h, c in ch.items() if _aggressive(c)}
        return defend, three
    return _legacy_defend(hero_pos, opener_pos)


def defend_mix(hero_pos, opener_pos):
    """Borderline hands the solver plays only part of the time (don't flag these)."""
    g = SHOT_VSOPEN.get(hero_pos, {}).get(opener_pos)
    if g:
        return {h for h, v in g.items() if VS_FOLD_LO <= v.get("f", 0) <= VS_FOLD_HI}
    ch = _chart(hero_pos, "vs-open", opener_pos)
    if ch:
        return {h for h, c in ch.items() if _is_mix(c)}
    return set()


# ---- facing a 3-bet ---------------------------------------------------------
def threebet_defense(hero_pos, tbet_pos, hero_opened):
    """(continue_set, fourbet_set) for Hero facing a single 3-bet."""
    if hero_opened:
        ch = _chart(hero_pos, "vs-3bet", tbet_pos)
        if ch:
            cont = {h for h, c in ch.items() if _in_range(c)}
            four = {h for h, c in ch.items() if _aggressive(c)}
            return cont, four
    else:
        return _CONT_COLD, _4BET
    # opened but pack missing this pair -> legacy IP/OOP model
    cont = _CONT_IP if _hero_ip(hero_pos, tbet_pos) else _CONT_OOP
    return cont, _4BET


def cont_mix(hero_pos, tbet_pos, hero_opened):
    if hero_opened:
        ch = _chart(hero_pos, "vs-3bet", tbet_pos)
        if ch:
            return {h for h, c in ch.items() if _is_mix(c)}
    return set()


# ===========================================================================
# LEGACY hand-authored approximations (fallback only)
# ===========================================================================
def _expand_token(tok):
    tok = tok.strip(); hands = set()
    plus = tok.endswith("+"); core = tok[:-1] if plus else tok
    if len(core) == 2 and core[0] == core[1]:
        start = RANK_VAL[core[0]]
        if plus:
            for v in range(0, start + 1):
                hands.add(RANKS[v] + RANKS[v])
        else:
            hands.add(core)
        return hands
    hi, lo, suit = core[0], core[1], core[2]
    hi_v, lo_v = RANK_VAL[hi], RANK_VAL[lo]
    if plus:
        for v in range(lo_v, hi_v, -1):
            hands.add(hi + RANKS[v] + suit)
    else:
        hands.add(hi + lo + suit)
    return hands


def expand(tokens):
    out = set()
    for t in tokens:
        out |= _expand_token(t)
    return out


_LEGACY_RFI = {
    "UTG": ["22+","A2s+","K9s+","Q9s+","J9s+","T8s+","97s+","87s","76s","65s","54s","ATo+","KJo+","QJo"],
    "HJ": ["22+","A2s+","K8s+","Q8s+","J8s+","T7s+","96s+","86s+","75s+","64s+","54s","A9o+","KTo+","QTo+","JTo"],
    "CO": ["22+","A2s+","K5s+","Q7s+","J7s+","T7s+","96s+","85s+","74s+","64s+","53s+","43s","A8o+","K9o+","Q9o+","J9o+","T9o"],
    "BTN": ["22+","A2s+","K2s+","Q4s+","J6s+","T6s+","95s+","85s+","74s+","64s+","53s+","43s","32s","A2o+","K7o+","Q8o+","J8o+","T8o+","98o","87o","76o","65o"],
    "SB": ["22+","A2s+","K2s+","Q5s+","J7s+","T7s+","96s+","86s+","75s+","65s","54s","A2o+","K9o+","Q9o+","J9o+","T9o"],
}

_V3_VS_EARLY = expand(["TT+","AQs+","AKo","A5s","A4s"])
_V3_VS_LATE = expand(["99+","AJs+","ATs","KQs","AQo+","KJs","A5s","A4s","A3s"])
_IP_CALL_EARLY = expand(["22","33","44","55","66","77","88","99","ATs","AJs","KJs","KQs","QJs","JTs","T9s","98s","AQo"])
_IP_CALL_LATE = expand(["22","33","44","55","66","77","88","99","TT","JJ","ATs","AJs","AQs","KTs","KJs","KQs","QTs","QJs","J9s","JTs","T9s","98s","87s","76s","AJo","AQo","KQo"])
_BB_CALL_EARLY = expand(["22","33","44","55","66","77","88","99","ATs","AJs","KJs","KQs","QJs","QTs","JTs","T9s","98s","76s","AQo","KQo"])
_BB_CALL_LATE = expand(["22","33","44","55","66","77","88","99","TT","A2s+","K7s+","Q8s+","J8s+","T8s+","97s+","86s+","75s+","65s","54s","A8o+","KTo+","QTo+","JTo","T9o"])


def _legacy_defend(hero_pos, opener_pos):
    early = opener_pos in ("UTG", "HJ")
    v3 = _V3_VS_EARLY if early else _V3_VS_LATE
    if hero_pos == "SB":
        call = set()
    elif hero_pos == "BB":
        call = _BB_CALL_EARLY if early else _BB_CALL_LATE
    else:
        call = _IP_CALL_EARLY if early else _IP_CALL_LATE
    return (v3 | call), v3


_4BET = expand(["QQ+", "AKs", "AKo", "A5s"])
_CONT_IP = expand(["55+", "ATs+", "KJs+", "QJs", "JTs", "T9s", "98s", "AJo+", "KQo"]) | _4BET
_CONT_OOP = expand(["99+", "AJs+", "KQs", "AQo+", "KQo"]) | _4BET
_CONT_COLD = expand(["TT+", "AQs+", "AKo", "AJs", "KQs"]) | _4BET
_POSTFLOP_RANK = {"SB": 0, "BB": 1, "UTG": 2, "HJ": 3, "CO": 4, "BTN": 5}
def _hero_ip(hero_pos, tbet_pos):
    return _POSTFLOP_RANK.get(hero_pos, 0) > _POSTFLOP_RANK.get(tbet_pos, 0)


# ---------------------------------------------------------------------------
# Per-cell action split (percentages) for the dashboard grids. The pekarstas
# pack only encodes pure actions (100%) or an even two-way mix (50/50); it has no
# finer frequencies, so these are the discrete values the pack provides.
# ---------------------------------------------------------------------------
_ACT = {"raise": "r", "call": "c", "fold": "f", "allin": "a"}

def cell_split(cell):
    """Return {'r','c','f','a'} percentages for a chart cell, or None for fold."""
    if cell is None:
        return None
    d = {"r": 0, "c": 0, "f": 0, "a": 0}
    if isinstance(cell, list):
        share = 100 // len(cell)
        for a in cell:
            d[_ACT[a]] += share
        rem = 100 - sum(d.values())
        if rem:
            d[_ACT[cell[0]]] += rem
    else:
        d[_ACT[cell]] = 100
    return d


def rfi_split(pos):
    if pos in SHOT_RFI:  # full {r,c,f,a} split per hand (SB has limp = call)
        return {h: {"r": v.get("r", 0), "c": v.get("c", 0),
                    "f": v.get("f", 0), "a": v.get("a", 0)}
                for h, v in SHOT_RFI[pos].items()}
    ch = _chart(pos, "RFI")
    if ch:
        out = {}
        for h, c in ch.items():
            s = cell_split(c)
            if s:
                out[h] = s
        return out
    return {h: {"r": 100, "c": 0, "f": 0, "a": 0} for h in RANGES.get(pos, set())}


def vsopen_split(hero_pos, opener_pos):
    g = SHOT_VSOPEN.get(hero_pos, {}).get(opener_pos)
    if g:
        return {h: dict(v) for h, v in g.items()}
    ch = _chart(hero_pos, "vs-open", opener_pos)
    if ch:
        return {h: cell_split(c) for h, c in ch.items() if cell_split(c)}
    defend, three = _legacy_defend(hero_pos, opener_pos)
    out = {}
    for h in defend:
        out[h] = {"r": 100, "c": 0, "f": 0, "a": 0} if h in three else {"r": 0, "c": 100, "f": 0, "a": 0}
    return out


def tb_split(hero_pos, tbet_pos):
    ch = _chart(hero_pos, "vs-3bet", tbet_pos)
    if ch:
        return {h: cell_split(c) for h, c in ch.items() if cell_split(c)}
    cont = _CONT_IP if _hero_ip(hero_pos, tbet_pos) else _CONT_OOP
    out = {}
    for h in cont:
        out[h] = {"r": 100, "c": 0, "f": 0, "a": 0} if h in _4BET else {"r": 0, "c": 100, "f": 0, "a": 0}
    return out


RANGES, RFI_MIX, RFI_LIMP = _build_rfi()
