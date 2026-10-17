"""Impulse response of the piano's undamped strings (sustain pedal down) -> assets/ir/strings.wav

Every one of the 88 strings is a set of slowly decaying partials. Convolving the played sound with the sum of
them makes each string ring in proportion to how much of its frequencies the played notes contain - the
sympathetic resonance you hear when the pedal is held. Low strings ring longer than high ones; bass strings
sit a little to the left, treble to the right (player's view). The app mixes it in only while the pedal is down.

usage: python tools/make_resonance.py
"""
import os, json, numpy as np
from scipy.io import wavfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR, LEN = 44100, 1.6
rng = np.random.default_rng(7)
t = np.arange(int(SR * LEN)) / SR
out = np.zeros((len(t), 2))
for k in range(21, 109):
    f0 = 440 * 2 ** ((k - 69) / 12)
    B = 10 ** (-4.2 + (k - 21) / 87 * 1.6)                 # inharmonicity: small in the bass, larger in the treble
    tau = 1.1 * 0.965 ** (k - 21) + 0.12                    # ~1.2 s low strings, ~0.17 s top strings
    pan = (k - 21) / 87
    for h in range(1, 13):
        f = h * f0 * np.sqrt(1 + B * h * h)
        if f > 9000: break
        a = 1 / h ** 1.2
        y = a * np.exp(-t / (tau / (1 + 0.25 * (h - 1)))) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
        out[:, 0] += y * np.cos(pan * np.pi / 2 * .5 + np.pi / 8)
        out[:, 1] += y * np.sin(pan * np.pi / 2 * .5 + np.pi / 8)
ramp = np.minimum(1, t / 0.004)[:, None]                   # strings take a moment to start moving
fade = np.minimum(1, (LEN - t) / 0.2)[:, None]
out *= ramp * fade
out /= np.abs(out).max()
wavfile.write(os.path.join(ROOT, "assets", "ir", "strings.wav"), SR, (out * 32000).astype(np.int16))
print("energy per channel", (out ** 2).sum(0))
