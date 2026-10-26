"""LilyPond score -> Humdrum **kern, so build_song.py can use pieces that exist only as LilyPond sources
(e.g. engravings on IMSLP / Mutopia).

usage: python tools/lily2kern.py score.ly out.krn [--mm 60]

How: LilyPond itself interprets the score (lilypond must be installed: pip install lilypond) and an engraver
(tools/lily/dump-events.ly) writes every note with its exact spelling, moment, duration, tie and fingering,
plus dynamics, hairpins, texts, pedal and tempo marks. This script lays those events out as kern:

- each staff becomes one or more **kern spines (one per simultaneous LilyPond voice), each followed by a
  **fing spine for printed fingerings; *staff1 = upper staff (right hand), *staff2 = lower staff
- dynamics and hairpins go to one **dynam spine, texts to !LO:TX comments, pedal to *ped / *Xped
- notes that cross a bar line or have no single kern duration are split and tied; gaps become rests
- grace notes are skipped; repeats are not expanded (the script warns if the score has any)

Afterwards the script checks that the kern file, read back by verovio, has exactly the same notes
(pitch and onset) as LilyPond produced.
"""
import os, re, sys, subprocess, tempfile, collections, argparse, shutil
from fractions import Fraction as Fr

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP = os.path.join(HERE, "lily", "dump-events.ly")


def lilypond_exe():
    exe = shutil.which("lilypond")
    if exe: return exe
    import lilypond
    return str(lilypond.executable())


def run_lilypond(ly):
    d = tempfile.mkdtemp()
    ev = os.path.join(d, "events.tsv")
    r = subprocess.run([lilypond_exe(), f"-dinclude-settings={DUMP}", "-dno-print-pages", "-o", os.path.join(d, "out"),
                        os.path.basename(ly)], cwd=os.path.dirname(os.path.abspath(ly)),
                       env=dict(os.environ, LILY_EVENTS=ev), capture_output=True, text=True)
    if not os.path.exists(ev):
        sys.exit("lilypond failed:\n" + r.stderr[-3000:])
    errs = [l for l in r.stderr.splitlines() if "error" in l.lower()]
    if errs: print("lilypond reported:", *errs[:10], sep="\n  ")
    return ev


# ---------------------------------------------------------------- durations
REPR = {}
for base in range(0, 8):                        # breve .. 128th
    n = 0 if base == 0 else 2 ** (base - 1)
    whole = Fr(2) if n == 0 else Fr(1, n)
    REPR[whole] = str(n); REPR[whole * Fr(3, 2)] = f"{n}."; REPR[whole * Fr(7, 4)] = f"{n}.."
for n in (3, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 18, 20, 24, 28, 36, 40, 48, 56, 80, 96, 112, 192):
    REPR.setdefault(Fr(1, n), str(n)); REPR.setdefault(Fr(3, 2 * n), f"{n}.")
SIZES = sorted(REPR, reverse=True)


def pieces(d):
    """split a duration (whole notes) into kern-writable parts, largest first"""
    out, left = [], Fr(d)
    while left > 0:
        if left in REPR: out.append(left); break
        p = next((s for s in SIZES if s <= left and (left - s) in REPR), None) or next((s for s in SIZES if s <= left), None)
        if p is None: out.append(left); break   # tiny remainder: written as a rational below
        out.append(p); left -= p
    return out


def recip(d):
    if d in REPR: return REPR[d]
    return f"{d.denominator}%{d.numerator}"   # kern rational duration: n%m = m/n of a whole note


def kpitch(name, alt, octave):
    sci = octave + 4
    L = "cdefgab"[name]
    s = L * (sci - 3) if sci >= 4 else L.upper() * (4 - sci)
    acc = {Fr(1, 2): "#", Fr(1): "##", Fr(-1, 2): "-", Fr(-1): "--"}.get(alt, "")
    return s + acc


# ---------------------------------------------------------------- read events
def read_events(path):
    E = []
    for ln in open(path, encoding="utf-8"):
        f = ln.rstrip("\n").split("\t")
        if len(f) < 9: continue
        e = dict(staff=int(f[0]), voice=int(f[1]), bar=int(f[2]), mpos=Fr(f[3]), mlen=Fr(f[4]), tsig=f[5],
                 t=Fr(f[6]), grace=Fr(f[7]), kind=f[8], args=f[9:])
        E.append(e)
    return E


def convert(ly, out, mm=None):
    src = open(ly, encoding="utf-8", errors="replace").read()
    if "\\repeat" in src and "unfold" not in src:
        print("warning: the score has \\repeat - repeats are not expanded")
    E = read_events(run_lilypond(ly))
    if not E: sys.exit("no events")

    # notes, grouped into chords per voice
    notes, skipped_grace = [], 0
    tie_all = {(e["voice"], e["t"]) for e in E if e["kind"] == "tieall"}
    for e in E:
        if e["kind"] != "note": continue
        if e["staff"] < 0:                       # a note outside any staff (e.g. a typo in a Dynamics line) is not played
            print("warning: note outside any staff skipped, bar", e["bar"]); continue
        if e["grace"] != 0: skipped_grace += 1; continue
        name, alt, octv, dur, fing, tie = e["args"][:6]
        notes.append(dict(staff=e["staff"], voice=e["voice"], t=e["t"], name=int(name), alt=Fr(alt), oct=int(octv),
                          d=Fr(dur), fing=fing, tie=bool(tie) or (e["voice"], e["t"]) in tie_all))
    for n in notes:
        n["midi"] = 60 + 12 * n["oct"] + [0, 2, 4, 5, 7, 9, 11][n["name"]] + int(n["alt"] * 2)
    end = max(n["t"] + n["d"] for n in notes)
    # fingerings written after a note or a chord arrive as separate events of that voice and moment:
    # one digit on a chord goes to the top note in the upper staff, the bottom note in the lower staff
    fing_at = collections.defaultdict(list)
    for e in E:
        if e["kind"] == "fing" and e["args"] and e["grace"] == 0: fing_at[(e["voice"], e["t"])].append(e["args"][0])
    stave_mean = collections.defaultdict(list)
    for n in notes: stave_mean[n["staff"]].append(n["midi"])
    upper_staff = max(stave_mean, key=lambda st: sum(stave_mean[st]) / len(stave_mean[st]))
    grp = collections.defaultdict(list)
    for n in notes: grp[(n["voice"], n["t"])].append(n)
    for key, digs in fing_at.items():
        ch = sorted(grp.get(key, []), key=lambda n: n["midi"])
        if not ch or any(n["fing"] for n in ch): continue
        if len(digs) == 1: (ch[-1] if ch[0]["staff"] == upper_staff else ch[0])["fing"] = digs[0]
        else:
            for n, d in zip(ch, digs): n["fing"] = d
    onsets = {(n["staff"], n["midi"], n["t"]) for n in notes}
    for n in notes:                              # a chord tie only binds the notes that really continue
        n["tie"] = n["tie"] and (n["staff"], n["midi"], n["t"] + n["d"]) in onsets

    # bars
    # where each bar starts, as most notes and rests in it say (events at moment 0 can come before \partial)
    votes, tsig_at = collections.defaultdict(collections.Counter), {}
    for e in E:
        if e["kind"] not in ("note", "rest") or e["grace"] != 0: continue
        votes[e["bar"]][max(Fr(0), e["t"] - e["mpos"])] += 1; tsig_at.setdefault(e["bar"], e["tsig"])
    bar_start = {b: c.most_common(1)[0][0] for b, c in votes.items()}
    bars = sorted(bar_start.items(), key=lambda x: x[1])
    starts = [s for _, s in bars if s < end]
    bars = [(b, s) for b, s in bars if s < end]
    def next_bar(t):
        return next((s for s in starts if s > t), end)

    # staves: upper staff (higher mean pitch) = staff1 = right hand
    by_staff = collections.defaultdict(list)
    for n in notes: by_staff[n["staff"]].append(n["midi"])
    order = sorted(by_staff, key=lambda s: -sum(by_staff[s]) / len(by_staff[s]))
    if len(order) != 2: print("warning: expected 2 staves, found", len(order))
    staffnum = {s: i + 1 for i, s in enumerate(order)}

    # voices -> spines (interval colouring so simultaneous voices get separate spines)
    spans = {}
    for n in notes:
        a, b = spans.get(n["voice"], (n["t"], n["t"] + n["d"]))
        spans[n["voice"]] = (min(a, n["t"]), max(b, n["t"] + n["d"]))
    vstaff = {n["voice"]: n["staff"] for n in notes}
    slots = {}                                   # staff -> list of [busy_until, [voices]]
    vslot = {}
    for v in sorted(spans, key=lambda v: (spans[v][0], v)):
        st = vstaff[v]; L = slots.setdefault(st, [])
        for k, sl in enumerate(L):
            if sl[0] <= spans[v][0]: sl[0] = spans[v][1]; sl[1].append(v); vslot[v] = (st, k); break
        else:
            L.append([spans[v][1], [v]]); vslot[v] = (st, len(L) - 1)
    spines = []                                  # (staff, slot) in column order: lower staff first
    for st in sorted(slots, key=lambda s: -staffnum[s]):
        for k in range(len(slots[st])): spines.append((st, k))
    col = {sp: i for i, sp in enumerate(spines)}

    # events per spine: chords (same voice, same onset), split at bar lines / unwritable durations with ties
    per = collections.defaultdict(lambda: collections.defaultdict(list))   # spine -> t -> list of (token, finger)
    chords = collections.defaultdict(list)
    for n in notes: chords[(n["voice"], n["t"])].append(n)
    tied_from = set()                            # (voice-slot, midi, t) that continue a tie
    for (v, t), ch in sorted(chords.items(), key=lambda x: x[0][1]):
        sp = vslot[v]
        ch.sort(key=lambda n: n["midi"])
        for n in ch:
            segs, t0, left = [], t, n["d"]
            while left > 0:
                nb = next_bar(t0); seg = min(left, nb - t0) if nb > t0 else left
                for p in pieces(seg): segs.append((t0, p)); t0 += p
                left -= seg
            cont = (n["staff"], n["midi"], t) in tied_from
            for i, (ts, p) in enumerate(segs):
                first, last = i == 0, i == len(segs) - 1
                mid_in = cont or not first           # this part continues a tie
                more = (not last) or n["tie"]        # and is tied onward
                pre = "[" if more and not mid_in else ""
                post = ("_" if more else "]") if mid_in else ""
                tok = pre + recip(p) + kpitch(n["name"], n["alt"], n["oct"]) + post
                per[sp][ts].append((tok, n["fing"] if first else "", n["midi"]))
            if n["tie"]: tied_from.add((n["staff"], n["midi"], t + n["d"]))

    # rests in gaps, so every spine adds up to the whole piece
    for sp in spines:
        ev = per[sp]
        busy = []
        for ts, toks in ev.items():
            dur = min(_tokdur(tk) for tk, _, _ in toks)
            busy.append((ts, ts + dur))
        busy.sort()
        cur = Fr(0); gaps = []
        for a, b in busy:
            if a > cur: gaps.append((cur, a))
            cur = max(cur, b)
        if cur < end: gaps.append((cur, end))
        for a, b in gaps:
            t0 = a
            while t0 < b:
                nb = min(next_bar(t0), b)
                for p in pieces(nb - t0):
                    ev[t0].append((recip(p) + "r", "", None)); t0 += p

    # dynamics, texts, pedal, tempo
    dyn, texts, ped, tempo = collections.defaultdict(list), collections.defaultdict(list), collections.defaultdict(list), {}
    for e in E:
        k, t = e["kind"], e["t"]
        if e["grace"] != 0: continue
        if k == "dyn" and e["args"]:
            if e["args"][0] not in dyn[t]: dyn[t].append(e["args"][0])
        elif k in ("cresc", "decresc") and e["args"]:
            d = int(e["args"][0])
            tok = ("<" if k == "cresc" else ">") if d < 0 else ("[" if k == "cresc" else "]")
            if tok not in dyn[t]: dyn[t].append(tok)
        elif k == "text" and e["args"] and e["args"][0].strip():
            c = col.get(vslot.get(e["voice"]), 0) if e["voice"] in vslot else 0
            texts[t].append((c, e["args"][0].strip()))
        elif k == "ped" and e["args"]:
            ped[t].append("*ped" if int(e["args"][0]) < 0 else "*Xped")   # \\sustainOff\\sustainOn: change
        elif k == "tempo" and e["args"] and e["args"][0]:
            try:
                cnt = Fr(e["args"][0]); unit = Fr(e["args"][1]) if len(e["args"]) > 1 and e["args"][1] else Fr(1, 4)
                tempo[t] = round(cnt * unit / Fr(1, 4))
            except ValueError: pass
    if 0 not in tempo and mm: tempo[Fr(0)] = mm

    # write
    ncol = len(spines)
    hdr = []
    for sp in spines: hdr += ["**kern", "**fing"]
    hdr.append("**dynam")
    W = len(hdr)
    def interp(fill, kernval=None, every=True):
        row = []
        for i, sp in enumerate(spines):
            row += [kernval if (kernval and (every or i == 0)) else "*", "*"]
        row.append("*")
        return "\t".join(row)
    lines = ["\t".join(hdr)]
    row = []
    for sp in spines: row += [f"*staff{staffnum[sp[0]]}", "*"]
    lines.append("\t".join(row + ["*"]))
    row = []
    for sp in spines: row += ["*clefG2" if staffnum[sp[0]] == 1 else "*clefF4", "*"]
    lines.append("\t".join(row + ["*"]))
    times = sorted(set(t for sp in spines for t in per[sp]) | set(starts) | set(dyn) | set(texts) | set(ped) | set(tempo))
    bar_at = {s: b for b, s in bars}
    if bars and bars[0][1] > 0:                  # pickup (\partial) before bar 1: numbered 0, like printed scores
        bar_at[Fr(0)] = 0; times = sorted(set(times) | {Fr(0)})
    last_ts = None
    for t in times:
        if t >= end and t not in starts: continue
        if t in bar_at:
            b = bar_at[t]
            lines.append("\t".join([f"={b}"] * W))
            ts = tsig_at.get(b)
            if ts and ts != last_ts:
                lines.append(interp(None, f"*M{ts}")); last_ts = ts
        if t in tempo: lines.append(interp(None, f"*MM{tempo[t]}"))
        for pv in ped.get(t, []): lines.append(interp(None, pv, every=False))
        for c, txt in texts.get(t, []):
            row = ["!"] * W; row[2 * c] = "!LO:TX:t=" + txt.replace("\t", " ").replace(":", "&colon;")
            lines.append("\t".join(row))
        row = []
        any_data = False
        for sp in spines:
            toks = per[sp].get(t, [])
            if toks:
                any_data = True
                row.append(" ".join(tk for tk, _, _ in toks))
                fs = [f or "." for _, f, _ in toks]
                row.append(" ".join(fs) if any(f != "." for f in fs) else ".")
            else:
                row += [".", "."]
        # several marks at one moment: ends first, then loudness, then a new hairpin (space-separated)
        ds = sorted(dyn.get(t, []), key=lambda x: 0 if x in "[]" else (2 if x in "<>" else 1))
        row.append(" ".join(ds) if ds else ".")
        if any_data or dyn.get(t):
            lines.append("\t".join(row))
    lines.append("\t".join(["=="] * W))
    lines.append("\t".join(["*-"] * W))
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"{out}: {len(notes)} notes, {len(bars)} bars, {len(spines)} kern spines, grace notes skipped: {skipped_grace}")
    verify(out, notes)


def _tokdur(tok):
    m = re.match(r"\[?(\d+)(%(\d+))?(\.*)", tok)
    n = int(m[1]); dots = len(m[4])
    if m[2]: d = Fr(int(m[3]), n)
    else: d = Fr(2) if n == 0 else Fr(1, n)
    return d * (2 - Fr(1, 2 ** dots))


def verify(krn, notes):
    """read the kern back with verovio: same pitches at the same onsets as LilyPond produced?"""
    import verovio
    sys.path.insert(0, HERE)
    from build_song import kern_pitch
    tk = verovio.toolkit(); tk.loadFile(krn)
    tm = tk.renderToTimemap({"includeMeasures": True})
    lines = open(krn, encoding="utf-8").read().split("\n")
    got = collections.Counter()
    q_of = {}
    for e in tm:
        for i in e.get("on", []):
            m = re.match(r"note-L(\d+)F(\d+)(?:S(\d+))?", i)
            tok = lines[int(m[1]) - 1].split("\t")[int(m[2]) - 1]
            tok = tok.split(" ")[int(m[3]) - 1] if m[3] else tok
            if "]" in tok or "_" in tok: continue
            got[(Fr(e["qstamp"]).limit_denominator(960), kern_pitch(tok))] += 1
    want = collections.Counter((n["t"] * 4, n["midi"]) for n in notes)
    # tied continuations in the source are separate LilyPond notes: drop them from 'want'
    tied_targets = collections.Counter()
    for n in notes:
        if n["tie"]: tied_targets[((n["t"] + n["d"]) * 4, n["midi"])] += 1
    want = want - tied_targets
    miss, extra = want - got, got - want
    print(f"check: {sum(want.values())} onsets expected, missing {sum(miss.values())}, unexpected {sum(extra.values())}")
    for k in list(miss)[:8]: print("  missing", k)
    for k in list(extra)[:8]: print("  extra", k)
    return not miss and not extra


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ly"); ap.add_argument("out"); ap.add_argument("--mm", type=int, default=None)
    a = ap.parse_args()
    convert(a.ly, a.out, a.mm)
