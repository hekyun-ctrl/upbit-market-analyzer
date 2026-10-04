"""Durable public-signal audit; no credentials or actual account data."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class AuditStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, stored_at TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS event_kind ON events(kind,id)")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        self.started_at = self.get("collection_started_at")
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()
            self.set("collection_started_at", self.started_at)

    def append(self, kind: str, payload: dict):
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256((kind + text).encode()).hexdigest()
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO events(kind,key,payload,stored_at) VALUES(?,?,?,?)",
                (kind, key, text, datetime.now(timezone.utc).isoformat()))

    def read(self, kind: str, limit: int | None = None):
        query = "SELECT payload FROM events WHERE kind=? ORDER BY id DESC"
        args = [kind]
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(query, args)]

    def get(self, key: str):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def set(self, key: str, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (key, json.dumps(value)))

    def prune(self, days=30):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.lock, self.db:
            self.db.execute("DELETE FROM events WHERE stored_at < ?", (cutoff,))


def configured_audit_path():
    explicit = os.getenv("CANDIDATE_AUDIT_DB_PATH")
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    return explicit or (str(Path(mount) / "candidate-audit.sqlite3") if mount else None)
