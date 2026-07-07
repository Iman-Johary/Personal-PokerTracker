"""
Postflop helpers for the replayer: a correct pure-stdlib hand evaluator, made-hand
labels, board-texture analysis, and Monte-Carlo equity of Hero vs an estimated
villain range. No third-party dependencies (works on any Python 3).

Design note: an optional faster/exact evaluator (e.g. pokerkit) can be dropped in
behind evaluate7() without touching callers; the stdlib path is the default so the
tool always runs.
"""
import random
from itertools import combinations
from collections import Counter

RANKS = "23456789TJQKA"
RVAL = {r: i + 2 for i, r in enumerate(RANKS)}  # 2..14
SUITS = "cdhs"
CAT_NAMES = ["high card", "pair", "two pair", "three of a kind", "straight",
             "flush", "full house", "four of a kind", "straight flush"]


def _card(c):  # "As" -> (14,'s')
    return RVAL[c[0]], c[1]


def _score5(cards):
    """Rank a 5-card hand -> (category, tiebreakers). Higher tuple = stronger."""
    vals = sorted((v for v, _ in cards), reverse=True)
    s0 = cards[0][1]
    flush = cards[1][1] == s0 and cards[2][1] == s0 and cards[3][1] == s0 and cards[4][1] == s0
    cc = {}
    for v in vals:
        cc[v] = cc.get(v, 0) + 1
    uniq = sorted(cc, reverse=True)
    straight_hi = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_hi = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight_hi = 5
    groups = sorted(cc.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape0 = groups[0][1]
    shape1 = groups[1][1] if len(groups) > 1 else 0
    if straight_hi and flush:
        return (8, straight_hi)
    if shape0 == 4:
        quad = groups[0][0]
        return (7, quad, max(v for v in vals if v != quad))
    if shape0 == 3 and shape1 >= 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5, vals[0], vals[1], vals[2], vals[3], vals[4])
    if straight_hi:
        return (4, straight_hi)
    if shape0 == 3:
        trip = groups[0][0]
        ks = [v for v in vals if v != trip]
        return (3, trip, ks[0], ks[1])
    if shape0 == 2 and shape1 == 2:
        hp, lp = groups[0][0], groups[1][0]
        return (2, hp, lp, max(v for v in vals if v != hp and v != lp))
    if shape0 == 2:
        pair = groups[0][0]
        ks = [v for v in vals if v != pair]
        return (1, pair, ks[0], ks[1], ks[2])
    return (0, vals[0], vals[1], vals[2], vals[3], vals[4])


def evaluate7(cards):
    """Best 5-of-N score for a list of 'As'-style cards (5..7 cards)."""
    pc = [_card(c) for c in cards]
    if len(pc) == 5:
        return _score5(pc)
    return max(_score5(list(combo)) for combo in combinations(pc, 5))


def made_hand(hole, board):
    """Human label for Hero's current best hand, e.g. 'two pair'."""
    cards = list(hole) + list(board)
    if len(cards) < 5:
        return None
    return CAT_NAMES[evaluate7(cards)[0]]


def board_texture(board):
    """Descriptor for a 3-5 card board."""
    if len(board) < 3:
        return None
    vals = sorted((RVAL[c[0]] for c in board), reverse=True)
    suits = [c[1] for c in board]
    from collections import Counter
    sc = Counter(suits); vc = Counter(vals)
    top_suit = max(sc.values())
    if top_suit >= 3:
        flush = "monotone"
    elif top_suit == 2:
        flush = "two-tone"
    else:
        flush = "rainbow"
    uniq = sorted(set(vals))
    gaps = min((uniq[i + 1] - uniq[i]) for i in range(len(uniq) - 1)) if len(uniq) > 1 else 9
    span = uniq[-1] - uniq[0] if len(uniq) > 1 else 0
    if len(uniq) >= 3 and span <= 4:
        conn = "connected"
    elif gaps <= 2:
        conn = "semi-connected"
    else:
        conn = "disconnected"
    tags = []
    if max(vc.values()) >= 2:
        tags.append("paired")
    tags.append(flush)
    tags.append(conn)
    return ", ".join(tags)


# ---- equity ----------------------------------------------------------------
def _combos(hand169, dead):
    """All 2-card combos for a 169-hand, minus dead cards. hand169 like 'AKs'/'QQ'/'T9o'."""
    r1, r2 = hand169[0], hand169[1]
    suited = hand169.endswith("s"); pair = (r1 == r2)
    out = []
    if pair:
        for a, b in combinations(SUITS, 2):
            c1, c2 = r1 + a, r1 + b
            if c1 not in dead and c2 not in dead:
                out.append((c1, c2))
    elif suited:
        for s in SUITS:
            c1, c2 = r1 + s, r2 + s
            if c1 not in dead and c2 not in dead:
                out.append((c1, c2))
    else:  # offsuit
        for a in SUITS:
            for b in SUITS:
                if a == b:
                    continue
                c1, c2 = r1 + a, r2 + b
                if c1 not in dead and c2 not in dead:
                    out.append((c1, c2))
    return out


def equity_vs_range(hole, board, villain_hands, iters=600, seed=0):
    """Hero equity (0-100) vs a range (list of 169-hands). Exact on the river."""
    if not hole or len(hole) != 2:
        return None
    dead = set(hole) | set(board)
    vcombos = [c for h in villain_hands for c in _combos(h, dead)]
    if not vcombos:
        return None
    full = [r + s for r in RANKS for s in SUITS]
    rng = random.Random(seed)
    need = 5 - len(board)
    wins = ties = n = 0.0

    if need == 0:  # river: exact over all villain combos
        hs = evaluate7(list(hole) + list(board))
        for v in vcombos:
            vs = evaluate7(list(v) + list(board))
            if hs > vs: wins += 1
            elif hs == vs: ties += 1
            n += 1
    else:
        for _ in range(iters):
            v = rng.choice(vcombos)
            used = dead | set(v)
            avail = [c for c in full if c not in used]
            runout = rng.sample(avail, need)
            fb = list(board) + runout
            hs = evaluate7(list(hole) + fb)
            vs = evaluate7(list(v) + fb)
            if hs > vs: wins += 1
            elif hs == vs: ties += 1
            n += 1
    return round(100 * (wins + ties / 2) / n, 1)


if __name__ == "__main__":
    # sanity checks
    print("AsKs on AhKd2c ->", made_hand(["As", "Ks"], ["Ah", "Kd", "2c"]), "| texture:", board_texture(["Ah", "Kd", "2c"]))
    print("7h2d on AhKdQc ->", made_hand(["7h", "2d"], ["Ah", "Kd", "Qc"]), "| texture:", board_texture(["Ah", "Kd", "Qc"]))
    print("straight flush 5s6s on 7s8s9s ->", made_hand(["5s", "6s"], ["7s", "8s", "9s"]))
    print("AA vs KK preflop-ish (flop AhKdQc) eq AsAc vs [KK]:",
          equity_vs_range(["As", "Ac"], ["Ah", "Kd", "Qc"], ["KK"]))
    print("river exact: AsAc on Ah Kd Qc 2c 3d vs [KK]:",
          equity_vs_range(["As", "Ac"], ["Ah", "Kd", "Qc", "2c", "3d"], ["KK"]))
    print("coinflip-ish: AsKs vs [QQ] on 2h7d9c:",
          equity_vs_range(["As", "Ks"], ["2h", "7d", "9c"], ["QQ"]))
