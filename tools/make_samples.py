"""Prepare the piano sound files in assets/piano/ from Salamander Grand Piano V3 (Alexander Holm, CC BY 3.0).

usage: python tools/make_samples.py          (needs npm and ffmpeg)

- 4 loudness layers (velocity 4 / 8 / 12 / 16 of the 16 recorded) -> assets/piano/v1 .. v4/<midi>.mp3
  recorded every 3 semitones from A0 to C8; the app shifts pitch by at most 1.5 semitones
- key-release noises (88 keys)                                     -> assets/piano/rel/<midi>.mp3
- stereo, 96 kbit/s; low notes keep up to 10 s of their natural decay, high notes less
"""
import os, subprocess, tempfile, glob, shutil, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "piano")
LAYERS = {"v1": 4, "v2": 8, "v3": 12, "v4": 16}
PC = {"C": 0, "D#": 3, "F#": 6, "A": 9}

def pack(name, d):
    subprocess.run(["npm", "pack", "-q", name], cwd=d, check=True, capture_output=True)
    subprocess.run(["sh", "-c", "tar xzf *.tgz"], cwd=d, check=True)
    return os.path.join(d, "package", "audio")

def encode(src, dst, seconds):
    fade = min(1.2, seconds / 3)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", f"{seconds:.2f}",
                    "-af", f"afade=t=out:st={seconds - fade:.2f}:d={fade:.2f}",
                    "-ac", "2", "-ar", "44100", "-b:a", "96k", dst], check=True)

def length_for(midi):
    return max(3.5, min(10.0, 10.0 - (midi - 21) * 0.09))

if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    for old in glob.glob(os.path.join(OUT, "*.mp3")): os.remove(old)       # previous single-layer set
    keys = set()
    for layer, vel in LAYERS.items():
        d = os.path.join(tmp, layer); os.makedirs(d)
        src = pack(f"@audio-samples/piano-mp3-velocity{vel}", d)
        os.makedirs(os.path.join(OUT, layer), exist_ok=True)
        for f in sorted(os.listdir(src)):
            name = f[: -len(f"v{vel}.mp3")]
            n = name.rstrip("0123456789"); octave = int(name[len(n):])
            midi = 12 * (octave + 1) + PC[n]
            encode(os.path.join(src, f), os.path.join(OUT, layer, f"{midi}.mp3"), length_for(midi))
            keys.add(midi)
        print(layer, "done", flush=True)
    d = os.path.join(tmp, "rel"); os.makedirs(d)
    src = pack("@audio-samples/piano-mp3-release", d)
    os.makedirs(os.path.join(OUT, "rel"), exist_ok=True)
    for f in os.listdir(src):
        k = int(f[3:-4]); midi = 20 + k                                       # rel1 = A0 (MIDI 21)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(src, f), "-ac", "2", "-b:a", "96k",
                        os.path.join(OUT, "rel", f"{midi}.mp3")], check=True)
    shutil.copy(os.path.join(tmp, "v1", "package", "LICENSE"), os.path.join(OUT, "LICENSE-audio-samples.txt"))
    json.dump({"layers": list(LAYERS), "keys": sorted(keys), "release": True}, open(os.path.join(OUT, "index.json"), "w"))
    print("keys", sorted(keys))
