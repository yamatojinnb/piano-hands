# Automatic piano fingering vs. human fingerings: evaluation of pianoplayer 3.0.2

Date: 2026-10-15. Everything here was run in `/home/claude/fingering-eval` (CPU only, 2 cores).

## 1. Data used (and its limits)

The official PIG site (beam.kisarazu.ac.jp, registration required, "only nonprofit, academic use") is not reachable from this shell, and no GitHub mirror of the full `FingeringFiles/` turned up. Repos checked: PRamoneda/*, robopianist, omnipiano, chirality, fingerflow, pygame-piano-animator, pydactyl, PianoFingering.jl, statpianofingering.github.io and fingeringdata.github.io. The fingerflow `fixtures/pig/*` files turned out to be synthetic, so they were not used.

**What was used:** a partial copy of real PIG annotations that a third-party repo committed as model-test output:
`https://github.com/sutkucanverdi/piano-fingering-ml` → `test_note_level_predictions.csv`. The columns kept are `file_id, note_id, onset_time, offset_time, midi_pitch, true_hand, true_finger`. `true_finger` is the first finger of a substitution, as an absolute value. That repo's own predictions were ignored.
- 23 PIG pieces (003 006 011 022 023 026 030 035 041 046 058 077 079 081 087 096 097 118 125 138 142 145 147) and **55 annotation files**, which is every PIG annotation of these pieces. 13 pieces have 2–6 annotators.
- The notes are truncated to multiples of 64 per file. About 80–90% of each piece's notes are kept (6,912 note positions; 17,737 annotated notes).
- Authenticity checks: the onsets and pitches match Nakamura's official HMM output files for the same pieces (e.g. 003-1 starts at Eb4, 0.00598087 s). Counting the PIG sequences in the lumikey training data gives exactly 55 files for these 23 pieces.
- 7 of the pieces (001–030) are in the PIG **official test split**. The models trained on PIG (Nakamura HMM, ArGNN/ArLSTM) were trained on 031–150, so for those models only the `test` column is clean.

Metrics follow Nakamura et al. 2019: **M_gen** is the match with each annotator, averaged over annotators, then over pieces. **M_high** is the max over annotators per piece. The human ceiling scores each pianist against the *other* pianists of the same piece, on notes where both use the same hand.

## 2. Method under test

`pianoplayer==3.0.2` is run exactly as in the app: hands are solved separately, x comes from the corrected `xpos()` layout, chords get `isChord/chordID/chordnr/NinChord` with the `-0.05*(len-k-1)` time stagger, and `Hand(size='M')` uses default autodepth. A solver output of 0 is filled with the "nearest finger" (the previous finger shifted by key distance). The package's `reader_PIG`/`keypos_midi` were **not** used. Identical per-hand inputs (same notes and hand split) are solved once and shared across annotators.

## Results

### A. Full automatic fingering (no anchors)

| Config | all-23 M_gen | all-23 M_high | test(001-030, 7 pcs) M_gen | test M_high | multi-annot (13 pcs) M_gen | multi M_high | notes |
|---|---|---|---|---|---|---|---|
| **Human vs. other humans (ceiling)** | 73.6 | 76.6 | 76.8 | 81.9 | 73.6 | 76.6 | multi-annotator pieces only; each pianist scored against the others |
| Baseline: always finger 1 | 30.2 | 31.0 | 28.9 | 30.5 | 29.9 | 31.5 |  |
| Baseline: position-in-8-note-window | 54.6 | 56.4 | 53.3 | 57.3 | 54.0 | 57.4 |  |
| pianoplayer pp_M **(app default)** | 59.4 | 61.4 | 60.2 | 64.9 | 60.7 | 64.5 | solver returned 0 for 394 notes (filled) |
| pianoplayer pp_XS | 52.2 | 53.9 | 51.1 | 54.6 | 51.1 | 54.4 | solver returned 0 for 63 notes (filled) |
| pianoplayer pp_S | 59.0 | 61.1 | 59.6 | 64.6 | 58.3 | 62.4 | solver returned 0 for 123 notes (filled) |
| pianoplayer pp_L | 56.4 | 58.4 | 55.7 | 60.6 | 55.9 | 59.8 | solver returned 0 for 623 notes (filled) |
| pianoplayer pp_XL | 55.4 | 57.2 | 53.3 | 56.5 | 54.6 | 57.9 | solver returned 0 for 733 notes (filled) |
| pianoplayer pp_M_d5 | 59.7 | 61.7 | 61.5 | 65.8 | 60.4 | 64.1 | solver returned 0 for 496 notes (filled) |
| pianoplayer pp_M_d7 | 60.3 | 62.4 | 61.2 | 66.2 | 60.9 | 65.1 | solver returned 0 for 560 notes (filled) |
| pianoplayer pp_M_d9 | 60.2 | 62.5 | 61.2 | 66.5 | 61.2 | 65.6 | solver returned 0 for 571 notes (filled) |
| pp_M + chord rule, always applied | 55.8 | 58.1 | 59.4 | 64.5 | 58.4 | 62.9 |  |
| pp_M + chord rule, only repair invalid chords | 60.6 | 62.6 | 61.9 | 66.5 | 62.0 | 65.9 | duplicate/out-of-order fingers in a chord |
| pp_M + consistency rule (pitch 4-gram) | 58.8 | 60.8 | 59.0 | 63.5 | 59.6 | 63.5 |  |
| pp_M + consistency rule (pitch 8-gram) | 59.3 | 61.4 | 59.7 | 64.4 | 60.6 | 64.5 |  |
| pp_M + consistency rule (pitch 12-gram) | 59.8 | 61.8 | 60.3 | 65.0 | 60.9 | 64.8 |  |
| pp_M + chord repair + consistency(12) | 61.0 | 63.0 | 61.9 | 66.5 | 62.2 | 66.1 |  |
| pp_S + chord repair | 59.4 | 61.6 | 60.5 | 65.6 | 59.0 | 63.3 |  |
| pp_L + chord repair | 58.4 | 60.6 | 59.2 | 64.2 | 58.3 | 62.6 |  |
| pp_M_d5 + chord repair | 60.8 | 62.8 | 62.9 | 67.1 | 61.8 | 65.5 |  |
| pp_M_d7 + chord repair | 61.3 | 63.6 | 62.8 | 67.9 | 62.3 | 66.6 |  |
| pp_M_d9 + chord repair | 61.3 | 63.6 | 62.8 | 68.1 | 62.4 | 66.9 |  |
| pp_M_d9 + chord repair + consistency(12) | 61.6 | 64.0 | 63.2 | 68.5 | 62.9 | 67.3 |  |
| Nakamura 1st-order HMM (released code+params) | 65.6 | 67.4 | 66.9 | 70.9 | 66.1 | 69.7 | trained on PIG 031-150: only test(001-030) column is clean |
| Nakamura 1st-order HMM (released code+params) + chord repair | 65.6 | 67.4 | 67.0 | 71.0 | 66.2 | 69.7 |  |
| Nakamura 2nd-order HMM | 69.8 | 72.4 | 70.3 | 74.8 | 69.7 | 74.5 | trained on PIG 031-150: only test(001-030) column is clean |
| Nakamura 2nd-order HMM + chord repair | 69.8 | 72.4 | 70.1 | 74.6 | 69.6 | 74.5 |  |
| Nakamura 3rd-order HMM | 70.2 | 72.8 | 69.7 | 74.5 | 69.9 | 74.7 | trained on PIG 031-150: only test(001-030) column is clean |
| Nakamura 3rd-order HMM + chord repair | 70.2 | 72.7 | 69.5 | 74.3 | 69.9 | 74.6 |  |
| ArLSTM (Ramoneda 2022, released weights) | 68.4 | 70.9 | 68.4 | 72.1 | 67.1 | 72.0 | trained on PIG 031-150: only test(001-030) column is clean |
| ArLSTM (Ramoneda 2022, released weights) + chord repair | 68.4 | 71.0 | 68.9 | 72.6 | 67.4 | 72.3 |  |
| ArGNN (Ramoneda 2022, released weights) | 67.3 | 69.2 | 67.6 | 72.4 | 67.1 | 70.8 | trained on PIG 031-150: only test(001-030) column is clean |
| ArGNN (Ramoneda 2022, released weights) + chord repair | 67.7 | 69.6 | 68.4 | 73.4 | 67.8 | 71.5 |  |
| @lumikey/piano-fingering-model 0.3.0 (npm, ONNX) as published | 75.8 | 78.1 | 81.1 | 86.0 | 77.4 | 81.7 | CONTAMINATED: trained on all of PIG incl. these pieces |
| @lumikey/piano-fingering-model 0.3.0 (npm, ONNX) as published + chord repair | 75.8 | 77.9 | 80.5 | 85.2 | 77.0 | 81.1 |  |
| lumikey Transformer retrained here WITHOUT the 23 eval pieces | 66.7 | 68.7 | 69.2 | 72.3 | 66.9 | 70.8 | clean on all columns (all 55 PIG files of the 23 pieces removed from training) |
| lumikey Transformer retrained here WITHOUT the 23 eval pieces + chord repair | 66.7 | 68.7 | 69.5 | 72.6 | 67.1 | 70.8 |  |

#### Per hand, M_gen (all 23 pieces / test 7 pieces)

| Config | RH all | LH all | RH test | LH test |
|---|---|---|---|---|
| pp_M | 56.7 | 61.9 | 59.6 | 63.3 |
| pp_M_d9 | 57.4 | 62.8 | 59.5 | 64.5 |
| nakamura_HMM2 | 66.9 | 71.5 | 67.5 | 72.3 |
| ArGNN | 61.5 | 72.5 | 61.5 | 74.1 |
| lumikey_heldout | 63.1 | 68.8 | 65.5 | 73.1 |

### B. Anchored mode: ~10% of notes fixed to the annotator's finger; scored on the other ~90%

| Config | all-23 M_gen | all-23 M_high | test(001-030, 7 pcs) M_gen | test M_high | multi-annot (13 pcs) M_gen | multi M_high | notes |
|---|---|---|---|---|---|---|---|
| [pp_M_anchor10_lookahead_s0] reference: pp_M without anchors, same scored notes | 59.7 | 61.7 | 60.2 | 65.3 | 60.8 | 64.7 |  |
| [pp_M_anchor10_lookahead_s0] pianoplayer with anchors | 62.9 | 64.8 | 64.9 | 69.5 | 64.2 | 67.9 | 9.7% notes anchored |
| [pp_M_anchor10_lookahead_s0] pianoplayer with anchors + chord repair | 64.1 | 66.0 | 66.5 | 71.0 | 65.6 | 69.3 |  |
| [pp_M_anchor10_lookahead_s0] reference: lumikey-heldout without anchors, same scored notes | 67.0 | 69.1 | 69.0 | 72.5 | 66.9 | 71.1 |  |
| [pp_M_anchor10_lookahead_s0] reference: ArGNN without anchors, same scored notes | 67.5 | 69.5 | 67.6 | 72.3 | 67.2 | 71.0 |  |
| [pp_M_anchor10_native_s0] reference: pp_M without anchors, same scored notes | 59.7 | 61.7 | 60.2 | 65.3 | 60.8 | 64.7 |  |
| [pp_M_anchor10_native_s0] pianoplayer with anchors | 61.4 | 63.2 | 62.9 | 67.3 | 62.5 | 66.1 | 9.7% notes anchored |
| [pp_M_anchor10_native_s0] pianoplayer with anchors + chord repair | 62.8 | 64.6 | 64.5 | 68.9 | 64.0 | 67.5 |  |
| [pp_M_anchor10_native_s0] reference: lumikey-heldout without anchors, same scored notes | 67.0 | 69.1 | 69.0 | 72.5 | 66.9 | 71.1 |  |
| [pp_M_anchor10_native_s0] lumikey-heldout with the same anchors | 69.8 | 72.0 | 71.7 | 76.0 | 69.4 | 73.7 |  |
| [pp_M_anchor10_native_s0] reference: ArGNN without anchors, same scored notes | 67.5 | 69.5 | 67.6 | 72.3 | 67.2 | 71.0 |  |
| [pp_M_anchor10_native_s0] ArGNN with the same anchors | 70.7 | 72.1 | 70.5 | 73.6 | 70.6 | 73.3 |  |

### Per-piece paired view (23 pieces, vs. the app default pp_M)
```
chord repair: mean delta +1.2 pts, better on 11/23 pieces, worse on 2
depth 9 fixed: mean delta +0.9 pts, better on 9/23 pieces, worse on 8
d9 + chord repair + consistency12: mean delta +2.3 pts, better on 17/23 pieces, worse on 5
size S: mean delta -0.4 pts, better on 10/23 pieces, worse on 12
size L: mean delta -3.0 pts, better on 5/23 pieces, worse on 18
lumikey retrained held-out: mean delta +7.4 pts, better on 19/23 pieces, worse on 4
Nakamura HMM2: mean delta +10.5 pts, better on 19/23 pieces, worse on 3
anchors native: mean delta +1.7 pts, better on 19/23 pieces, worse on 3
lumikey-heldout anchors: mean delta +2.9 pts, better on 20/23 pieces, worse on 3
anchors lookahead: mean delta +3.2 pts, better on 21/23 pieces, worse on 1
```
(The Nakamura HMM2 line over all 23 pieces includes its training pieces. On the 7 clean test pieces it is 70.3 vs 60.2.)

## 3. What helps (pianoplayer only)

| Change | Effect on M_gen (all 23) | Verdict |
|---|---|---|
| Hand size S / L / XL / XS | -0.4 / -3.0 / -4.0 / -7.2 | Keep **M**. A larger hand makes the solver fail more often (more zeros) and agree less with humans. |
| Fixed depth 7 or 9 (autodepth off) | +0.9 (9/23 pieces better, 8 worse) | Small, inconsistent gain. Slower. |
| Chord rule, **always** overwrite with 1-2-3-5 templates | **-3.6** | Harmful. Humans don't use fixed chord templates. |
| Chord rule, **repair only** (only when the solver gives duplicate or wrong-order fingers in a chord, or 0) | **+1.2** (11 better, 2 worse) | Cheap and safe. Fixes most solver zeros, which are mostly in chords. |
| Consistency rule (repeat an identical pitch n-gram's fingering) | 4-gram -0.6, 8-gram -0.1, 12-gram +0.4 | Neutral. Only long exact repeats help, and only a little. Useful for UX (stable fingering on repeats) rather than accuracy. |
| depth 9 + chord repair + consistency(12) | **+2.3** (17 better, 5 worse) → 61.6 | The best pianoplayer-only setting. Still far below learned models. |
| 10% human anchors, app's native handling (anchor only fixes its own note and the next start finger) | +1.7 on the remaining 90% (19/23 better) | Anchors barely propagate. |
| 10% anchors + **look-ahead constraint patch** (anchored notes inside the 9-note search window restrict the DFS; `pp_run._opt_lookahead`) | **+3.2** (21/23 better); +4.4 with chord repair | A worthwhile ~30-line patch if the app uses printed or user fingerings. |

## 4. State of the art (published PIG numbers, official 30-piece test set)

| Method | M_gen | M_high | Code + weights runnable here? |
|---|---|---|---|
| Human vs. other humans (Nakamura 2019) | 71.4 | 79.1 | n/a |
| Nakamura 1st/2nd/3rd-order HMM (2019/2020) | 61.7 / 64.3 / 64.5 | 68.3 / 70.8 / 71.0 | **Yes.** C++ source + trained params from github.com/statpianofingering/statpianofingering.github.io (`data/SourceCode_v190921.zip`). Compiled and run here, ~1 s for everything. |
| Nakamura DNN FF / LSTM | 61.5 / 61.3 | 66.2 / 66.1 | no weights |
| Ramoneda et al. 2022 ArLSTM / ArGNN (ThumbSet pretrain + PIG finetune) | 65.3 / 66.8 | 71.7 / 72.6 | **Yes.** github.com/PRamoneda/Automatic-Piano-Fingering (MIT, `models/*.pth`, PyTorch). Run here, greedy decoding, ~4 min. |
| Checklist models (Srivatsan & Berg-Kirkpatrick, ISMIR 2022); their re-run of HMM-3 = 70.2 | 68.1–69.5 | – | no released weights found |
| Pitch-difference fingering match (Guan et al. 2022) | HMM3 +2–3 pts | – | no code |
| Penner & Hindle 2026 (score transcription with fingering) | joint hand+finger 56.6 (different metric) | – | Zenodo package (not reachable from shell) |
| chirality (pip, 2026): Nakamura HMMs + learned Parncutt rules, reported 65.4 | 65.4 | – | weights on HuggingFace (blocked from shell). Its HMM part is the same model as above. |
| @lumikey/piano-fingering-model 0.3.0 (npm, MIT, 0.5 MB ONNX Transformer; runs in browser) | not published | – | **Yes**, but it is trained on *all* of PIG. Retrained here without the 23 eval pieces for a fair number. |

Sources: arXiv 1904.10237 (Nakamura et al.), Ramoneda et al. 2022 PDF (eita-nakamura.github.io), arXiv 2209.05622 (checklist models), arXiv 2108.09058, arXiv 2609.28787, huggingface.co/vibetuned/chirality-models, github.com/lumikey/piano-fingering-model.

Note that the absolute levels on this 23-piece partial subset are higher than the published ones (e.g. HMM2 70.3 here vs. 64.3 published), because it is a different and truncated piece set. Compare rows within the table, not across papers.

## 5. Recommendation

1. **The biggest win is replacing the solver, not tuning it.** On clean held-out pieces, statistical and learned models beat pianoplayer by 7–10 points M_gen. That closes about 2/3 of the gap to human-human agreement:
   - Nakamura HMM2/HMM3: 70.3/69.7 vs. 60.2 on the 7 clean test pieces. The C++ source is tiny and fast, and porting the Viterbi to JS/Python is easy.
   - A small Transformer (lumikey architecture, ~100K params, ONNX, browser-capable) trained without the eval pieces: 66.7 (all 23) / 69.2 (test 7). It also supports anchors natively: +2.9 with 10% anchors.
   - **Licensing caveat:** every one of these is trained on PIG, whose terms allow only "nonprofit, academic use". For a commercial app you would need permission from the PIG authors or your own training data. The lumikey package is MIT, but its weights are PIG-derived.
2. **If you stay on pianoplayer**, ship `size='M'`, `depth=9` (or keep autodepth for speed), **chord repair only** (never force chord templates), and optionally the 12-gram consistency pass. Expected gain is about +2 points (59.4 → 61.6).
3. **If the app has edition or user fingerings, add the look-ahead anchor patch.** It doubles the benefit of anchors (+1.7 → +3.2, +4.4 with chord repair).
4. Hand-size presets should not be used as an "accuracy" knob. L/XL are clearly worse against these (adult professional) annotators.

## 6. Limitations

- Only 23 of 150 PIG pieces (17.7k annotated notes) were used, truncated to ~85% of each piece. The data comes from a third-party upload, and its redistribution status under the PIG terms is doubtful; it was used here only for evaluation. With an official PIG download (free registration) the scripts run on the full set unchanged after a small loader change in `build_data.py`.
- Only 7 pieces (33 annotation files) are clean for the PIG-trained HMM/ArGNN models, so their deltas carry about ±2–3 pt uncertainty. The lumikey-heldout model is clean on all 23 pieces, but it was retrained for only 15 epochs (the original used up to 300 with early stopping), so it is probably a slight underestimate.
- Anchored runs use one random seed (anchors are drawn from the annotator being scored). M_high in anchored mode is not meaningful.
- ArGNN was run with greedy decoding, as in the repo's evaluation. The paper's best numbers may use beam search.
- The "nearest finger" fallback for solver zeros is my reimplementation of the app's description.

## Files
- `build_data.py` (CSV → `data/pig_partial.json`), `pp_run.py` (app-equivalent pianoplayer call + look-ahead anchor patch), `evaluate.py` (configs, rules, metrics), `anchors.py`, `run_hmm.py`, `run_argnn.py`, `run_lumikey.py`, `retrain_lumikey.py`, `run_lumikey_heldout_anchor.py`, `report.py` (→ `results_tables.md`), `paired.py` (→ `results_paired.txt`), predictions in `preds/*.json`, external code in `ext/`.
