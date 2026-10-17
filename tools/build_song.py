"""Build practice data for one song: score (Humdrum **kern) -> notes + fingering + marks.

usage:  python tools/build_song.py <song-id> [<song-id> ...]   (ids from tools/catalog.json)
        python tools/build_song.py --all

Fingering priority (highest first):
  1. your own corrections   songs/fix/<id>.json
  2. fingerings printed in the edition (**fing spines)
  3. automatic: Nakamura et al. 2nd-order HMM (trained on human fingerings; ~70% agreement
     with pianists vs ~61% for PianoPlayer in our test), then cleanup rules (repeats, chords).
     If the HMM can't be built, PianoPlayer is used instead.
"""
import json, re, sys, os, subprocess, collections, logging, statistics

import verovio
from pianoplayer.hand import Hand
from pianoplayer.models import INote

logging.disable(logging.WARNING)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
CATALOG = json.load(open(os.path.join(ROOT, "tools", "catalog.json"), encoding="utf-8"))

PLAYER = json.load(open(os.path.join(ROOT, "tools", "player.json"), encoding="utf-8"))
WHITE_CM = 2.35                                    # width of one white key
def reach_semitones(span_cm):
    """Largest interval the hand can strike at once. Key centres n white keys apart are n*2.35 cm;
    about 1.5 cm of the span is lost to the fingertip pads."""
    steps = int((span_cm - 1.5) // WHITE_CM)       # white-key steps, 7 = octave
    return {6: 11, 7: 12, 8: 14, 9: 16, 10: 17}.get(max(6, min(10, steps)), 14)
REACH = reach_semitones(PLAYER["span_cm"])         # 22 cm -> 8 steps -> a 9th (14 semitones)
HOLD_REACH = REACH + 2                             # holding one key and adding another stretches a bit further

LAYOUT = [0, .5, 1, 1.5, 2, 3, 3.5, 4, 4.5, 5, 5.5, 6]
K = 16.5 / 7
def xpos(p): return 16.5 * (p // 12) + LAYOUT[p % 12] * K
def is_black(p): return p % 12 in (1, 3, 6, 8, 10)


# ---------------------------------------------------------------- sources
def fetch_kern(song):
    repo, path = song["kern_repo"], song["kern_path"]
    d = os.path.join(CACHE, repo.replace("/", "__"))
    if not os.path.isdir(d):
        os.makedirs(CACHE, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{repo}", d], check=True)
    f = os.path.join(d, path)
    if not os.path.exists(f):
        subprocess.run(["git", "-C", d, "checkout", "HEAD", "--", path], check=True)
    return f


# ---------------------------------------------------------------- kern helpers
def kern_pitch(tok):
    letters = re.search(r"([a-g]+|[A-G]+)", tok)
    if not letters: return None
    L = letters.group(1)
    base = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}[L[0].lower()]
    octv = 3 + len(L) if L[0].islower() else 4 - len(L)
    mm = re.match(r"(#+|-+|n)?", tok[letters.end():])
    acc = 0
    if mm and mm.group(1):
        a = mm.group(1); acc = len(a) if a[0] == "#" else (-len(a) if a[0] == "-" else 0)
    return 12 * (octv + 1) + base + acc

def spine_maps(lines):
    """For every line: what each field is -> ('L'|'R', voice) for notes, ('FL'|'FR',0) fingering, ('D',0) dynamics."""
    fmap, cur = {}, None
    for i, ln in enumerate(lines, 1):
        if ln.startswith("**"):
            hdr = ln.split("\t"); kidx = [j for j, f in enumerate(hdr) if f == "**kern"]
            cur, last = [], None
            for j, f in enumerate(hdr):
                if f == "**kern":
                    last = "L" if j == kidx[0] else "R"; cur.append((last, 0))
                elif f == "**fing" and last: cur.append(("F" + last, 0))
                elif f == "**dynam": cur.append(("D", 0))
                else: cur.append(("x", 0))
            fmap[i] = list(cur); continue
        if cur is None: continue
        fmap[i] = list(cur)
        toks = ln.split("\t")
        if ln.startswith("*") and ("*^" in toks or "*v" in toks):
            new, j = [], 0
            while j < len(toks):
                if toks[j] == "*^": new += [cur[j], (cur[j][0], cur[j][1] + 1)]; j += 1
                elif toks[j] == "*v":
                    k = j
                    while k < len(toks) and toks[k] == "*v": k += 1
                    new.append((cur[j][0], 0)); j = k
                else: new.append(cur[j]); j += 1
            cur = new
    return fmap


# ---------------------------------------------------------------- build
def build(song):
    kern = fetch_kern(song)
    if song.get("kern_patch"):          # small fixes to the score file, e.g. a missing tempo change (line count kept)
        text = open(kern, encoding="utf-8").read()
        for old, new in song["kern_patch"]:
            assert text.count(old) >= 1, old
            text = text.replace(old, new, 1)
        kern = kern[:-4] + ".patched.krn"; open(kern, "w", encoding="utf-8").write(text)
    lines = open(kern, encoding="utf-8").read().split("\n")
    fmap = spine_maps(lines)
    tk = verovio.toolkit(); tk.loadFile(kern)
    tm = tk.renderToTimemap({"includeMeasures": True})

    measure_at_line = {i: int(m[1]) for i, ln in enumerate(lines, 1) if (m := re.match(r"^=(\d+)", ln))}
    on_time, off_time, meas = {}, {}, []
    for e in tm:
        t = e["tstamp"]
        if e.get("measureOn"):
            L = int(re.match(r"measure-L(\d+)", e["measureOn"])[1]); meas.append((t, measure_at_line.get(L, 1)))
        for i in e.get("on", []): on_time[i] = t
        for i in e.get("off", []): off_time[i] = t
    def measure_index(t):
        k = 0
        for idx, (ms, _) in enumerate(meas):
            if ms <= t + 1: k = idx
            else: break
        return k

    def orig_finger(L, F, S, staff):
        m = fmap[L]; toks = lines[L - 1].split("\t"); j = F
        while j < len(toks) and j < len(m):
            if m[j][0] == staff: return 0
            if m[j][0] == "F" + staff: break
            j += 1
        else: return 0
        if j >= len(toks): return 0
        ft = toks[j]
        if ft in (".", "") or ft[0] in "*!": return 0
        subs, kt = ft.split(" "), toks[F - 1].split(" ")
        if len(subs) != len(kt): return 0
        d = re.search(r"[1-5]", subs[int(S) - 1 if S else 0])
        return int(d[0]) if d else 0

    raw = []
    for nid, t in on_time.items():
        m = re.match(r"note-L(\d+)F(\d+)(?:S(\d+))?", nid)
        L, F, S = int(m[1]), int(m[2]), m[3]
        field = lines[L - 1].split("\t")[F - 1]
        tok = field.split(" ")[int(S) - 1] if S else field
        p = kern_pitch(tok)
        if p is None: continue
        staff, voice = fmap[L][F - 1]
        raw.append(dict(id=nid, base=re.sub(r"-rend\d+$", "", nid), t=t, off=off_time.get(nid, t + 200), p=p,
                        hand=staff, voice=voice, tie=("]" in tok or "_" in tok), grace="q" in tok,
                        trill="TT" in tok or bool(re.search(r"[Tt]", re.sub(r"[a-gA-G#\-n]", "", tok))),
                        mi=measure_index(t), of=orig_finger(L, F, S, staff), L=L, F=F))
    raw.sort(key=lambda n: (n["t"], n["p"]))
    # Which HAND plays a note is not the same as which STAFF it is written on: inner voices are often
    # written on the other hand's staff (e.g. Pathetique II: the inner semiquavers sit on the lower staff
    # but are played by the right hand - Czerny: "without exception"). separate_hands() decides it.
    notes = []
    for n in raw:
        if n["tie"]:
            for prev in reversed(notes):
                if prev["p"] == n["p"] and prev["hand"] == n["hand"]:
                    prev["off"] = max(prev["off"], n["off"]); break
            continue
        notes.append(n)

    marked = apply_hand_markings(lines, notes)
    hand_moves = separate_hands(notes, meas)
    # your own corrections act as fixed anchors
    fixp = os.path.join(ROOT, "songs", "fix", song["id"] + ".json")
    fixes = json.load(open(fixp, encoding="utf-8")).get("edits", {}) if os.path.exists(fixp) else {}
    for n in notes:
        k = f'{n["t"]}:{n["p"]}:{n["hand"]}'
        if k in fixes: n["of"] = int(fixes[k]); n["mine"] = 1

    engine = "hmm2"
    try:
        hmm_fingering(notes)
    except Exception as e:
        print("  HMM unavailable, falling back to PianoPlayer:", e, flush=True); engine = "pianoplayer"
        for side, h in (("right", "R"), ("left", "L")):
            finger_hand([n for n in notes if n["hand"] == h], side)
    stats = cleanup(notes, meas)
    stats["same_finger_repaired"] = repair_same_finger(notes)
    if engine == "hmm2":
        for h in "LR": set_poses([n for n in notes if n["hand"] == h], h)

    marks = extract_marks(song, lines, tm)
    # a real pianist's performance (velocity per note, pedal, tempo map), aligned to these score notes
    perf = None
    if song.get("perf"):
        pf = json.load(open(os.path.join(ROOT, song["perf"]), encoding="utf-8"))
        for n in notes: n["pv"] = pf["velocity"].get(f'{n["t"]}:{n["p"]}', 0)
        perf = dict(performer=re.sub(r"\d+$", "", pf["performer"]), source=pf["source"], file=pf["file"],
                    license=pf["license"], tempo=pf["tempo"], pedal=pf["pedal"])
    out = dict(
        id=song["id"], title=song["title"], composer=song["composer"], work=song["work"],
        speed=song.get("speed", 0.5),
        source=dict(name=song["source_name"], url=song["source_url"], license=song.get("license", "")),
        measures=[[t, m] for t, m in meas],
        notes=[[n["t"], n["off"] - n["t"], n["p"], 0 if n["hand"] == "L" else 1, n["f"], meas[n["mi"]][1],
                1 if n["grace"] else 0, 1 if n["trill"] else 0, n["pos"],
                2 if n.get("mine") else (1 if n.get("of") else 0), n.get("pv", 0)] for n in notes],
        marks=marks,
    )
    if perf: out["perf"] = perf
    json.dump(out, open(os.path.join(ROOT, "songs", song["id"] + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    stats.update(quality(notes)); stats["engine"] = engine
    stats["hand_moves"] = hand_moves
    stats["hand_marked"] = marked
    stats["reach_semitones"] = REACH
    stats["hand_audit"] = hand_audit(notes, meas)
    stats.update(notes=len(notes), measures=len(meas), seconds=round(notes[-1]["t"] / 1000),
                 edition_fingers=sum(1 for n in notes if n.get("of") and not n.get("mine")),
                 my_fingers=sum(1 for n in notes if n.get("mine")))
    return stats


# ---------------------------------------------------------------- HMM (Nakamura, Saito, Yoshii 2019)
HMM_REPO = "statpianofingering/statpianofingering.github.io"
def hmm_binary():
    """Download the authors' published source (not redistributed here), compile the 2nd-order HMM once."""
    import zipfile
    base = os.path.join(CACHE, "nakamura")
    exe = os.path.join(base, "SourceCode", "Binary", "FingeringHMM2_Run")
    if os.path.exists(exe): return exe, os.path.join(base, "SourceCode", "Code", "param_FHMM2.txt")
    repo = os.path.join(CACHE, "statpianofingering")
    if not os.path.isdir(repo):
        os.makedirs(CACHE, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{HMM_REPO}", repo], check=True)
        subprocess.run(["git", "-C", repo, "checkout", "HEAD", "--", "data/SourceCode_v190921.zip"], check=True)
    zipfile.ZipFile(os.path.join(repo, "data", "SourceCode_v190921.zip")).extractall(base)
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    subprocess.run(["g++", "-O2", os.path.join(base, "SourceCode", "Code", "FingeringHMM2_Run_v181125.cpp"), "-o", exe], check=True)
    return exe, os.path.join(base, "SourceCode", "Code", "param_FHMM2.txt")

PNAME = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]
def hmm_fingering(notes):
    import tempfile
    exe, param = hmm_binary()
    seq = sorted(notes, key=lambda n: (n["t"], 0 if n["hand"] == "R" else 1, n["p"]))
    d = tempfile.mkdtemp(dir=CACHE)
    inp, outp = os.path.join(d, "in.txt"), os.path.join(d, "out.txt")
    with open(inp, "w") as fh:
        fh.write("//Version: PianoFingering_v170101\n")
        for k, n in enumerate(seq):
            ch = 0 if n["hand"] == "R" else 1
            fh.write(f"{k}\t{n['t']/1000:.4f}\t{max(n['off'], n['t']+30)/1000:.4f}\t{PNAME[n['p']%12]}{n['p']//12-1}\t64\t80\t{ch}\t{1 if ch == 0 else -1}\n")
    subprocess.run([exe, param, inp, outp, "0.556", "0.407", "0.474", "-5"], check=True, capture_output=True)
    out = [l.split() for l in open(outp) if l.strip() and not l.startswith("//")]
    assert len(out) == len(seq), (len(out), len(seq))
    for n, c in zip(seq, out):
        f = abs(int(c[7].split("_")[0]))
        n["f"] = n["of"] if n.get("of") else (f if 1 <= f <= 5 else 3)
    for h in "LR": set_poses([n for n in notes if n["hand"] == h], h)

def set_poses(hnotes, h):
    """Where each finger hovers, derived from the fingering: pressed fingers sit on their keys,
    the others keep a relaxed spacing next to the nearest pressed finger."""
    sgn = 1 if h == "R" else -1
    SP = [0, 1.25, 1.0, 1.0, 0.95]          # white-key distance from finger j to j+1 (index 1..4 used)
    groups = collections.defaultdict(list)
    for n in hnotes: groups[n["t"]].append(n)
    for t in sorted(groups):
        pressed = {n["f"]: xpos(n["p"]) / K for n in groups[t]}
        pos = [None] * 5
        for f, u in pressed.items(): pos[f - 1] = u
        for j in range(5):
            if pos[j] is not None: continue
            k = min(pressed, key=lambda f: abs(f - 1 - j)) - 1
            off = sum(SP[i] for i in range(min(j, k) + 1, max(j, k) + 1))
            pos[j] = pressed[k + 1] + sgn * (off if j > k else -off)
        for n in groups[t]: n["pos"] = [round(x, 2) for x in pos]

_orig_opt = Hand.optimize_seq
def _opt_lookahead(self, nseq, istart):
    """Let printed/own fingers inside the 9-note window constrain the search (not just their own note).
    Measured on human fingerings (PIG): roughly doubles the gain from anchors."""
    fixed = [self._anchor_of.get(id(n), 0) for n in nseq]
    if not any(fixed[1:]): return _orig_opt(self, nseq, istart)
    orig_skip = self.skip
    def skip(fa, fb, na, nb, hf, lr, level):
        f = fixed[level - 1] if level - 1 < len(fixed) else 0
        if f and fb != f: return True
        return orig_skip(fa, fb, na, nb, hf, lr, level)
    self.skip = skip
    try: out, v = _orig_opt(self, nseq, istart)
    finally: self.skip = orig_skip
    if v >= 1e10: out, v = _orig_opt(self, nseq, istart)
    return out, v

def finger_hand(hnotes, side):
    seq, groups = [], collections.defaultdict(list)
    for n in hnotes: groups[n["t"]].append(n)
    for cid, t in enumerate(sorted(groups)):
        g = sorted(groups[t], key=lambda n: n["p"])
        for k, n in enumerate(g):
            a = INote(); a.pitch = n["p"]; a.octave = n["p"] // 12; a.x = xpos(n["p"]); a.isBlack = is_black(n["p"])
            a.duration = max(.05, (n["off"] - n["t"]) / 1000); a.time = n["t"] / 1000; a.measure = n["mi"] + 1
            if n.get("of"): a.fingering = n["of"]
            if len(g) > 1:
                a.isChord = True; a.chordID = cid; a.chordnr = k; a.NinChord = len(g); a.time -= .05 * (len(g) - k - 1)
            seq.append((a, n))
    seq.sort(key=lambda an: an[0].time)
    if len(seq) < 2:
        for a, n in seq: n["f"] = n.get("of") or 3; n["pos"] = [xpos(n["p"]) / K + d for d in (-2, -1, 0, 1, 2)]
        return
    h = Hand(noteseq=[a for a, _ in seq], side=side, size="M"); h.verbose = False
    h.autodepth = False; h.depth = 9          # fixed full depth: +0.9pt agreement with pianists in our test
    h._anchor_of = {id(a): int(a.fingering) for a, _ in seq if a.fingering}
    if h._anchor_of: h.optimize_seq = _opt_lookahead.__get__(h, Hand)
    h.generate()
    sign = -1 if side == "left" else 1
    for (a, n), fpos in zip(seq, h.fingerseq):
        pos = [sign * x / K for x in fpos[1:]]
        f = int(a.fingering) or 1 + min(range(5), key=lambda i: abs(pos[i] - xpos(n["p"]) / K))
        n["f"] = n["of"] if n.get("of") else f
        n["pos"] = [round(x, 2) for x in pos]


def chord_pattern(k, span):
    if k == 2: return [1, 5] if span >= 7 else ([1, 3] if span >= 4 else [1, 2])
    if k == 3: return [1, 3, 5] if span >= 8 else [1, 2, 4]
    if k == 4: return [1, 2, 3, 5]
    return [1, 2, 3, 4, 5][:k]

def cleanup(notes, meas):
    """Rules on top of the search: same passage -> same fingers; no finger used twice in a chord."""
    st = collections.Counter()
    # 1. repeated sections (same written note played again) take the first time's fingering
    first = {}
    for n in notes:
        if n["base"] in first:
            src = first[n["base"]]
            if not n.get("of") and n["f"] != src["f"]: st["repeat_fixed"] += 1
            if not n.get("of"): n["f"], n["pos"] = src["f"], src["pos"]
        else: first[n["base"]] = n
    # 2. written-out repeats: identical measure content (per hand) -> same fingering as first time
    by = collections.defaultdict(list)
    for n in notes: by[(n["hand"], n["mi"])].append(n)
    seen = {}
    for (h, mi) in sorted(by, key=lambda k: k[1]):
        g = sorted(by[(h, mi)], key=lambda n: (n["t"], n["p"]))
        if len(g) < 8: continue          # short patterns: copying hurt accuracy in our test
        t0 = meas[mi][0]
        sig = (h, tuple((round((n["t"] - t0) / 10), n["p"]) for n in g))
        if sig in seen:
            for n, s in zip(g, seen[sig]):
                if not n.get("of") and n["f"] != s["f"]: st["phrase_fixed"] += 1; n["f"] = s["f"]; n["pos"] = s["pos"]
        else: seen[sig] = g
    # 3. chords: never the same finger twice
    g = collections.defaultdict(list)
    for n in notes: g[(n["t"], n["hand"])].append(n)
    for (t, h), v in g.items():
        if len(v) > 1 and len({x["f"] for x in v}) < len(v) and not any(x.get("of") for x in v):
            v.sort(key=lambda x: x["p"]); fs = chord_pattern(len(v), v[-1]["p"] - v[0]["p"])
            if h == "L": fs = sorted(fs, reverse=True)
            for x, f in zip(v, fs): x["f"] = f
            st["chord_fixed"] += 1
    return dict(st)


def repair_same_finger(notes):
    """Fast moves to a different key with the same finger are unplayable legato. This happens mostly
    where a printed finger was dropped into the HMM result. Move the free note to the neighbouring
    finger in the direction of travel."""
    fixed = 0
    for h in "LR":
        seq = [n for n in notes if n["hand"] == h and not n["grace"]]
        onset = collections.Counter(n["t"] for n in seq)
        seq = [n for n in seq if onset[n["t"]] == 1]            # single notes only; chords handled elsewhere
        for _ in range(3):
            changed = False
            for i in range(1, len(seq)):
                a, b = seq[i - 1], seq[i]
                if a["f"] != b["f"] or a["p"] == b["p"] or abs(a["p"] - b["p"]) > 4 or b["t"] - a["t"] >= 250: continue
                for tgt, ref, nxt in ((b, a, seq[i + 1] if i + 1 < len(seq) else None), (a, b, seq[i - 2] if i >= 2 else None)):
                    if tgt.get("of"): continue
                    up = (tgt["p"] > ref["p"]) == (tgt is b)
                    step = 1 if (up == (h == "R")) else -1
                    nf = ref["f"] + step
                    if 1 <= nf <= 5 and not (nxt and nxt["f"] == nf and nxt["p"] != tgt["p"]):
                        tgt["f"] = nf; fixed += 1; changed = True; break
            if not changed: break
    return fixed

HAND_MARKS = {
    "R": r"^(m\.\s?d\.?|M\.\s?D\.?|r\.\s?H\.?|R\.\s?H\.?|m\.\s?dr\.?|main droite|mano destra|rechte Hand|right hand|D\.)$",
    "L": r"^(m\.\s?g\.?|M\.\s?G\.?|m\.\s?s\.?|M\.\s?S\.?|l\.\s?H\.?|L\.\s?H\.?|main gauche|mano sinistra|linke Hand|left hand|G\.|S\.)$",
}
def apply_hand_markings(lines, notes):
    """m.d. / m.g. (and r.H. / l.H., R.H. / L.H., m.s.) written in the score are the composer's or editor's
    explicit instruction: the notes that follow the mark in that voice, up to the end of the bar
    (or the opposite mark), are fixed to that hand and never moved by separate_hands."""
    marks = []
    for L, ln in enumerate(lines, 1):
        if not ln.startswith("!") or ln.startswith("!!"): continue
        for F, tok in enumerate(ln.split("\t"), 1):
            for m in re.finditer(r"LO:TX:[^\t]*?t=([^:\t]+)", tok):
                txt = m[1].strip()
                for h, pat in HAND_MARKS.items():
                    if re.match(pat, txt): marks.append((L, F, h))
    bars = [i for i, ln in enumerate(lines, 1) if ln.startswith("=")]
    out = []
    for L, F, h in marks:
        end = next((b for b in bars if b > L), len(lines) + 1)
        nxt = [l2 for l2, f2, _ in marks if f2 == F and L < l2 < end]
        if nxt: end = min(nxt)
        hit = [n for n in notes if n.get("F") == F and L < n.get("L", 0) < end]
        for n in hit: n["forced"] = h
        if hit: out.append([L, "右手" if h == "R" else "左手", len(hit)])
    return out

def notes_staff_prepare(raw):
    for n in raw: n["staff"] = n["hand"]
    return []

def separate_hands(notes, meas):
    """Decide which hand plays each note.

    Outer voices stay with their staff: the lowest line of the lower staff is the left hand, the top
    line of the upper staff is the right hand. Inner notes (>= a 5th inside the outer voice of the same
    staff, sounding at the same time) form a movable unit per staff and measure. For every measure we
    pick the hand for each unit that is physically playable (no hand striking more than a 9th at once,
    no hand holding+playing more than a 10th, hands not crossing), preferring the written staff and
    keeping the same unit in the same hand from measure to measure."""
    for n in notes: n["staff"] = n.get("staff", n["hand"]); n["hand"] = n.get("forced") or n["staff"]
    by_t = collections.defaultdict(list)
    for n in notes: by_t[n["t"]].append(n)
    ts = sorted(by_t)
    # role: outer / inner
    for t in ts:
        for st in "LR":
            # outer voice: what sounds now, or arrives within a quarter of a second (a bass note entering
            # just after a chord still defines what is "inside")
            snd = [m for m in notes if m["staff"] == st and (m["t"] <= t < max(m["off"], m["t"] + 1)
                   or (st == "L" and t < m["t"] <= t + 260))]          # look ahead only for an entering bass
            if not snd: continue
            outer = min(snd, key=lambda m: m["p"]) if st == "L" else max(snd, key=lambda m: m["p"])
            for n in by_t[t]:
                if n["staff"] != st: continue
                far = (n["p"] - outer["p"] if st == "L" else outer["p"] - n["p"]) >= 7
                # struck together with the outer note and held as long = part of the same chord (e.g. bass octave)
                same_chord = (n["t"] == outer["t"] and abs((n["off"] - n["t"]) - (outer["off"] - outer["t"])) < 30
                              and abs(n["p"] - outer["p"]) <= 12)
                n["inner"] = far and not same_chord
    for n in notes: n.setdefault("inner", False)
    # units: (measure, staff, kind) - held inner notes and moving inner notes can belong to different hands
    units = collections.defaultdict(list)
    tmp = collections.defaultdict(list)
    for n in notes:
        if n["inner"] and not n.get("forced"): tmp[(n["mi"], n["staff"])].append(n)
    for (mi, st), g in tmp.items():
        durs = sorted(m["off"] - m["t"] for m in g); med = durs[len(durs) // 2]
        for m in g: units[(mi, st + ("S" if m["off"] - m["t"] >= 2.5 * med else "M"))].append(m)
    mis = sorted({n["mi"] for n in notes})
    notes_in = collections.defaultdict(list)
    for n in notes: notes_in[n["mi"]].append(n)

    def violations(mi):
        cost = 0
        group = collections.defaultdict(list)
        for n in notes_in[mi]: group[n["t"]].append(n)
        for t, g in group.items():
            snd = [m for m in notes if m["t"] <= t < max(m["off"], m["t"] + 1)]
            for h in "LR":
                st = [n["p"] for n in g if n["hand"] == h]
                if st and max(st) - min(st) > REACH: cost += 60       # wider than this hand can strike
                held = [m["p"] for m in snd if m["hand"] == h]
                if held and st and max(held + st) - min(held + st) > HOLD_REACH: cost += 8
            L = [m["p"] for m in snd if m["hand"] == "L"]; R = [m["p"] for m in snd if m["hand"] == "R"]
            if L and R and max(L) > min(R) + 2: cost += 6             # hands crossing
        return cost

    MISMATCH, SWITCH = 3, 8
    prev_best = {(): (0, None, {})}                                # assignment tuple -> (cost, back, map)
    trail = []
    for mi in mis:
        keys = [k for k in ("LM", "LS", "RM", "RS") if (mi, k) in units]
        opts = []
        for mask in range(1 << len(keys)):
            asg = {k: ("R" if k[0] == "L" else "L") if mask >> j & 1 else k[0] for j, k in enumerate(keys)}
            for st, h in asg.items():
                for n in units[(mi, st)]: n["hand"] = h
            local = violations(mi) + MISMATCH * sum(1 for st, h in asg.items() if h != st[0])
            for st in asg:
                for n in units[(mi, st)]: n["hand"] = st[0]
            opts.append((asg, local))
        cur = {}
        for asg, local in opts:
            best = None
            for pk, (pc, _, pmap) in prev_best.items():
                sw = sum(SWITCH for st, h in asg.items() if st in pmap and pmap[st] != h)
                c = pc + local + sw
                if best is None or c < best[0]: best = (c, pk)
            merged = dict(prev_best[best[1]][2]); merged.update(asg)
            key = tuple(sorted(asg.items()))
            if key not in cur or best[0] < cur[key][0]: cur[key] = (best[0], best[1], merged)
        trail.append((mi, cur, keys)); prev_best = cur
    # backtrack
    moves = []
    k = min(prev_best, key=lambda x: prev_best[x][0])
    for mi, cur, keys in reversed(trail):
        asg = dict(k)
        for st, h in asg.items():
            for n in units[(mi, st)]: n["hand"] = h
            if h != st[0]: moves.append([meas[mi][1], ("下の段" if st[0] == "L" else "上の段") + "の内声を" + ("右手" if h == "R" else "左手") + "に"])
        k = cur[k][1]
    return sorted(moves, key=lambda x: x[0])

def hand_audit(notes, meas):
    """Places a person should look at: left hand sounding above the right hand, or one hand striking
    more than a 10th at once. Reported only."""
    out = []
    ons = sorted({n["t"] for n in notes})
    for t in ons:
        snd = [n for n in notes if n["t"] <= t < max(n["off"], n["t"] + 1)]
        L = [n["p"] for n in snd if n["hand"] == "L"]; R = [n["p"] for n in snd if n["hand"] == "R"]
        m = meas[next(n["mi"] for n in notes if n["t"] == t)][1]
        if L and R and max(L) > min(R) + 2: out.append([m, "左手が右手より上"])
        for h, nm in (("L", "左手"), ("R", "右手")):
            st = [n["p"] for n in notes if n["t"] == t and n["hand"] == h]
            if len(st) > 1 and max(st) - min(st) > REACH: out.append([m, nm + "が手の届く幅を超えて同時に押す"])
    uniq = []
    for x in out:
        if x not in uniq: uniq.append(x)
    return uniq

def quality(notes):
    """Simple physical checks a teacher would flag."""
    q = collections.Counter()
    for h in "LR":
        seq = [n for n in notes if n["hand"] == h and not n["grace"]]
        for a, b in zip(seq, seq[1:]):
            if b["t"] == a["t"]: continue
            dt = b["t"] - a["t"]
            if a["f"] == b["f"] and a["p"] != b["p"] and abs(a["p"] - b["p"]) <= 4 and dt < 250: q["same_finger_step"] += 1
            if b["f"] == 1 and is_black(b["p"]) and dt < 250 and not is_black(a["p"]): q["thumb_on_black_fast"] += 1
            up = b["p"] > a["p"] if h == "R" else b["p"] < a["p"]
            if up and b["f"] < a["f"] and b["f"] != 1 and a["f"] != 1: q["odd_crossing"] += 1
    return {"check_" + k: v for k, v in q.items()}


# ---------------------------------------------------------------- marks
JP_TEXT = {
    "leggiero": ("軽やかに", "leggiero"), "sostenuto": ("音を保って、ゆったりと", "sostenuto"), "Fine.": ("ここで終わり", "Fine"),
    "FINE.": ("ここで終わり", "Fine"), "m.d.": ("右手で弾く", "m.d."), "m.g.": ("左手で弾く", "m.g."),
    "con fuoco.": ("炎のように激しく", "con fuoco"), "ed appassionato.": ("そして熱情をこめて", "ed appassionato"),
    "legatissimo.": ("できるだけなめらかにつなげて", "legatissimo"), "poco rallent.": ("少しずつ遅く", "poco rallent."),
    "smorz.": ("消えるように弱く、遅く", "smorz."), "sotto voce.": ("ひそやかに", "sotto voce"), "stretto.": ("せき込むように速めて", "stretto"),
    "cresc.": ("だんだん強く", "cresc."), "dim.": ("だんだん弱く", "dim."), "decresc.": ("だんだん弱く", "decresc."),
    "cantabile": ("歌うように", "cantabile"),
    "si deve suonare tutto questo pezzo delicatissimamente e senza sordini": ("曲全体をとても繊細に、ペダルを踏んで響かせて", "senza sordini"),
    "sempre %s senza sordini": ("ずっととても弱く、ペダルを踏んだまま", "sempre pp senza sordini"),
    "attacca": ("間をあけずに次の楽章へ", "attacca"), "dolce": ("やさしく", "dolce"), "ten.": ("音をしっかり保って", "ten."),
    "espressivo": ("表情豊かに", "espressivo"), "espr.": ("表情豊かに", "espr."), "rit.": ("だんだん遅く", "rit."),
    "ritard.": ("だんだん遅く", "ritard."), "rall.": ("だんだん遅く", "rall."), "a tempo": ("元の速さで", "a tempo"),
    "a tempo.": ("元の速さで", "a tempo"), "sempre legato": ("ずっとなめらかに", "sempre legato"), "legato": ("なめらかに", "legato"),
    "accel.": ("だんだん速く", "accel."), "rubato": ("自由に揺らして", "rubato"), "tranquillo": ("静かに落ち着いて", "tranquillo"),
}
DYN = {"ppp": ("ごく弱く", "ppp"), "pp": ("とても弱く", "pp"), "p": ("弱く", "p"), "mp": ("やや弱く", "mp"), "mf": ("やや強く", "mf"),
       "f": ("強く", "f"), "ff": ("とても強く", "ff"), "fff": ("ごく強く", "fff"), "sf": ("その音だけ強く", "sf"), "sfz": ("その音だけ強く", "sfz"),
       "fz": ("その音だけ強く", "fz"), "rfz": ("その音を急に強く", "rfz"), "fp": ("強く出してすぐ弱く", "fp")}

def extract_marks(song, lines, tm):
    times = collections.defaultdict(list)
    for e in tm:
        for i in e.get("on", []):
            L = int(re.match(r"note-L(\d+)", i)[1])
            if e["tstamp"] not in times[L]: times[L].append(e["tstamp"])
    def passes(L):
        for k in range(L, len(lines) + 1):
            if times.get(k):
                out = []
                for t in sorted(times[k]):
                    if not out or t - out[-1] > 1500: out.append(t)
                return out
        return []
    ev, spines, hair = [], [], None
    for L, ln in enumerate(lines, 1):
        toks = ln.split("\t")
        if ln.startswith("**"): spines = list(toks); continue
        if ln.startswith("!"):
            for m in re.finditer(r"!LO:TX:[^\t]*?t=([^:\t]+)", ln):
                if m[1] in JP_TEXT:
                    for t in passes(L): ev.append(dict(t=t, kind="text", jp=JP_TEXT[m[1]][0], it=JP_TEXT[m[1]][1]))
            continue
        if ln.startswith("*"):
            if "*ped" in toks:
                for t in passes(L): ev.append(dict(t=t, kind="ped", v=1))
            if "*Xped" in toks:
                for t in passes(L): ev.append(dict(t=t, kind="ped", v=0))
            if "*^" in toks or "*v" in toks:
                new, j = [], 0
                while j < len(toks):
                    if toks[j] == "*^": new += [spines[j], spines[j]]; j += 1
                    elif toks[j] == "*v":
                        k = j
                        while k < len(toks) and toks[k] == "*v": k += 1
                        new.append(spines[j]); j = k
                    else: new.append(spines[j]); j += 1
                spines = new
            continue
        if ln.startswith("=") or not ln.strip(): continue
        for j, sp in enumerate(spines):
            if sp == "**kern" and j < len(toks) and "TT" in toks[j] and "]" not in toks[j] and "_" not in toks[j]:
                for t in passes(L): ev.append(dict(t=t, kind="trill", jp="トリル（隣の音とすばやく交互に）", it="tr"))
        d = next((toks[j] for j, sp in enumerate(spines) if sp == "**dynam" and j < len(toks) and toks[j] not in (".", "")), None)
        if not d: continue
        if d in DYN:
            for t in passes(L): ev.append(dict(t=t, kind="dyn", jp=DYN[d][0], it=DYN[d][1]))
        elif d in ("<", ">"): hair = (d, passes(L))
        elif d in ("[", "]", "[[", "]]") and hair:
            ends = passes(L)
            for i, t0 in enumerate(hair[1]):
                t1 = ends[i] if i < len(ends) else t0 + 1100
                if t1 - t0 >= 600:   # shorter wedges are per-note swells, too noisy to label
                    ev.append(dict(t=t0, end=t1, kind="hairpin", jp="だんだん強く" if hair[0] == "<" else "だんだん弱く",
                                   it="cresc." if hair[0] == "<" else "dim."))
            hair = None
    ev.append(dict(t=0, kind="text", jp=song["tempo_text"][0], it=song["tempo_text"][1]))
    ped, cur = [], None
    for e in sorted([e for e in ev if e["kind"] == "ped"], key=lambda e: (e["t"], e["v"])):
        if e["v"] == 1:
            if cur is not None: ped.append([cur, e["t"]])
            cur = e["t"]
        elif cur is not None: ped.append([cur, e["t"]]); cur = None
    marks, seen, last_dyn = [], set(), {}
    for e in sorted([e for e in ev if e["kind"] != "ped"], key=lambda e: e["t"]):
        key = (e["t"], e["kind"], e.get("jp"))
        if key in seen: continue
        if e["kind"] == "dyn" and e["it"] in last_dyn and e["t"] - last_dyn[e["it"]] < 2500: continue
        if e["kind"] == "dyn": last_dyn = {e["it"]: e["t"]}
        seen.add(key); marks.append(e)
    return dict(marks=marks, pedal=ped)


# ---------------------------------------------------------------- main
def write_index():
    items = []
    for s in CATALOG:
        p = os.path.join(ROOT, "songs", s["id"] + ".json")
        if not os.path.exists(p): continue
        d = json.load(open(p, encoding="utf-8"))
        items.append(dict(id=s["id"], title=s["title"], composer=s["composer"], work=s["work"], level=s.get("level", ""),
                          measures=len(d["measures"]), notes=len(d["notes"]),
                          edition_fingers=sum(1 for n in d["notes"] if len(n) > 9 and n[9] == 1)))
    json.dump(items, open(os.path.join(ROOT, "songs", "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    ids = [s["id"] for s in CATALOG] if sys.argv[1:] == ["--all"] else sys.argv[1:]
    report_p = os.path.join(ROOT, "tools", "report.json")
    report = json.load(open(report_p)) if os.path.exists(report_p) else {}
    for sid in ids:
        song = next(s for s in CATALOG if s["id"] == sid)
        print("building", sid, "...", flush=True)
        report[sid] = build(song)
        print(" ", report[sid], flush=True)
    json.dump(report, open(report_p, "w"), ensure_ascii=False, indent=1)
    write_index()
