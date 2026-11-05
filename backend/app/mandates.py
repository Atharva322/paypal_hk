"""Mandates: what the user asked for, kept so every order can be held against it.

draft -> confirmed. Only the user's confirmation moves a mandate on, and a confirmed
mandate never changes: a new request makes a new mandate.
"""

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    mandate_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    request_text TEXT NOT NULL,
    draft TEXT NOT NULL,
    confirmed TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT
)
"""


@dataclass(frozen=True)
class Mandate:
    mandate_id: str
    status: str
    request_text: str
    draft: dict
    confirmed: dict | None


class MandateLog:
    def __init__(self, path: str):
        self._path = path
        with closing(self._connect()) as db, db:
            db.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def record_draft(self, request_text: str, draft: dict) -> str:
        mandate_id = uuid.uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO mandates (mandate_id, status, request_text, draft) "
                "VALUES (?, 'draft', ?, ?)",
                (mandate_id, request_text, json.dumps(draft)),
            )
        return mandate_id

    def get(self, mandate_id: str) -> Mandate | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT mandate_id, status, request_text, draft, confirmed "
                "FROM mandates WHERE mandate_id = ?",
                (mandate_id,),
            ).fetchone()
        if row is None:
            return None
        return Mandate(row[0], row[1], row[2], json.loads(row[3]), row[4] and json.loads(row[4]))

    def confirm(self, mandate_id: str, confirmed: dict) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "UPDATE mandates SET status = 'confirmed', confirmed = ?, "
                "confirmed_at = CURRENT_TIMESTAMP WHERE mandate_id = ? AND status = 'draft'",
                (json.dumps(confirmed), mandate_id),
            )
            return cursor.rowcount == 1
