"""Real pipeline/API fixture server; temporary data is removed on shutdown."""
import gzip
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.pipeline.process import process
import uvicorn

with tempfile.TemporaryDirectory(prefix="deadlock-web-") as temporary:
    base = Path(temporary)
    raw = base / "raw"
    raw.mkdir()
    fixtures = ROOT / "tests/fixtures"
    for name in ("msgs.tsv", "ents.jsonl"):
        with gzip.open(fixtures / f"133162474.{name}.gz", "rb") as src, (raw / name).open("wb") as dst:
            shutil.copyfileobj(src, dst)
    out = base / "data/matches/133162474"
    out.mkdir(parents=True)
    header = json.loads((fixtures / "133162474.header.json").read_text(encoding="utf-8"))
    summary = process(raw, header, out)
    (out / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    evil = base / "data/matches/133162475"
    shutil.copytree(out, evil)
    data = json.loads((evil / "match.json").read_text(encoding="utf-8"))
    old = data["players"][0]["name"]
    # Replace references too, including damage matrix keys.
    serialized = json.dumps(data).replace(json.dumps(old), json.dumps("<img src=x onerror=alert(1)>"))
    data = json.loads(serialized)
    data["match"]["matchId"] = 133162475
    (evil / "match.json").write_text(json.dumps(data), encoding="utf-8")
    summary["matchId"] = 133162475
    summary["heroes"][0]["name"] = "<img src=x onerror=alert(1)>"
    (evil / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    duplicate = base / "data/matches/133162476"
    shutil.copytree(out, duplicate)
    same = json.loads((duplicate / "match.json").read_text(encoding="utf-8"))
    same["match"]["matchId"] = 133162476
    for player in same["players"]:
        player["name"] = "Одинаковый"
    (duplicate / "match.json").write_text(json.dumps(same), encoding="utf-8")
    summary["matchId"] = 133162476
    (duplicate / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    fake = base / "fake.py"
    fake.write_text('import pathlib, shutil, sys, time\ntime.sleep(1)\nfor name in ("msgs.tsv", "ents.jsonl"):\n    shutil.copyfile(pathlib.Path(%r) / name, pathlib.Path(sys.argv[2]) / name)\n' % str(raw), encoding="utf-8")
    os.environ["DATA_DIR"] = str(base / "data")
    os.environ["DLDUMP_BIN"] = shlex.join([sys.executable, str(fake)])
    uvicorn.run("app.server.main:app", host="127.0.0.1", port=8765, log_level="warning")
