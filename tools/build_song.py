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
                        mi=measure_index(t), of=orig_finger(L, F, S, staff)))
    raw.sort(key=lambda n: (n["t"], n["p"]))
    if song.get("move_secondary"):
        lh_on = {n["t"] for n in raw if n["hand"] == "L"}
        for n in raw:
            if n["hand"] == "R" and n["voice"] > 0 and n["t"] not in lh_on: n["hand"] = "L"
    notes = []
    for n in raw:
        if n["tie"]:
            for prev in reversed(notes):
                if prev["p"] == n["p"] and prev["hand"] == n["hand"]:
                    prev["off"] = max(prev["off"], n["off"]); break
            continue
        notes.append(n)

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
    out = dict(
        id=song["id"], title=song["title"], composer=song["composer"], work=song["work"],
        speed=song.get("speed", 0.5),
        source=dict(name=song["source_name"], url=song["source_url"], license=song.get("license", "")),
        measures=[[t, m] for t, m in meas],
        notes=[[n["t"], n["off"] - n["t"], n["p"], 0 if n["hand"] == "L" else 1, n["f"], meas[n["mi"]][1],
                1 if n["grace"] else 0, 1 if n["trill"] else 0, n["pos"],
                2 if n.get("mine") else (1 if n.get("of") else 0)] for n in notes],
        marks=marks,
    )
    json.dump(out, open(os.path.join(ROOT, "songs", song["id"] + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    stats.update(quality(notes)); stats["engine"] = engine
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
