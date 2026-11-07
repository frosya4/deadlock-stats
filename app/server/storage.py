import json
import sqlite3
import shutil
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def closing_connection(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.matches = root / 'matches'
        self.tmp = root / 'tmp'
        self.matches.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.index = root / 'index.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hashes (hash TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE)')
        self.prune()

    def connect(self):
        # Each operation owns its connection (also safe across FastAPI threads).
        return closing_connection(self.index)

    def size(self):
        return sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())

    def prune(self):
        with self.connect() as db:
            for digest, match_id in db.execute('SELECT hash, id FROM hashes'):
                if not (self.matches / match_id / 'match.json').is_file():
                    db.execute('DELETE FROM hashes WHERE hash = ?', (digest,))

    def cleanup(self):
        self.prune()
        for path in [*self.tmp.iterdir(), *self.matches.glob('.*.tmp')]:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def lookup(self, digest):
        with self.connect() as db:
            row = db.execute('SELECT id FROM hashes WHERE hash = ?', (digest,)).fetchone()
        if row and row[0] == 'u' + digest[:12] and (self.matches / row[0] / 'match.json').is_file():
            return row[0]
        self.prune()
        return None

    def record(self, digest, match_id):
        # Upload index must never alias an official numeric match.
        if match_id != 'u' + digest[:12]:
            raise ValueError('Only upload hash identities may be indexed')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO hashes(hash, id) VALUES (?, ?)', (digest, match_id))

    def summaries(self):
        result = []
        for path in self.matches.iterdir():
            if not path.is_dir() or path.name.startswith('.') or not (path / 'match.json').is_file():
                continue
            try:
                summary = json.loads((path / 'summary.json').read_text(encoding='utf-8'))
                result.append((path.stat().st_mtime, summary))
            except (OSError, ValueError):
                continue
        return [summary for _, summary in sorted(result, key=lambda item: item[0], reverse=True)]
