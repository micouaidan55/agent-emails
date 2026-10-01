"""Accès aux emails traités, aux corrections humaines et au journal."""

import json
import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel

from agent_emails.models import (
    Attachment, Category, Email, EmailResult, EmailStatus, InvoiceCheck, Urgency,
)

CORRECTABLE_FIELDS = {"category", "urgency"}
_ORDER = """
ORDER BY CASE WHEN status IN ('a_verifier', 'erreur') THEN 0 ELSE 1 END,
         CASE urgency WHEN 'haute' THEN 0 WHEN 'normale' THEN 1 WHEN 'basse' THEN 2 ELSE 3 END,
         received_at DESC
"""


class StoredEmail(BaseModel):
    message_id: str
    sender_name: str
    sender_address: str
    subject: str
    received_at: datetime
    body: str
    attachments: list[str]
    status: EmailStatus
    category: Category | None
    urgency: Urgency | None
    confidence: float | None
    explanation: str | None
    summary: str | None
    extraction: dict | None
    invoice_checks: list[InvoiceCheck]
    draft: str | None
    draft_original: str | None
    error: str | None
    cost_usd: float
    seconds: float

    def to_email(self) -> Email:
        return Email(
            message_id=self.message_id,
            sender_name=self.sender_name,
            sender_address=self.sender_address,
            subject=self.subject,
            received_at=self.received_at,
            body=self.body,
            attachments=[Attachment(filename=name, content_type="application/octet-stream") for name in self.attachments],
        )


class Stats(BaseModel):
    total: int
    by_category: dict[str, int]
    pending_high: int
    drafts_to_review: int
    to_check: int
    validated: int
    validated_unchanged: int
    ignored: int
    corrected: int
    mean_cost_usd: float


class EmailStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_result(self, result: EmailResult) -> None:
        email, triage = result.email, result.triage
        # Normalize received_at to UTC
        received_at_utc = email.received_at.astimezone(timezone.utc).isoformat()
        
        # Fix 1: Use conditional UPDATE to preserve decided emails
        self._conn.execute(
            "INSERT INTO emails (message_id, sender_name, sender_address, subject, received_at, body,"
            " attachments, status, category, urgency, confidence, explanation, summary, extraction, invoice_checks,"
            " draft, draft_original, error, cost_usd, seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(message_id) DO UPDATE SET"
            " sender_name = excluded.sender_name,"
            " sender_address = excluded.sender_address,"
            " subject = excluded.subject,"
            " received_at = excluded.received_at,"
            " body = excluded.body,"
            " attachments = excluded.attachments,"
            " status = excluded.status,"
            " category = excluded.category,"
            " urgency = excluded.urgency,"
            " confidence = excluded.confidence,"
            " explanation = excluded.explanation,"
            " summary = excluded.summary,"
            " extraction = excluded.extraction,"
            " invoice_checks = excluded.invoice_checks,"
            " draft = excluded.draft,"
            " draft_original = excluded.draft_original,"
            " error = excluded.error,"
            " cost_usd = excluded.cost_usd,"
            " seconds = excluded.seconds"
            " WHERE emails.status NOT IN ('valide', 'ignore')",
            (
                email.message_id, email.sender_name, email.sender_address, email.subject,
                received_at_utc, email.body,
                json.dumps([a.filename for a in email.attachments], ensure_ascii=False),
                result.status.value,
                triage.category.value if triage else None,
                triage.urgency.value if triage else None,
                triage.confidence if triage else None,
                triage.explanation if triage else None,
                triage.summary if triage else None,
                json.dumps(result.extraction, ensure_ascii=False) if result.extraction is not None else None,
                json.dumps([c.model_dump(mode="json") for c in result.invoice_checks], ensure_ascii=False),
                result.draft, result.draft, result.error, result.usage.cost_usd, result.seconds,
            ),
        )
        self._conn.commit()

    def record_fetch_error(self, filename: str, message: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO emails (message_id, subject, received_at, status, error) VALUES (?, ?, ?, ?, ?)",
            (f"fichier:{filename}", filename, datetime.now(timezone.utc).isoformat(), EmailStatus.ERROR.value, message),
        )
        self._conn.commit()

    def known_ids(self) -> set[str]:
        # Un email en erreur reste relevable ; les fichiers illisibles ("fichier:…") restent connus.
        rows = self._conn.execute(
            "SELECT message_id FROM emails WHERE status != 'erreur' OR message_id LIKE 'fichier:%'"
        )
        return {row["message_id"] for row in rows}

    def get(self, message_id: str) -> StoredEmail | None:
        rows = self._select("WHERE message_id = ?", (message_id,))
        return rows[0] if rows else None

    def list_emails(self, statuses: set[EmailStatus] | None = None) -> list[StoredEmail]:
        if not statuses:
            return self._select("", ())
        marks = ", ".join("?" for _ in statuses)
        return self._select(f"WHERE status IN ({marks})", tuple(s.value for s in statuses))

    def update_draft(self, message_id: str, text: str, original: bool = False) -> None:
        column = "draft = ?, draft_original = ?" if original else "draft = ?"
        params = (text, text, message_id) if original else (text, message_id)
        self._conn.execute(f"UPDATE emails SET {column} WHERE message_id = ?", params)
        self._conn.commit()

    def set_status(self, message_id: str, status: EmailStatus) -> None:
        decided = status in (EmailStatus.VALIDATED, EmailStatus.IGNORED)
        self._conn.execute(
            "UPDATE emails SET status = ?, decided_at = CASE WHEN ? THEN datetime('now') ELSE decided_at END"
            " WHERE message_id = ?",
            (status.value, decided, message_id),
        )
        self._conn.commit()

    def correct(self, message_id: str, field: str, new_value: str) -> None:
        if field not in CORRECTABLE_FIELDS:
            raise ValueError(f"Champ non corrigeable : {field}")
        
        # Fix 4: Check if message_id exists
        row = self._conn.execute(f"SELECT {field} FROM emails WHERE message_id = ?", (message_id,)).fetchone()
        if row is None:
            raise ValueError(f"Email non trouvé : {message_id}")
        
        # Fix 3: Validate enum values
        if field == "category":
            try:
                Category(new_value)
            except ValueError:
                raise ValueError(f"Catégorie invalide : {new_value}")
        elif field == "urgency":
            try:
                Urgency(new_value)
            except ValueError:
                raise ValueError(f"Urgence invalide : {new_value}")
        
        # Only now insert and update
        self._conn.execute(
            "INSERT INTO corrections (message_id, field, old_value, new_value) VALUES (?, ?, ?, ?)",
            (message_id, field, row[field] if row else None, new_value),
        )
        self._conn.execute(f"UPDATE emails SET {field} = ? WHERE message_id = ?", (new_value, message_id))
        self._conn.commit()

    def log(self, message_id: str, action: str, detail: dict | None = None) -> None:
        self._conn.execute(
            "INSERT INTO action_log (message_id, action, detail) VALUES (?, ?, ?)",
            (message_id, action, json.dumps(detail or {}, ensure_ascii=False, default=str)),
        )
        self._conn.commit()

    def list_log(self, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            "SELECT message_id, action, detail, created_at FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def stats(self) -> Stats:
        count = lambda where, params=(): self._conn.execute(f"SELECT COUNT(*) FROM emails {where}", params).fetchone()[0]  # noqa: E731
        by_category = {
            row["category"]: row["n"]
            for row in self._conn.execute(
                "SELECT category, COUNT(*) AS n FROM emails WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC, category"
            )
        }
        mean_cost = self._conn.execute(
            "SELECT AVG(cost_usd) FROM emails WHERE status != 'erreur' OR category IS NOT NULL"
        ).fetchone()[0]
        # Fix 2: Only count distinct message_ids with category corrections
        corrected = self._conn.execute("SELECT COUNT(DISTINCT message_id) FROM corrections WHERE field = 'category'").fetchone()[0]
        return Stats(
            total=count(""),
            by_category=by_category,
            pending_high=count("WHERE urgency = 'haute' AND status IN ('a_verifier', 'brouillon_pret')"),
            drafts_to_review=count("WHERE status = 'brouillon_pret'"),
            to_check=count("WHERE status = 'a_verifier'"),
            validated=count("WHERE status = 'valide'"),
            validated_unchanged=count("WHERE status = 'valide' AND draft IS draft_original"),
            ignored=count("WHERE status = 'ignore'"),
            corrected=corrected,
            mean_cost_usd=mean_cost or 0.0,
        )

    def _select(self, where: str, params: tuple) -> list[StoredEmail]:
        rows = self._conn.execute(f"SELECT * FROM emails {where} {_ORDER}", params)
        return [
            StoredEmail(
                message_id=row["message_id"],
                sender_name=row["sender_name"],
                sender_address=row["sender_address"],
                subject=row["subject"],
                received_at=datetime.fromisoformat(row["received_at"]),
                body=row["body"],
                attachments=json.loads(row["attachments"]),
                status=row["status"],
                category=row["category"],
                urgency=row["urgency"],
                confidence=row["confidence"],
                explanation=row["explanation"],
                summary=row["summary"],
                extraction=json.loads(row["extraction"]) if row["extraction"] else None,
                invoice_checks=[InvoiceCheck.model_validate(c) for c in json.loads(row["invoice_checks"])],
                draft=row["draft"],
                draft_original=row["draft_original"],
                error=row["error"],
                cost_usd=row["cost_usd"],
                seconds=row["seconds"],
            )
            for row in rows
        ]
