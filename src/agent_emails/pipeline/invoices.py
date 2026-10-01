"""Étape ③ : les pièces jointes de type facture sont analysées par l'agent de factures (projet 1)."""

import hashlib
from pathlib import Path
from typing import Protocol

from agent_factures.agent.loop import AgentResult

from agent_emails.models import Email, InvoiceCheck

INVOICE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


class InvoiceAnalyzer(Protocol):
    def process(self, path: Path) -> AgentResult: ...


def check_invoices(email: Email, analyzer: InvoiceAnalyzer, workdir: Path) -> list[InvoiceCheck]:
    folder = workdir / hashlib.sha1(email.message_id.encode()).hexdigest()[:16]
    checks = []
    written_names = set()
    for attachment in email.attachments:
        name = Path(attachment.filename).name or "piece_jointe"
        if Path(name).suffix.lower() not in INVOICE_SUFFIXES:
            continue
        # Handle duplicate basenames by suffixing
        original_name = name
        counter = 2
        while name in written_names:
            stem = Path(original_name).stem
            suffix = Path(original_name).suffix
            name = f"{stem}_{counter}{suffix}"
            counter += 1
        written_names.add(name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / name
            path.write_bytes(attachment.data)
            result = analyzer.process(path)
        except Exception as exc:
            checks.append(InvoiceCheck(filename=name, path=str(path), status="a_revoir",
                                       explanation=f"Analyse de la facture impossible : {exc}"))
            continue
        checks.append(
            InvoiceCheck(
                filename=name,
                path=str(path),
                status=result.verdict.status.value,
                explanation=result.verdict.explanation,
                issues=list(result.issues),
                invoice=result.invoice.model_dump(mode="json") if result.invoice else None,
                cost_usd=result.cost_usd,
            )
        )
    return checks
