"""Turn a "[運指]" issue into songs/fix/<id>.json.

Reads the issue body from the ISSUE_BODY environment variable, takes the JSON in its ```json block
({"song": "<id>", "edits": {"<t>:<pitch>:<L|R>": finger}}), checks it and merges it into the fix file.
Prints the song id so the workflow can rebuild that song.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
body = os.environ.get("ISSUE_BODY", "")
m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
if not m:
    sys.exit("修正データ（```json ... ```）が見つかりません")
data = json.loads(m.group(1))
song = str(data.get("song", ""))
catalog = {s["id"] for s in json.load(open(os.path.join(ROOT, "tools", "catalog.json"), encoding="utf-8"))}
if song not in catalog:
    sys.exit(f"曲 {song!r} はカタログにありません")
edits = data.get("edits", {})
if not isinstance(edits, dict) or not edits:
    sys.exit("修正が空です")
clean = {}
for k, v in edits.items():
    if not re.fullmatch(r"\d+:\d+:[LR]", str(k)) or int(v) not in (1, 2, 3, 4, 5):
        sys.exit(f"形式が正しくありません: {k}={v}")
    clean[str(k)] = int(v)

path = os.path.join(ROOT, "songs", "fix", song + ".json")
fix = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"song": song, "edits": {}}
fix["edits"].update(clean)
fix["edits"] = dict(sorted(fix["edits"].items(), key=lambda kv: (int(kv[0].split(":")[0]), kv[0])))
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(fix, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(song)
