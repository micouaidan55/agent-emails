"""Orchestration : le code décide de l'enchaînement des étapes, pas le modèle."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic

from agent_emails.connectors.base import MailSource
from agent_emails.models import Category, Email, EmailResult, EmailStatus, Triage, Usage
from agent_emails.pipeline.drafting import draft_reply
from agent_emails.pipeline.extraction import extract_info
from agent_emails.pipeline.invoices import InvoiceAnalyzer, check_invoices
from agent_emails.pipeline.llm import DRAFT_MODEL, EXTRACTION_MODEL, TRIAGE_MODEL, StepError
from agent_emails.pipeline.triage import triage_email
from agent_emails.storage.repository import EmailStore, StoredEmail

CONFIDENCE_THRESHOLD = 0.7


@dataclass
class PipelineConfig:
    company: str
    attachments_dir: Path
    triage_model: str = TRIAGE_MODEL
    extraction_model: str = EXTRACTION_MODEL
    draft_model: str = DRAFT_MODEL


class Pipeline:
    def __init__(self, client, analyzer: InvoiceAnalyzer | None, config: PipelineConfig):
        self.client = client
        self.analyzer = analyzer
        self.config = config

    def process(self, email: Email) -> EmailResult:
        started = time.perf_counter()
        usage = Usage()
        triage = None
        extraction = None
        checks = []
        try:
            triage, step = triage_email(self.client, email, self.config.company, self.config.triage_model)
            usage += step
            draft = None
            if triage.category is not Category.PROSPECTING:
                info, step = extract_info(self.client, email, triage.category, self.config.company,
                                          self.config.extraction_model)
                usage += step
                extraction = info.model_dump(mode="json")
                if triage.category is Category.INVOICE and self.analyzer is not None:
                    checks = check_invoices(email, self.analyzer, self.config.attachments_dir)
                    usage += Usage(cost_usd=sum(c.cost_usd or 0.0 for c in checks))
                draft, step = draft_reply(self.client, email, triage, extraction, checks, self.config.company,
                                          self.config.draft_model)
                usage += step
            if triage.confidence < CONFIDENCE_THRESHOLD:
                status = EmailStatus.TO_CHECK
            elif triage.category is Category.PROSPECTING:
                status = EmailStatus.TO_IGNORE
            else:
                status = EmailStatus.DRAFT_READY
            return self._result(email, status, started, usage, triage, extraction, checks, draft)
        except StepError as exc:
            usage += exc.usage
            return self._result(email, EmailStatus.TO_CHECK, started, usage, triage, extraction, checks,
                                error=f"Analyse incomplète : {exc}")
        except anthropic.APIError as exc:
            return self._result(email, EmailStatus.ERROR, started, usage, triage, extraction, checks,
                                error=f"Erreur d'API Claude ({type(exc).__name__}) : {exc}")

    @staticmethod
    def _result(email, status, started, usage, triage=None, extraction=None, checks=(), draft=None, error=None):
        return EmailResult(
            email=email, status=status, triage=triage, extraction=extraction, invoice_checks=list(checks),
            draft=draft, error=error, usage=usage, seconds=time.perf_counter() - started,
        )


def process_inbox(
    source: MailSource, store: EmailStore, pipeline: Pipeline,
    on_result: Callable[[EmailResult], None] | None = None,
) -> list[EmailResult]:
    fetched = source.fetch_new(store.known_ids())
    for filename, message in fetched.errors:
        store.record_fetch_error(filename, message)
    results = []
    for email in fetched.emails:
        result = pipeline.process(email)
        store.save_result(result)
        store.log(email.message_id, "analyse", {
            "statut": result.status.value,
            "categorie": result.triage.category.value if result.triage else None,
            "cout_usd": round(result.usage.cost_usd, 5),
        })
        results.append(result)
        if on_result:
            on_result(result)
    return results


def regenerate_draft(client, stored: StoredEmail, company: str, model: str = DRAFT_MODEL) -> tuple[str | None, Usage]:
    triage = Triage(
        category=stored.category, urgency=stored.urgency, confidence=stored.confidence or 0.0,
        explanation=stored.explanation or "", summary=stored.summary or "",
    )
    return draft_reply(client, stored.to_email(), triage, stored.extraction, stored.invoice_checks, company, model)
