"""Decision log, and the state machine that stands between approval and capture.

created -> checked -> capturing -> captured
created/checked -> cancelled

Each move is a conditional UPDATE, so a confirm racing a cancel has exactly one winner.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    order_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    cart_total_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    checked_total_cents INTEGER,
    checked_payee TEXT,
    capture_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    risk_level TEXT,
    risk_score INTEGER,
    rule_hits TEXT,
    checked_at REAL,
    mandate_id TEXT,
    shown TEXT
)
"""

# Columns added after the first release, for logs that already exist.
ADDED_COLUMNS = {
    "risk_level": "TEXT",
    "risk_score": "INTEGER",
    "rule_hits": "TEXT",
    "checked_at": "REAL",
    "mandate_id": "TEXT",
    # What the user was shown and heard at the check: the consent they gave or refused.
    "shown": "TEXT",
}


@dataclass(frozen=True)
class Decision:
    order_id: str
    status: str
    cart_total_cents: int
    currency: str
    checked_total_cents: int | None
    checked_payee: str | None
    capture_id: str | None
    risk_level: str | None
    checked_at: float | None
    mandate_id: str | None = None


class DecisionLog:
    def __init__(self, path: str):
        self._path = path
        with closing(self._connect()) as db, db:
            db.execute(SCHEMA)
            existing = {row[1] for row in db.execute("PRAGMA table_info(decisions)")}
            for name, sql_type in ADDED_COLUMNS.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE decisions ADD COLUMN {name} {sql_type}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _update(self, order_id: str, allowed_from: tuple[str, ...], **fields) -> bool:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        placeholders = ", ".join("?" for _ in allowed_from)
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                f"UPDATE decisions SET {assignments}, updated_at = CURRENT_TIMESTAMP "
                f"WHERE order_id = ? AND status IN ({placeholders})",
                (*fields.values(), order_id, *allowed_from),
            )
            return cursor.rowcount == 1

    def record_created(
        self, order_id: str, cart_total_cents: int, currency: str, mandate_id: str | None = None
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO decisions (order_id, status, cart_total_cents, currency, mandate_id) "
                "VALUES (?, 'created', ?, ?, ?)",
                (order_id, cart_total_cents, currency, mandate_id),
            )

    def get(self, order_id: str) -> Decision | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT order_id, status, cart_total_cents, currency, checked_total_cents, "
                "checked_payee, capture_id, risk_level, checked_at, mandate_id "
                "FROM decisions WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return Decision(*row) if row else None

    def mark_checked(
        self,
        order_id: str,
        total_cents: int,
        payee: str,
        risk_level: str,
        risk_score: int,
        rule_hits: str,
        checked_at: float,
        shown: str | None = None,
    ) -> bool:
        return self._update(
            order_id,
            ("created", "checked"),
            status="checked",
            checked_total_cents=total_cents,
            checked_payee=payee,
            risk_level=risk_level,
            risk_score=risk_score,
            rule_hits=rule_hits,
            checked_at=checked_at,
            shown=shown,
        )

    def receipt_row(self, order_id: str) -> dict | None:
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT order_id, status, mandate_id, risk_level, risk_score, shown, "
                "capture_id, created_at, updated_at FROM decisions WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        return dict(row) if row else None

    def claim_for_capture(self, order_id: str) -> bool:
        # "capturing" is allowed so a retry after a network failure can go through;
        # the stable PayPal-Request-Id makes that retry safe.
        return self._update(order_id, ("checked", "capturing"), status="capturing")

    def release_capture(self, order_id: str) -> bool:
        return self._update(order_id, ("capturing",), status="checked")

    def mark_captured(self, order_id: str, capture_id: str) -> bool:
        return self._update(order_id, ("capturing",), status="captured", capture_id=capture_id)

    def mark_cancelled(self, order_id: str) -> bool:
        # Not from "capturing": the capture may already have reached PayPal.
        return self._update(order_id, ("created", "checked"), status="cancelled")
