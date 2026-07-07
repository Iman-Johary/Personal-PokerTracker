"""
Poker Tracker - preflop analysis + hand replayer.

Parses GGPoker hand histories in ../history/ and writes dashboard.html:
  * RFI (Raise-First-In) analysis vs GTO opening ranges  -> 13x13 charts.
  * Facing a single open (call/3-bet/fold) vs simplified GTO defend ranges.
  * Net $ result per hand -> biggest wins / losses tab.
  * Full hand replay on a poker table for every hand, now with a postflop readout
    (board texture, Hero made hand, equity vs an estimated villain range).

UI/markup lives in tools/template.html (this file injects the data).
Run:  python3 analyze.py
"""

import os
import re
import json
import glob
from collections import defaultdict
from datetime import datetime

import gto_ranges as gto
import equity

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HISTORY_DIR = os.path.join(ROOT, "history")
TEMPLATE = os.path.join(HERE, "template.html")
OUT_HTML = os.path.join(ROOT, "dashboard.html")

RFI_POS = ["UTG", "HJ", "CO", "BTN", "SB"]
card_re = re.compile(r"([2-9TJQKA][cdhs])")


def money(s):
    m = re.search(r"\$([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def parse_hand(block):
    lines = [l.rstrip() for l in block.splitlines() if l.strip()]
    if not lines:
        return None
    m_id = re.search(r"Poker Hand #(\w+)", lines[0])
    hand_id = m_id.group(1) if m_id else "?"
    m_date = re.search(r"-\s*(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", lines[0])
    when = m_date.group(1) if m_date else ""
    m_stk = re.search(r"\(\$([\d.]+)/\$([\d.]+)\)", lines[0])
    sb, bb = (float(m_stk.group(1)), float(m_stk.group(2))) if m_stk else (0.05, 0.1)
    m_tab = re.search(r"Table '(.+?)'", "\n".join(lines[:2]))
    table = m_tab.group(1) if m_tab else ""

    btn = None
    for l in lines:
        mb = re.search(r"Seat #(\d+) is the button", l)
        if mb:
            btn = int(mb.group(1)); break
    if btn is None:
        return None

    seat_player, seat_stack = {}, {}
    for l in lines:
        ms = re.match(r"Seat (\d+): (.+?) \(\$([\d.]+) in chips\)", l)
        if ms:
            seat_player[int(ms.group(1))] = ms.group(2).strip()
            seat_stack[int(ms.group(1))] = float(ms.group(3))
    if btn not in seat_player or "Hero" not in seat_player.values():
        return None

    ss = sorted(seat_player.keys())
    order = ss[ss.index(btn):] + ss[:ss.index(btn)]
    n = len(order)
    pos_of_seat = {}
    if n >= 1: pos_of_seat[order[0]] = "BTN"
    if n >= 2: pos_of_seat[order[1]] = "SB"
    if n >= 3: pos_of_seat[order[2]] = "BB"
    nonblind = order[3:]; m = len(nonblind)
    for i, seat in enumerate(nonblind):
        fe = m - i
        pos_of_seat[seat] = "CO" if fe == 1 else "HJ" if fe == 2 else "UTG"

    hero_seat = next(s for s, p in seat_player.items() if p == "Hero")
    hero_pos = pos_of_seat.get(hero_seat)

    hole, revealed, collected = None, {}, 0.0
    for l in lines:
        if l.startswith("Dealt to Hero"):
            cs = card_re.findall(l.split("Hero", 1)[1])
            if len(cs) >= 2:
                hole = cs[:2]
        msh = re.match(r"(.+?): shows \[(.+?)\]", l)
        if msh:
            cs = card_re.findall(msh.group(2))
            if cs:
                revealed[msh.group(1).strip()] = cs
        mc = re.match(r"Hero collected \$([\d.]+)", l)
        if mc:
            collected += float(mc.group(1))
    if hole is None:
        return None

    street = None
    actions = []
    runs_raw = {}       # run label -> {flop,turn,river} new cards dealt that run
    run_win = {}        # run label -> [(name, amount)] collected in that run's showdown
    sd_label = None
    _RUNW = ("SECOND", "THIRD", "FOURTH", "FIFTH")
    for l in lines:
        if "*** HOLE CARDS ***" in l:
            street = "preflop"; continue
        if l.startswith("***"):
            # Normal streets AND run-it-2/3/N-times. Each runout's board is captured
            # ("*** FIRST/SECOND/THIRD FLOP/TURN/RIVER ***"); only the FIRST run has
            # betting action, so only it drives the `street` used for action capture.
            if "SUMMARY" in l:
                street = "done"; sd_label = None; continue
            lab = next((w for w in _RUNW if w in l), "FIRST")
            if "SHOWDOWN" in l:
                sd_label = lab; street = "done"; continue
            if "FLOP" in l or "TURN" in l or "RIVER" in l:
                r = runs_raw.setdefault(lab, {"flop": [], "turn": [], "river": []})
                if "FLOP" in l:
                    r["flop"] = card_re.findall(l)[:3]; cur = "flop"
                elif "TURN" in l:
                    r["turn"] = card_re.findall(l)[-1:]; cur = "turn"
                else:
                    r["river"] = card_re.findall(l)[-1:]; cur = "river"
                street = cur if lab == "FIRST" else "extra"
                continue
            street = "done"; continue
        if street in ("done", "extra"):
            mcol = re.match(r"(.+?) collected \$([\d.]+)", l)
            if mcol and sd_label:
                run_win.setdefault(sd_label, []).append((mcol.group(1).strip(), float(mcol.group(2))))
            continue

        mr = re.match(r"Uncalled bet \(\$([\d.]+)\) returned to (.+)$", l)
        if mr and street:
            actions.append({"street": street, "name": mr.group(2).strip(),
                            "verb": "refund", "amt": float(mr.group(1)), "to": 0,
                            "text": "uncalled bet returned"})
            continue
        ma = re.match(r"(.+?): (folds|checks|calls|raises|bets|posts|straddle) ?(.*)$", l)
        if not ma:
            continue
        name, verb, rest = ma.group(1).strip(), ma.group(2), ma.group(3)
        st = street or "preflop"
        if verb == "folds":
            actions.append({"street": st, "name": name, "verb": "fold", "amt": 0, "to": 0, "text": "folds"})
        elif verb == "checks":
            actions.append({"street": st, "name": name, "verb": "check", "amt": 0, "to": 0, "text": "checks"})
        elif verb == "calls":
            a = money(rest)
            actions.append({"street": st, "name": name, "verb": "call", "amt": a, "to": 0, "text": f"calls ${a:g}"})
        elif verb == "bets":
            a = money(rest)
            actions.append({"street": st, "name": name, "verb": "bet", "amt": a, "to": a, "text": f"bets ${a:g}"})
        elif verb == "raises":
            mto = re.search(r"to \$([\d.]+)", rest)
            to = float(mto.group(1)) if mto else money(rest)
            actions.append({"street": st, "name": name, "verb": "raise", "amt": 0, "to": to, "text": f"raises to ${to:g}"})
        elif verb == "posts":
            a = money(rest)
            lab = "SB" if "small" in rest else "BB" if "big" in rest else "ante" if "ante" in rest else "blind"
            actions.append({"street": "preflop", "name": name, "verb": "post", "amt": a, "to": a, "text": f"posts {lab} ${a:g}"})
        elif verb == "straddle":
            # A straddle is a forced voluntary blind; amounts are cumulative "to"
            # totals (like a raise). "and is all-in" marks an all-in re-straddle.
            to = money(rest)
            allin = "all-in" in rest
            actions.append({"street": "preflop", "name": name, "verb": "straddle", "amt": 0, "to": to,
                            "text": f"straddle ${to:g}" + (" and is all-in" if allin else "")})

    # Assemble runs (multiple boards when the pot was run 2+ times).
    _order = [x for x in ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH") if x in runs_raw]
    _first = runs_raw.get("FIRST", {"flop": [], "turn": [], "river": []})
    runs = []
    for lab in _order:
        r = runs_raw[lab]
        runs.append({"flop": r["flop"] or _first["flop"],
                     "turn": r["turn"] or _first["turn"],
                     "river": r["river"] or _first["river"],
                     "heroWon": any(nm == "Hero" for nm, _a in run_win.get(lab, []))})
    board = ({"flop": runs[0]["flop"], "turn": runs[0]["turn"], "river": runs[0]["river"]}
             if runs else {"flop": [], "turn": [], "river": []})

    # Hero net = collected - contributed
    contrib, hstreet, cur_street = 0.0, 0.0, "preflop"
    for a in actions:
        if a["street"] != cur_street:
            cur_street = a["street"]; hstreet = 0.0
        if a["name"] != "Hero":
            continue
        if a["verb"] in ("post", "call", "bet"):
            contrib += a["amt"]; hstreet += a["amt"]
        elif a["verb"] == "raise":
            contrib += a["to"] - hstreet; hstreet = a["to"]
        elif a["verb"] == "refund":
            contrib -= a["amt"]; hstreet -= a["amt"]
    hero_net = round(collected - contrib, 2)

    hi = order.index(hero_seat)
    cw = order[hi:] + order[:hi]
    seats = [{"slot": idx, "pos": pos_of_seat.get(s, "?"), "name": seat_player[s],
              "stack": seat_stack[s], "is_hero": s == hero_seat,
              "hole": hole if s == hero_seat else revealed.get(seat_player[s])}
             for idx, s in enumerate(cw)]

    return {"hand_id": hand_id, "when": when, "table": table, "sb": sb, "bb": bb,
            "hero_pos": hero_pos, "hole": hole, "seats": seats, "actions": actions,
            "board": board, "runs": runs, "hero_net": hero_net, "showdown": bool(revealed),
            "preflop": [a for a in actions if a["street"] == "preflop"]}


def classify_preflop(hand):
    """Return a judgement dict for Hero's first preflop decision, or None."""
    pos = hand["hero_pos"]
    # Straddled pots aren't standard 100bb spots — don't GTO-judge them.
    if any(a["verb"] == "straddle" for a in hand["preflop"]):
        hero_first = next((a for a in hand["preflop"] if a["name"] == "Hero"), None)
        if hero_first is None:
            return None
        act_txt = {"fold": "folded", "call": "called", "raise": "raised",
                   "check": "checked"}.get(hero_first["verb"], hero_first["verb"])
        return {"kind": "unjudged", "pos": pos, "hand": gto.hand_to_169(*hand["hole"]),
                "action": act_txt, "reason": "straddled pot", "type": "info"}
    raises = calls = 0
    opener = None
    for a in hand["preflop"]:
        if a["verb"] == "post":
            continue
        if a["name"] == "Hero":
            h169 = gto.hand_to_169(*hand["hole"])
            # ---- RFI: unopened pot ----
            if raises == 0 and calls == 0:
                if pos not in RFI_POS:
                    return {"kind": "unjudged", "pos": pos, "hand": h169,
                            "action": {"fold": "folded", "call": "called", "check": "checked",
                                       "raise": "raised"}.get(a["verb"], a["verb"]),
                            "reason": "big-blind option (pot unopened)", "type": "info"}
                if a["verb"] == "raise":
                    act = "RAISE"
                elif a["verb"] == "fold":
                    act = "FOLD"
                elif a["verb"] == "call":
                    act = "LIMP"
                else:
                    return None
                rec = gto.gto_action(pos, h169)
                gto_limps = h169 in gto.RFI_LIMP.get(pos, set())
                if rec == "MIX":
                    # solver plays this several ways (raise/limp/fold, e.g. the SB).
                    # Only flag a limp when the solver never limps this hand here.
                    if act == "LIMP" and not gto_limps:
                        return {"kind": "RFI", "pos": pos, "hand": h169, "action": act,
                                "rec": "MIX", "type": "limp"}
                    return {"kind": "RFI", "pos": pos, "hand": h169, "action": act,
                            "rec": ("LIMP" if gto_limps else "MIX"), "type": "ok"}
                if act == "LIMP":
                    # solver is pure raise or pure fold here -> limping is a leak
                    return {"kind": "RFI", "pos": pos, "hand": h169, "action": act,
                            "rec": ("RAISE" if rec != "FOLD" else "FOLD"), "type": "limp"}
                if act == rec:
                    return {"kind": "RFI", "pos": pos, "hand": h169, "action": act,
                            "rec": rec, "type": "ok"}
                t = "too_tight" if rec == "RAISE" else "too_loose"
                return {"kind": "RFI", "pos": pos, "hand": h169, "action": act, "rec": rec, "type": t}
            # ---- Facing a single open ----
            # UTG acts first in 6-max, so a genuine UTG can never be facing an open.
            # Likewise the opener can never share Hero's position. Both only happen on
            # non-6-max tables, where the position labeler collapses several early seats
            # into one "UTG" label — those aren't valid 6-max facing-open spots, so skip.
            if raises == 1 and calls == 0 and opener and pos != "UTG" and opener != pos:
                if a["verb"] == "fold":
                    act = "FOLD"
                elif a["verb"] == "call":
                    act = "CALL"
                elif a["verb"] == "raise":
                    act = "3BET"
                else:
                    return None
                defend, three = gto.defend_range(pos, opener)
                mix = gto.defend_mix(pos, opener)
                in_def = h169 in defend
                if h169 in mix:
                    t = "ok"  # solver plays this borderline hand both ways
                elif act == "FOLD" and in_def:
                    t = "too_tight"
                elif act in ("CALL", "3BET") and not in_def:
                    t = "too_loose"
                else:
                    t = "ok"
                return {"kind": "vsOpen", "pos": pos, "opener": opener, "hand": h169,
                        "action": act, "rec": ("DEFEND" if in_def else "FOLD"), "type": t}
            # ---- Not judged: describe the spot so the replayer can note it ----
            if raises == 1 and calls == 0 and (pos == "UTG" or opener == pos):
                reason = "a non-6-max table (positions aren't GTO-judged here)"
            elif raises >= 2:
                reason = "3-bet pot"
            elif raises == 1 and calls >= 1:
                reason = "multiway pot (a caller was in before you)"
            elif calls >= 1:
                reason = "limped pot"
            else:
                reason = "an unusual preflop spot"
            act_txt = {"fold": "folded", "call": "called", "raise": "raised",
                       "check": "checked"}.get(a["verb"], a["verb"])
            return {"kind": "unjudged", "pos": pos, "hand": h169,
                    "action": act_txt, "reason": reason, "type": "info"}
        if a["verb"] == "raise":
            raises += 1
            if raises == 1:
                opener = next((s["pos"] for s in hand["seats"] if s["name"] == a["name"]), "?")
        elif a["verb"] == "call":
            calls += 1
    return None


def classify_3bet(hand):
    """Judge Hero's decision when facing exactly one 3-bet (raises == 2 before
    Hero acts), whether Hero opened first or is cold. Returns a judgement or None."""
    pos = hand["hero_pos"]
    if any(a["verb"] == "straddle" for a in hand["preflop"]):
        return None
    raises = 0
    hero_raised = False
    tbet_pos = None
    for a in hand["preflop"]:
        if a["verb"] == "post":
            continue
        if a["name"] == "Hero":
            if raises == 2 and tbet_pos:
                h169 = gto.hand_to_169(*hand["hole"])
                if a["verb"] == "fold":
                    act = "FOLD"
                elif a["verb"] == "call":
                    act = "CALL"
                elif a["verb"] == "raise":
                    act = "4BET"
                else:
                    return None
                cont, four = gto.threebet_defense(pos, tbet_pos, hero_raised)
                mix = gto.cont_mix(pos, tbet_pos, hero_raised)
                in_cont = h169 in cont
                if h169 in mix:
                    t = "ok"  # solver plays this borderline hand both ways
                elif act == "FOLD" and in_cont:
                    t = "too_tight"
                elif act in ("CALL", "4BET") and not in_cont:
                    t = "too_loose"
                else:
                    t = "ok"
                return {"kind": "3bet", "pos": pos, "tbet": tbet_pos, "hand": h169,
                        "action": act, "opened": hero_raised,
                        "rec": ("CONTINUE" if in_cont else "FOLD"), "type": t}
            if a["verb"] == "raise":
                hero_raised = True; raises += 1
            continue
        if a["verb"] == "raise":
            raises += 1
            if raises == 2:
                tbet_pos = next((sx["pos"] for sx in hand["seats"] if sx["name"] == a["name"]), "?")
    return None


def _villain_range(hand, villain_name, villain_pos):
    """Estimate the villain's preflop range (list of 169-hands) from the action."""
    raisers = [a["name"] for a in hand["preflop"] if a["verb"] == "raise"]
    opener = raisers[0] if raisers else None
    opener_pos = next((s["pos"] for s in hand["seats"] if s["name"] == opener), None) if opener else None
    r = None
    if villain_name in raisers:
        if raisers.index(villain_name) == 0:            # villain was the opener
            r = gto.RANGES.get(villain_pos)
        elif opener_pos:                                # villain 3-bet (or more) over an open
            _, r = gto.defend_range(villain_pos, opener_pos)
        else:
            r = gto.RANGES.get(villain_pos)
    elif opener and opener_pos and villain_name != opener:  # villain flat-called an open
        defend, three = gto.defend_range(villain_pos, opener_pos)
        r = defend - three
    return sorted(r) if r else None


def compute_postflop(hand):
    """Per-street postflop readout for the replayer: board texture, Hero's made
    hand, and (heads-up only) Hero equity vs an estimated villain range."""
    board = hand.get("board") or {}
    flop = board.get("flop") or []
    hole = hand.get("hole")
    if len(flop) < 3 or not hole:
        return None
    folded = {a["name"] for a in hand["preflop"] if a["verb"] == "fold"}
    in_flop = [s["name"] for s in hand["seats"] if s["name"] not in folded]
    if "Hero" not in in_flop:
        return None
    others = [n for n in in_flop if n != "Hero"]
    multiway = len(others) > 1
    villain_name = others[0] if len(others) == 1 else None
    villain_pos = next((s["pos"] for s in hand["seats"] if s["name"] == villain_name), None) if villain_name else None
    vrange = _villain_range(hand, villain_name, villain_pos) if (villain_name and villain_pos) else None

    turn = board.get("turn") or []
    river = board.get("river") or []
    layers = [("flop", flop)]
    if turn:
        layers.append(("turn", flop + turn))
    if river:
        layers.append(("river", flop + turn + river))
    streets = {}
    for name, b in layers:
        info = {"texture": equity.board_texture(b), "made": equity.made_hand(hole, b)}
        if vrange and not multiway:
            eq = equity.equity_vs_range(hole, b, vrange, iters=200)
            if eq is not None:
                info["eq"] = eq
        streets[name] = info
    return {"villain": ({"pos": villain_pos} if villain_pos else None),
            "multiway": multiway, "streets": streets}


def main():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.txt")))
    total = 0
    hands, replays, threebet = [], {}, []

    for fp in files:
        with open(fp, encoding="utf-8", errors="replace") as f:
            content = f.read()
        for b in re.split(r"(?=Poker Hand #)", content):
            if "Poker Hand #" not in b:
                continue
            total += 1
            if total % 400 == 0:
                print(f"  ...processed {total} hands", flush=True)
            hand = parse_hand(b)
            if not hand:
                continue
            hid = hand["hand_id"]
            hole169 = gto.hand_to_169(*hand["hole"])
            when = hand["when"]
            dt = when.replace("/", "-").replace(" ", "T") if when else ""
            replays[hid] = {"id": hid, "when": when, "table": hand["table"],
                            "sb": hand["sb"], "bb": hand["bb"], "hero_pos": hand["hero_pos"],
                            "seats": hand["seats"], "actions": hand["actions"],
                            "board": hand["board"], "runs": hand["runs"], "net": hand["hero_net"],
                            "judgement": None, "postflop": None}
            try:
                replays[hid]["postflop"] = compute_postflop(hand)
            except Exception:
                replays[hid]["postflop"] = None
            vp = 1 if any(a["name"] == "Hero" and a["verb"] in ("raise", "call")
                          for a in hand["preflop"]) else 0
            entry = {"id": hid, "dt": dt, "day": dt[:10], "net": hand["hero_net"],
                     "pos": hand["hero_pos"] or "", "sd": 1 if hand["showdown"] else 0,
                     "vp": vp, "h": hole169, "k": "", "op": "", "a": "", "t": ""}
            j = classify_preflop(hand)
            j3 = classify_3bet(hand)
            if j:
                if j["kind"] == "RFI":
                    entry["k"] = "R"; entry["a"] = j["action"]; entry["t"] = j["type"]; entry["h"] = j["hand"]
                elif j["kind"] == "vsOpen":
                    entry["k"] = "V"; entry["op"] = j["opener"]; entry["a"] = j["action"]; entry["t"] = j["type"]; entry["h"] = j["hand"]
            # replayer shows the 3-bet decision when there is one, else the first decision
            replays[hid]["judgement"] = j3 or j
            if j3:
                threebet.append({"id": hid, "dt": dt, "day": dt[:10], "net": hand["hero_net"],
                                 "pos": j3["pos"], "tbet": j3["tbet"], "h": j3["hand"],
                                 "a": j3["action"], "t": j3["type"], "rec": j3["rec"],
                                 "opened": 1 if j3["opened"] else 0})
            hands.append(entry)

    vs_defend = {}
    vs_freq = {}
    for hero in ["HJ", "CO", "BTN", "SB", "BB"]:
        vs_defend[hero] = {}
        vs_freq[hero] = {}
        for op in ["UTG", "HJ", "CO", "BTN", "SB"]:
            d, three = gto.defend_range(hero, op)
            mix = gto.defend_mix(hero, op)
            vs_defend[hero][op] = {"defend": sorted(d), "threebet": sorted(three), "mix": sorted(mix)}
            vs_freq[hero][op] = gto.vsopen_split(hero, op)

    ALLPOS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]
    tb_defend = {}
    tb_freq = {}
    for hero in ALLPOS:
        tb_defend[hero] = {}
        tb_freq[hero] = {}
        for tb in ALLPOS:
            if hero == tb:
                continue
            cont, four = gto.threebet_defense(hero, tb, True)
            tb_defend[hero][tb] = {"cont": sorted(cont), "fourbet": sorted(four)}
            tb_freq[hero][tb] = gto.tb_split(hero, tb)

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "files": len(files), "total_hands": total,
        "ranks": gto.RANKS, "positions": RFI_POS,
        "ranges": {p: sorted(list(gto.RANGES[p])) for p in RFI_POS},
        "rfi_mix": {p: sorted(list(gto.RFI_MIX[p])) for p in RFI_POS},
        "rfi_limp": {p: sorted(list(gto.RFI_LIMP[p])) for p in RFI_POS},
        "vs_defend": vs_defend, "tb_defend": tb_defend,
        "rfi_freq": {p: gto.rfi_split(p) for p in RFI_POS},
        "vs_freq": vs_freq, "tb_freq": tb_freq,
        "hands": hands, "threebet": threebet, "replays": replays,
    }

    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("/*__DATA__*/null", json.dumps(payload))
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Parsed {total} hands from {len(files)} file(s) -> {OUT_HTML}")


if __name__ == "__main__":
    main()
