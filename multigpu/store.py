"""Small persistent store. Provider passwords and access tokens never enter it."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.controller_lock = (self.root / "controller.lock").open("a+b")
        self.controller_lock.seek(0, 2)
        if self.controller_lock.tell() == 0:
            self.controller_lock.write(b"0")
            self.controller_lock.flush()
        self.controller_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.controller_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.controller_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.controller_lock.close()
            raise RuntimeError("A MultiGPU controller already uses this data directory. Open its dashboard or choose another --data-dir.") from None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "state.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))")
        self.db.commit()

    def put(self, kind, key, data):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)", (kind, key, json.dumps(data, allow_nan=False)))
            self.db.commit()
        return data

    def get(self, kind, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        with self.lock:
            rows = self.db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self):
        with self.lock:
            self.db.close()
            self.controller_lock.close()
