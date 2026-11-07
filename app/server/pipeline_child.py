import json
import sys
from pathlib import Path
from app.pipeline.process import process
from app.pipeline.errors import DemoError
from app.server.jobs import read_header

raw, demo, staging = map(Path, sys.argv[1:])
try:
    summary = process(raw, read_header(demo), staging)
    (staging / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
except DemoError as exc:
    (staging / 'error.json').write_text(json.dumps(exc.user_message), encoding='utf-8')
    sys.exit(1)
