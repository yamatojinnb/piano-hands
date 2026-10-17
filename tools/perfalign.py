#!/usr/bin/env python3
"""
perfalign.py - align the piano-hands app score (songs/<id>.json) to a real
performance MIDI and extract per-note velocity, a beat-level tempo map and
sustain-pedal intervals mapped into app score time.

Two modes
---------
  asap : uses an ASAP / (n)ASAP piece folder (midi_score.mid +
         midi_score_annotations.txt + <perf>.mid + <perf>_annotations.txt).
         Score beats <-> performance beats are given by the dataset.
         app score --(measure DP + note anchors)--> ASAP score --(beats)--> perf
  dtw  : any performance MIDI with no annotations (e.g. ATEPP / MAESTRO files
         downloaded by hand).  app score --(onset-chroma DTW)--> perf.

Usage
  python3 perfalign.py asap <song_id> <asap_piece_dir> <perf_name|best|all> [--out DIR]
  python3 perfalign.py dtw  <song_id> <perf.mid> [--bpm-beats N] [--source TXT] [--license TXT] [--out DIR]

Times in the output JSON are milliseconds.  'perf_ms' values are on a
continuous performance timeline that starts at 0 at the performer's first
matched note; when the app expands a repeat the performer skipped, the
missing pass is filled with the other pass ("borrowed") and later times are
shifted, so tempo_map is always monotone in both columns.
"""
import argparse, json, os, sys, glob
import numpy as np
import pretty_midi

SONGS_DIR = '/home/claude/piano-hands/songs'
HERE = os.path.dirname(os.path.abspath(__file__))

# default beats per measure in the app scores (for beat-level tempo maps in dtw mode)
APP_BEATS = {'koinu': 3, 'kakumei': 4, 'hisou2': 2, 'gekkou1': 4}

# ---------------------------------------------------------------- loading

def load_app(song_id):
    d = json.load(open(os.path.join(SONGS_DIR, song_id + '.json'), encoding='utf-8'))
    notes = d['notes']
    on = np.array([n[0] for n in notes], float) / 1000.0
    dur = np.array([n[1] for n in notes], float) / 1000.0
    pitch = np.array([n[2] for n in notes], int)
    grace = np.array([n[6] for n in notes], int)
    trill = np.array([1 if n[7] else 0 for n in notes], int)
    meas = d['measures']
    mstart = np.array([m[0] for m in meas], float) / 1000.0
    mnum = [m[1] for m in meas]
    end = max(float((on + dur).max()), mstart[-1] + (mstart[-1] - mstart[-2]))
    mend = np.append(mstart[1:], end)
    # measure index of each note (by time; the note's own field is a measure *number*)
    nmeas = np.searchsorted(mstart, on + 1e-6, side='right') - 1
    return dict(id=song_id, d=d, on=on, dur=dur, pitch=pitch, grace=grace, trill=trill,
                mstart=mstart, mend=mend, mnum=mnum, nmeas=nmeas, end=end)


def load_perf(path):
    pm = pretty_midi.PrettyMIDI(path)
    notes = []
    ped = []
    for inst in pm.instruments:
        for n in inst.notes:
            notes.append((n.start, n.end, n.pitch, n.velocity))
        for c in inst.control_changes:
            if c.number == 64:
                ped.append((c.time, c.value))
    notes.sort()
    ped.sort()
    a = np.array(notes, float)
    return dict(path=path, on=a[:, 0], off=a[:, 1], pitch=a[:, 2].astype(int),
                vel=a[:, 3].astype(int), pedal=ped)


def pedal_intervals(ped, thresh=64, min_len=0.03):
    """CC64 events -> list of [down, up] in seconds."""
    out, down = [], None
    for t, v in ped:
        if v >= thresh and down is None:
            down = t
        elif v < thresh and down is not None:
            if t - down >= min_len:
                out.append([down, t])
            down = None
    if down is not None:
        out.append([down, ped[-1][0] if ped else down])
    return out


def read_ann(path):
    t, lab = [], []
    for line in open(path):
        p = line.rstrip('\n').split('\t')
        if len(p) < 3:
            continue
        t.append(float(p[0]))
        lab.append(p[2])
    return np.array(t), lab

# ---------------------------------------------------------------- generic helpers

def greedy_match(exp_t, exp_p, tol, perf_on, perf_pitch, forbid=None):
    """One-to-one same-pitch matching, closest |dt| first.
    exp_t: expected perf time per score note; tol: per-note tolerance (s)."""
    by_pitch = {}
    for j, p in enumerate(perf_pitch):
        by_pitch.setdefault(int(p), []).append(j)
    by_pitch = {k: np.array(v) for k, v in by_pitch.items()}
    cand = []
    for i in range(len(exp_t)):
        if np.isnan(exp_t[i]):
            continue
        js = by_pitch.get(int(exp_p[i]))
        if js is None:
            continue
        dt = perf_on[js] - exp_t[i]
        ok = np.abs(dt) < tol[i]
        for j, x in zip(js[ok], dt[ok]):
            cand.append((abs(x), i, int(j)))
    cand.sort()
    m = np.full(len(exp_t), -1, int)
    used = set()
    for _, i, j in cand:
        if m[i] < 0 and j not in used:
            m[i] = j
            used.add(j)
    return m


def running_median(x, y, xq, half):
    """median of y over points with |x - xq| <= half (x sorted)."""
    out = np.full(len(xq), np.nan)
    lo = np.searchsorted(x, xq - half, 'left')
    hi = np.searchsorted(x, xq + half, 'right')
    for k in range(len(xq)):
        if hi[k] > lo[k]:
            out[k] = np.median(y[lo[k]:hi[k]])
    return out


def lis_mask(a):
    """mask of a longest strictly increasing subsequence of a."""
    import bisect
    n = len(a)
    tails, tails_i, prev = [], [], [-1] * n
    for i, v in enumerate(a):
        k = bisect.bisect_left(tails, v)
        if k == len(tails):
            tails.append(v); tails_i.append(i)
        else:
            tails[k] = v; tails_i[k] = i
        prev[i] = tails_i[k - 1] if k > 0 else -1
    mask = np.zeros(n, bool)
    i = tails_i[-1] if tails_i else -1
    while i >= 0:
        mask[i] = True
        i = prev[i]
    return mask

# ---------------------------------------------------------------- measure DP (app <-> ASAP score)

def meas_hist(on, pitch, starts, ends):
    H = np.zeros((len(starts), 88))
    idx = np.searchsorted(starts, on + 1e-6, 'right') - 1
    for k, p in zip(idx, pitch):
        if 0 <= k < len(starts) and 21 <= p <= 108:
            H[k, p - 21] += 1
    return H


def cos(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0 if (na or nb) else 1.0
    return float(a @ b / (na * nb))


def align_measures(HA, HB, dA=None, dB=None, gap=-0.35):
    """Global alignment of two measure sequences (gaps = repeats taken/skipped).
    Moves: 1:1, 1:2, 2:1, gap-in-A, gap-in-B.  Returns list of (ia_list, ib_list)."""
    nA, nB = len(HA), len(HB)
    NEG = -1e9
    if dA is None:
        dA = np.ones(nA); dB = np.ones(nB)
    R = np.median(dB) / np.median(dA)   # tempo ratio between the two scores
    def dpen(a, b):  # duration consistency penalty
        return 0.8 * abs(np.log(max(b, 1e-6) / max(R * a, 1e-6)))
    # affine gaps: a skipped/extra repeat is one contiguous run of measures
    g_open, g_ext = -1.5, -0.08
    # states: 0 = last move was a match, 1 = gap run in B (app-only), 2 = gap run in A
    S = np.full((3, nA + 1, nB + 1), NEG)
    B = {}
    S[0, 0, 0] = 0
    for i in range(nA + 1):
        for j in range(nB + 1):
            if i == 0 and j == 0:
                continue
            # state 0: match moves from any state
            best, arg = NEG, None
            for (di, dj, extra) in ((1, 1, 0.0), (1, 2, 0.15), (2, 1, 0.15)):
                if i >= di and j >= dj:
                    ha = HA[i-di:i].sum(0); hb = HB[j-dj:j].sum(0)
                    sc = cos(ha, hb) - 0.5 - extra - dpen(dA[i-di:i].sum(), dB[j-dj:j].sum())
                    for st in range(3):
                        v = S[st, i-di, j-dj] + sc
                        if v > best: best, arg = v, (st, di, dj)
            S[0, i, j] = best; B[(0, i, j)] = arg
            # state 1: app measure i-1 unmatched
            if i:
                c = [(S[0, i-1, j] + g_open, 0), (S[1, i-1, j] + g_ext, 1), (S[2, i-1, j] + g_open, 2)]
                v, st = max(c)
                S[1, i, j] = v; B[(1, i, j)] = (st, 1, 0)
            if j:
                c = [(S[0, i, j-1] + g_open, 0), (S[2, i, j-1] + g_ext, 2), (S[1, i, j-1] + g_open, 1)]
                v, st = max(c)
                S[2, i, j] = v; B[(2, i, j)] = (st, 0, 1)
    path, i, j = [], nA, nB
    st = int(np.argmax(S[:, nA, nB]))
    while i or j:
        pst, di, dj = B[(st, i, j)]
        path.append((list(range(i - di, i)), list(range(j - dj, j))))
        i, j, st = i - di, j - dj, pst
    return path[::-1]

# ---------------------------------------------------------------- core: ASAP mode

def asap_mode(app, piece_dir, perf_name):
    sc = pretty_midi.PrettyMIDI(os.path.join(piece_dir, 'midi_score.mid'))
    s_notes = sorted((n.start, n.pitch) for i in sc.instruments for n in i.notes)
    s_on = np.array([x[0] for x in s_notes]); s_p = np.array([x[1] for x in s_notes])
    S, slab = read_ann(os.path.join(piece_dir, 'midi_score_annotations.txt'))
    P, plab = read_ann(os.path.join(piece_dir, perf_name + '_annotations.txt'))
    assert len(S) == len(P), 'beat count mismatch'
    perf = load_perf(os.path.join(piece_dir, perf_name + '.mid'))

    # ASAP score measures from downbeats (+ pickup measure if notes precede first db)
    db = [k for k, l in enumerate(slab) if l.startswith('db')]
    bstart = [S[k] for k in db]
    if s_on.min() < bstart[0] - 1e-3:
        bstart = [min(s_on.min(), S[0])] + bstart
    bstart = np.array(bstart)
    s_end = max(sc.get_end_time(), S[-1])
    bend = np.append(bstart[1:], s_end)

    HA = meas_hist(app['on'], app['pitch'], app['mstart'], app['mend'])
    HB = meas_hist(s_on, s_p, bstart, bend)
    path = align_measures(HA, HB, app['mend'] - app['mstart'], bend - bstart)
    a2b = {}  # app measure -> (asap measure list, app measure list) group
    for ia, ib in path:
        for i in ia:
            a2b[i] = (ia, ib)

    # ---- note-level anchors app -> ASAP score, measure-group local
    n = len(app['on'])
    exp_s = np.full(n, np.nan)   # expected ASAP-score time of each app note
    groups = [g for g in path if g[0] and g[1]]
    anchors_by_group = {}
    for g in groups:
        ia, ib = g
        a0, a1 = app['mstart'][ia[0]], app['mend'][ia[-1]]
        b0, b1 = bstart[ib[0]], bend[ib[-1]]
        sel = np.where((app['on'] >= a0 - 1e-6) & (app['on'] < a1 - 1e-6))[0]
        lin = b0 + (app['on'][sel] - a0) * (b1 - b0) / (a1 - a0)
        ssel = np.where((s_on >= b0 - 0.05) & (s_on < b1 + 0.05))[0]
        tol = np.full(len(sel), 0.25 * (b1 - b0))
        m = greedy_match(lin, app['pitch'][sel], tol, s_on[ssel], s_p[ssel])
        xa = [a0, a1]; xb = [b0, b1]
        ok = m >= 0
        if ok.sum() >= 2:
            pa = app['on'][sel][ok]; pb = s_on[ssel][m[ok]]
            # one anchor per unique app onset (median)
            ua = np.unique(pa); ub = np.array([np.median(pb[pa == u]) for u in ua])
            keep = lis_mask(list(ub))
            xa = list(ua[keep]); xb = list(ub[keep])
            # add measure boundaries if consistent
            if a0 < xa[0] and b0 < xb[0]: xa.insert(0, a0); xb.insert(0, b0)
            if a1 > xa[-1] and b1 > xb[-1]: xa.append(a1); xb.append(b1)
        xa, xb = np.array(xa), np.array(xb)
        anchors_by_group[tuple(ia)] = (xa, xb)
        exp_s[sel] = interp_extrap(app['on'][sel], xa, xb)

    # ---- ASAP score -> perf via beats
    def s2p(s):
        return interp_extrap(s, S, P)
    beat_perf = np.diff(P)
    exp_p = s2p(exp_s)
    loc_beat = np.interp(exp_s, S[:-1], beat_perf) if len(S) > 1 else np.full(n, 0.5)
    loc_beat = np.where(np.isnan(loc_beat), 0.5, loc_beat)

    m = two_pass_match(app, exp_p, loc_beat, perf)

    # ---- beat points in app order
    bp = []  # (app_time, perf_src_time, src_beat_index, borrowed)
    for g in path:
        ia, ib = g
        if not ia:
            continue  # performance-only measures (repeat the app does not have)
        if ib:
            xa, xb = anchors_by_group[tuple(ia)]
            b0, b1 = bstart[ib[0]], bend[ib[-1]]
            ks = np.where((S >= b0 - 1e-6) & (S < b1 - 1e-6))[0]
            for k in ks:
                bp.append([float(interp_extrap(np.array([S[k]]), xb, xa)[0]), float(P[k]), int(k), False])
    bp.sort(key=lambda x: x[0])
    info = dict(measure_path=[[list(map(int, a)), list(map(int, b))] for a, b in path],
                n_asap_beats=int(len(S)))
    return finish(app, perf, m, bp, info, borrow_unaligned=True, a2b=a2b)


def interp_extrap(x, xp, fp):
    x = np.asarray(x, float); xp = np.asarray(xp, float); fp = np.asarray(fp, float)
    y = np.interp(x, xp, fp)
    if len(xp) >= 2:
        lo = x < xp[0]; hi = x > xp[-1]
        y[lo] = fp[0] + (x[lo] - xp[0]) * (fp[1] - fp[0]) / max(xp[1] - xp[0], 1e-9)
        y[hi] = fp[-1] + (x[hi] - xp[-1]) * (fp[-1] - fp[-2]) / max(xp[-1] - xp[-2], 1e-9)
    return y


def two_pass_match(app, exp_p, loc_beat, perf):
    """pass 1: wide window around expected time; pass 2: correct expected time by the
    running median of residuals and re-match with a tighter window."""
    n = len(exp_p)
    grace = app['grace'].astype(bool)
    tol1 = np.maximum(0.15, 0.6 * loc_beat) + grace * 0.15
    m1 = greedy_match(exp_p, app['pitch'], tol1, perf['on'], perf['pitch'])
    ok = m1 >= 0
    if ok.sum() < 10:
        return m1
    order = np.argsort(exp_p[ok])
    xs = exp_p[ok][order]; rs = (perf['on'][m1[ok]] - exp_p[ok])[order]
    corr = running_median(xs, rs, np.nan_to_num(exp_p), half=1.5)
    corr = np.where(np.isnan(corr), 0, corr)
    exp2 = exp_p + corr
    tol2 = np.maximum(0.09, 0.35 * loc_beat) + grace * 0.15
    m2 = greedy_match(exp2, app['pitch'], tol2, perf['on'], perf['pitch'])
    return gap_fill(app, m2, exp2, perf)


def gap_fill(app, m, exp, perf, max_win=1.5, slack=0.04):
    """pass 3: an unmatched score note may still be played, just far from the
    beat-predicted time (fermata, final chord, big rubato).  Look for an unused
    same-pitch perf note strictly between the perf times of the neighbouring
    matched score onsets (earlier / later), if that window is short."""
    m = m.copy()
    used = set(int(j) for j in m if j >= 0)
    on = app['on']
    ok = np.where(m >= 0)[0]
    if len(ok) == 0:
        return m
    order = ok[np.argsort(on[ok])]
    mon = on[order]; mpt = perf['on'][m[order]]
    # running max / min so the window respects order on both sides
    run_max = np.maximum.accumulate(mpt)
    run_min = np.minimum.accumulate(mpt[::-1])[::-1]
    for i in np.where(m < 0)[0]:
        if np.isnan(exp[i]):
            continue
        k0 = np.searchsorted(mon, on[i] - 1e-6, 'left') - 1   # last matched onset strictly before
        k1 = np.searchsorted(mon, on[i] + 1e-6, 'right')      # first matched onset strictly after
        lo = run_max[k0] - slack if k0 >= 0 else exp[i] - max_win
        hi = run_min[k1] + slack if k1 < len(mon) else lo + max_win + 2 * slack
        if hi - lo > max_win + 2 * slack:
            continue
        c = np.where((perf['pitch'] == app['pitch'][i]) & (perf['on'] > lo) & (perf['on'] < hi))[0]
        c = [j for j in c if int(j) not in used]
        if c:
            j = min(c, key=lambda j: abs(perf['on'][j] - exp[i]))
            m[i] = j; used.add(int(j))
    return m


def finish(app, perf, m, bp, info, borrow_unaligned=False, a2b=None):
    """Build output timeline, velocities, pedal; borrow from repeated pass for
    app measures the performer skipped."""
    n = len(app['on'])
    vel = [int(perf['vel'][j]) if j >= 0 else None for j in m]
    borrowed = np.zeros(n, bool)

    # borrow: unaligned app measures -> same measure number elsewhere
    if borrow_unaligned and a2b is not None:
        aligned = {i for i, (ia, ib) in a2b.items() if ib}
        for mi in range(len(app['mstart'])):
            if mi in aligned:
                continue
            src = [k for k in aligned if app['mnum'][k] == app['mnum'][mi]]
            if not src:
                continue
            k = src[0]
            dt = app['mstart'][k] - app['mstart'][mi]
            # beats
            for b in [x for x in bp if app['mstart'][k] - 1e-6 <= x[0] < app['mend'][k] - 1e-6 and not x[3]]:
                bp.append([b[0] - dt, b[1], b[2], True])
            # notes: same pitch & same offset within the measure
            src_idx = np.where(app['nmeas'] == k)[0]
            for i in np.where(app['nmeas'] == mi)[0]:
                c = src_idx[(app['pitch'][src_idx] == app['pitch'][i]) &
                            (np.abs(app['on'][src_idx] - dt - app['on'][i]) < 1e-3)]
                if len(c) and m[c[0]] >= 0 and m[i] < 0:
                    m[i] = m[c[0]]; vel[i] = vel[c[0]]; borrowed[i] = True
        bp.sort(key=lambda x: x[0])

    # continuous output timeline by integrating beat intervals
    out = []
    for q, b in enumerate(bp):
        if q == 0:
            out.append(0.0)
            continue
        a = bp[q - 1]
        if b[2] is not None and a[2] is not None and b[2] == a[2] + 1:
            d = b[1] - a[1]
        else:  # jump in the performance: use previous local ratio
            ratio = ((out[q - 1] - out[q - 2]) / max(a[0] - bp[q - 2][0], 1e-6)) if q >= 2 else 1.0
            d = (b[0] - a[0]) * ratio
        out.append(out[-1] + d)
    bpa = np.array([b[0] for b in bp]); bpo = np.array(out); bps = np.array([b[1] for b in bp])

    # note perf onset on output timeline: offset relative to the governing beat point
    def perf_to_out(i, t_src):
        q = max(0, np.searchsorted(bpa, app['on'][i] + 1e-6, 'right') - 1)
        return bpo[q] + (t_src - bps[q])
    perf_on_ms = [None] * n
    for i in range(n):
        if m[i] >= 0:
            perf_on_ms[i] = perf_to_out(i, perf['on'][m[i]])

    # shift so the first matched note is at 0
    first = min(v for v in perf_on_ms if v is not None) if any(v is not None for v in perf_on_ms) else 0.0
    bpo = bpo - first
    perf_on_ms = [None if v is None else round((v - first) * 1000) for v in perf_on_ms]

    # pedal: walk consecutive beat-point pairs that are consecutive in the performance
    raw_ped = pedal_intervals(perf['pedal'])
    def down_at(t):
        return any(a <= t < b for a, b in raw_ped)
    events = []  # (app_time, state)
    for q in range(len(bp) - 1):
        a, b = bp[q], bp[q + 1]
        if not (a[2] is not None and b[2] is not None and b[2] == a[2] + 1):
            continue
        p0, p1 = a[1], b[1]
        events.append((a[0], down_at(p0)))
        for d0, u0 in raw_ped:
            for t, st in ((d0, True), (u0, False)):
                if p0 < t < p1:
                    events.append((a[0] + (t - p0) * (b[0] - a[0]) / (p1 - p0), st))
    # tail after last beat: extrapolate with last ratio
    if len(bp) >= 2:
        a, b = bp[-2], bp[-1]
        r = (b[0] - a[0]) / max(b[1] - a[1], 1e-6)
        events.append((b[0], down_at(b[1])))
        for d0, u0 in raw_ped:
            for t, st in ((d0, True), (u0, False)):
                if t > b[1]:
                    events.append((b[0] + (t - b[1]) * r, st))
    events.sort(key=lambda x: x[0])
    ped_app, cur = [], None
    for t, st in events:
        if st and cur is None:
            cur = t
        elif not st and cur is not None:
            if t - cur > 0.02:
                ped_app.append([round(cur * 1000), round(t * 1000)])
            cur = None
    if cur is not None:
        ped_app.append([round(cur * 1000), round(app['end'] * 1000)])

    tempo_map = [[round(a * 1000), round(o * 1000)] for a, o in zip(bpa, bpo)]
    # smoothed version: 3-beat moving average of the local tempo ratio
    if len(bpa) > 3:
        da = np.diff(bpa); do = np.diff(bpo)
        r = do / np.maximum(da, 1e-6)
        rs = np.convolve(np.pad(r, 1, mode='edge'), np.ones(3) / 3, 'valid')
        so = np.concatenate([[bpo[0]], bpo[0] + np.cumsum(rs * da)])
        tempo_smooth = [[round(a * 1000), round(o * 1000)] for a, o in zip(bpa, so)]
    else:
        tempo_smooth = tempo_map

    matched = int(sum(1 for j in m if j >= 0))
    res = dict(velocity=vel, perf_onset_ms=perf_on_ms,
               borrowed=[int(i) for i in np.where(borrowed)[0]],
               tempo_map=tempo_map, tempo_map_smooth=tempo_smooth, pedal=ped_app,
               match_rate=round(matched / n, 4), n_notes=n, n_matched=matched,
               n_perf_notes=int(len(perf['on'])),
               perf_notes_used=round(len(set(int(j) for j in m if j >= 0)) / len(perf['on']), 4))
    res['_m'] = m
    res.update(info)
    return res

# ---------------------------------------------------------------- DTW mode (no annotations)

def onset_chroma(on, pitch, vel, t0, t1, hop):
    nf = int(np.ceil((t1 - t0) / hop)) + 1
    F = np.zeros((nf, 12 + 4), np.float32)
    k = np.clip(((on - t0) / hop).astype(int), 0, nf - 1)
    for kk, p, v in zip(k, pitch, vel):
        F[kk, p % 12] += v
        F[kk, 12 + min(3, max(0, (p - 21) // 22))] += 0.5 * v  # coarse register
    # smear by one frame for tolerance
    G = F.copy(); G[1:] += 0.5 * F[:-1]; G[:-1] += 0.5 * F[1:]
    nrm = np.linalg.norm(G, axis=1, keepdims=True)
    return G / np.maximum(nrm, 1e-9), nrm[:, 0] > 0


def dtw_path(A, B, a_on, b_on):
    """slope-constrained DTW (steps (1,1),(1,2),(2,1),(1,3),(3,1)), vectorised by row."""
    C = 1.0 - A @ B.T
    # empty frames: neutral cost
    C[~a_on, :] = 0.5; C[:, ~b_on] = np.minimum(C[:, ~b_on], 0.5)
    nA, nB = C.shape
    INF = np.float32(1e9)
    D = np.full((nA, nB), INF, np.float32)
    T = np.zeros((nA, nB), np.int8)
    D[0, 0] = C[0, 0]
    steps = [(1, 1), (1, 2), (2, 1), (1, 3), (3, 1)]
    w = [1.0, 1.15, 1.15, 1.35, 1.35]
    for i in range(1, nA):
        best = np.full(nB, INF, np.float32); arg = np.zeros(nB, np.int8)
        for s, (di, dj) in enumerate(steps):
            if i - di < 0:
                continue
            cand = np.full(nB, INF, np.float32)
            cand[dj:] = D[i - di, :nB - dj] + w[s] * C[i, dj:]
            better = cand < best
            best[better] = cand[better]; arg[better] = s
        D[i] = best; T[i] = arg
    # end: last app frame, best perf frame in the last 10%
    j = int(np.argmin(D[-1, int(nB * 0.9):]) + int(nB * 0.9))
    i = nA - 1
    path = [(i, j)]
    while i > 0 and j > 0:
        di, dj = steps[T[i, j]]
        i, j = i - di, j - dj
        if i < 0 or j < 0:
            break
        path.append((i, j))
    return np.array(path[::-1])


def repeat_runs(app, min_len=2):
    """app measure indices of 2nd+ passes and of 1st passes of repeated sections
    (runs of >= min_len consecutive measures whose measure number occurs twice)."""
    mn = app['mnum']
    second = [k for k in range(len(mn)) if mn[k] in mn[:k]]
    first = [k for k in range(len(mn)) if mn[k] in mn[k + 1:]]
    def runs(ix):
        out, cur = [], []
        for k in ix:
            if cur and k == cur[-1] + 1: cur.append(k)
            else:
                if len(cur) >= min_len: out += cur
                cur = [k]
        if len(cur) >= min_len: out += cur
        return out
    return runs(second), runs(first)


def dtw_mode(app, perf_path, beats_per_measure, skip=None):
    """skip: app measure indices the performer did not play (filled by borrowing),
    or 'auto' to try {none, 2nd passes, 1st passes} of repeats and keep the best."""
    if skip == 'auto':
        sec, fst = repeat_runs(app)
        cands = [[]] + ([sec] if sec else []) + ([fst] if fst else [])
        best = None
        for c in cands:
            r = dtw_mode(app, perf_path, beats_per_measure, c)
            score = r['n_matched'] - 0.5 * len(r['borrowed'])  # real matches count more
            print('  dtw skip=%s -> match_rate %.3f (borrowed %d)' % (
                ('%d measures' % len(c)) if c else 'none', r['match_rate'], len(r['borrowed'])))
            if best is None or score > best[0]:
                best = (score, r)
        return best[1]
    skip = set(skip or [])
    perf = load_perf(perf_path)
    nM = len(app['mstart'])
    keep = [k for k in range(nM) if k not in skip]
    mdur = app['mend'] - app['mstart']
    cstart = {}
    acc = 0.0
    for k in keep:
        cstart[k] = acc; acc += mdur[k]
    cend = acc
    nm = app['nmeas']
    kept_note = np.array([k in cstart for k in nm])
    ct = np.full(len(app['on']), np.nan)   # compacted app time
    for i in np.where(kept_note)[0]:
        ct[i] = app['on'][i] - app['mstart'][nm[i]] + cstart[nm[i]]
    p0, p1 = perf['on'].min(), perf['on'].max()
    hop = 0.05 if (p1 - p0) < 200 else 0.08
    p0, p1 = perf['on'].min(), perf['on'].max()
    # scale the app score to the performance's overall duration so the DTW slope
    # limits (1/3..3) are relative to the performer's average tempo
    ct0 = np.nanmin(ct[kept_note])
    g = (p1 - p0) / max(np.nanmax(ct[kept_note]) - ct0, 1e-6)
    A, aon = onset_chroma((ct[kept_note] - ct0) * g, app['pitch'][kept_note], np.full(kept_note.sum(), 64), 0, (cend - ct0) * g, hop)
    B, bon = onset_chroma(perf['on'], perf['pitch'], perf['vel'], p0, p1, hop)
    path = dtw_path(A, B, aon, bon)
    ta = path[:, 0] * hop / g + ct0; tb = p0 + path[:, 1] * hop
    ua = np.unique(ta); ub = np.array([tb[ta == u].mean() for u in ua])
    exp_p = np.full(len(ct), np.nan)
    exp_p[kept_note] = np.interp(ct[kept_note], ua, ub)
    beat_app = np.median(mdur) / beats_per_measure
    slope = np.gradient(ub, ua) if len(ua) > 1 else np.ones(1)
    loc_beat = np.full(len(ct), beat_app)
    loc_beat[kept_note] = beat_app * np.interp(ct[kept_note], ua, np.clip(slope, 0.3, 3))
    m = two_pass_match(app, exp_p, loc_beat, perf)

    ok = m >= 0
    xa = ct[ok]; xb = perf['on'][m[ok]]
    ua2 = np.unique(xa); ub2 = np.array([np.median(xb[xa == u]) for u in ua2])
    lm = lis_mask(list(ub2)); ua2, ub2 = ua2[lm], ub2[lm]
    beats_c, beats_a = [], []
    for k in keep:
        nb = max(1, int(round(mdur[k] / beat_app)))
        for q in range(nb):
            beats_c.append(cstart[k] + q * mdur[k] / nb)
            beats_a.append(app['mstart'][k] + q * mdur[k] / nb)
    beats_c = np.array(beats_c)
    bt = interp_extrap(beats_c, ua2, ub2)
    for q, t in enumerate(beats_c):
        sel = np.abs(ua2 - t) <= beat_app * 1.0
        if sel.sum() >= 3:
            c = np.polyfit(ua2[sel] - t, ub2[sel], 1)
            bt[q] = c[1]
    bt = np.maximum.accumulate(bt + np.arange(len(bt)) * 1e-6)
    bp = [[float(a), float(b), q, False] for q, (a, b) in enumerate(zip(beats_a, bt))]
    a2b = {k: ([k], [k] if k in cstart else []) for k in range(nM)}
    info = dict(dtw_hop_s=hop, skipped_app_measures=sorted(int(k) for k in skip))
    return finish(app, perf, m, bp, info, borrow_unaligned=bool(skip), a2b=a2b)

# ---------------------------------------------------------------- CLI

ASAP_LICENSE = ('ASAP / (n)ASAP: "The dataset is made available under a Creative Commons '
                'Attribution Non-Commercial Share-Alike 4.0 (CC BY-NC-SA 4.0) license" '
                '(MAESTRO-derived performances: CC BY-NC-SA 4.0; e-Piano Competition MIDI).')


def write(res, song_id, outdir, meta):
    res = dict(res); res.pop('_m', None)
    o = dict(id=song_id, **meta)
    o.update(res)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, song_id + '.json')
    json.dump(o, open(path, 'w'), ensure_ascii=False, separators=(',', ':'))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['asap', 'dtw'])
    ap.add_argument('song_id')
    ap.add_argument('src')
    ap.add_argument('perf', nargs='?', default='best')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--bpm-beats', type=int, default=None)
    ap.add_argument('--source', default='')
    ap.add_argument('--license', default='')
    ap.add_argument('--performer', default='')
    ap.add_argument('--skip', default='auto',
                    help="dtw mode: 'auto', 'none', or comma list of app measure indices (0-based) not played")
    a = ap.parse_args()
    app = load_app(a.song_id)
    if a.mode == 'asap':
        names = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(a.src, '*.mid'))
                       if not f.endswith('midi_score.mid'))
        if a.perf not in ('best', 'all'):
            names = [a.perf]
        results = []
        for nm in names:
            r = asap_mode(app, a.src, nm)
            results.append((r['match_rate'], nm, r))
            print('%-22s match_rate=%.3f  perf_notes_used=%.3f  pedal_intervals=%d' %
                  (nm, r['match_rate'], r['perf_notes_used'], len(r['pedal'])))
        results.sort(key=lambda x: -x[0])
        rate, nm, r = results[0]
        meta = dict(source='ASAP / (n)ASAP dataset (github.com/fosfrancesco/asap-dataset, '
                           'github.com/CPJKU/asap-dataset)',
                    file=os.path.relpath(os.path.join(a.src, nm + '.mid'), a.src.rsplit('/', 3)[0]),
                    performer=nm, license=ASAP_LICENSE, mode='asap-beat-annotations',
                    candidates={x[1]: x[0] for x in results})
        print('->', write(r, a.song_id, a.out, meta), nm, rate)
    else:
        bpmb = a.bpm_beats or APP_BEATS.get(a.song_id, 4)
        skip = 'auto' if a.skip == 'auto' else ([] if a.skip == 'none' else [int(x) for x in a.skip.split(',')])
        r = dtw_mode(app, a.src, bpmb, skip)
        meta = dict(source=a.source or a.src, file=os.path.basename(a.src), performer=a.performer,
                    license=a.license, mode='dtw-no-annotations')
        print('match_rate=%.3f perf_notes_used=%.3f pedal=%d' % (r['match_rate'], r['perf_notes_used'], len(r['pedal'])))
        print('->', write(r, a.song_id, a.out, meta))


if __name__ == '__main__':
    main()
