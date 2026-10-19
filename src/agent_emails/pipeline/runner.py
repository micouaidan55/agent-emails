"""Orchestration : le code décide de l'enchaînement des étapes, pas le modèle."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic

from agent_emails.connectors.base import MailSource
from agent_emails.models import Category, Email, EmailResult, EmailStatus, Triage, Usage
from agent_emails.pipeline.drafting import draft_reply
from agent_emails.pipeline.fraud import KnownSenders, fraud_signals, is_suspicious
from agent_emails.pipeline.extraction import extract_info
from agent_emails.pipeline.invoices import InvoiceAnalyzer, check_invoices
from agent_emails.pipeline.llm import DRAFT_MODEL, EXTRACTION_MODEL, TRIAGE_MODEL, StepError
from agent_emails.pipeline.triage import triage_email
from agent_emails.storage.repository import EmailStore, StoredEmail

CONFIDENCE_THRESHOLD = 0.7
# Référence portée par les relances et échéanciers d'agent-relances : [Réf. R-00042] ou [Réf. E-00007].
RELANCE_REFERENCE = re.compile(r"\[Réf\. [RE]-\d{5}\]")
RELANCE_NOTE = "Réponse à une relance : traitée par agent-relances"


@dataclass
class PipelineConfig:
    company: str
    attachments_dir: Path
    triage_model: str = TRIAGE_MODEL
    extraction_model: str = EXTRACTION_MODEL
    draft_model: str = DRAFT_MODEL
    company_domain: str = ""
    extra_domains: tuple[str, ...] = ()


class Pipeline:
    def __init__(self, client, analyzer: InvoiceAnalyzer | None, config: PipelineConfig):
        self.client = client
        self.analyzer = analyzer
        self.config = config

    def process(self, email: Email, known: KnownSenders | None = None, check_fraud: bool = True) -> EmailResult:
        started = time.perf_counter()
        usage = Usage()
        # Une réponse à une relance est laissée à agent-relances, avant tout appel au modèle.
        # Sécurité : un fraudeur peut ajouter une fausse balise « [Réf. …] » pour échapper au contrôle de
        # fraude d'agent-emails. C'est accepté : agent-relances applique son propre contrôle
        # (reply_is_suspicious) à chaque réponse avant de proposer une action, et un humain confirme toujours.
        if RELANCE_REFERENCE.search(email.subject):
            return self._result(email, EmailStatus.TO_IGNORE, started, usage, note=RELANCE_NOTE)
        triage = None
        extraction = None
        checks = []
        if known is None:
            domain = self.config.company_domain.strip().lower()
            known = KnownSenders(domains=frozenset({domain} - {""}))
        signals = fraud_signals(email, known)

        def blocked(risk) -> bool:
            return check_fraud and is_suspicious(signals, risk)

        try:
            triage, step = triage_email(self.client, email, self.config.company, self.config.triage_model, signals)
            usage += step
            if blocked(triage.fraud_risk):
                return self._result(email, EmailStatus.SUSPECT, started, usage, triage, signals=signals)
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
            return self._result(email, status, started, usage, triage, extraction, checks, draft, signals=signals)
        except StepError as exc:
            usage += exc.usage
            status = EmailStatus.SUSPECT if blocked(triage.fraud_risk if triage else None) else EmailStatus.TO_CHECK
            return self._result(email, status, started, usage, triage, extraction, checks,
                                error=f"Analyse incomplète : {exc}", signals=signals)
        except anthropic.APIError as exc:
            status = EmailStatus.SUSPECT if blocked(triage.fraud_risk if triage else None) else EmailStatus.ERROR
            return self._result(email, status, started, usage, triage, extraction, checks,
                                error=f"Erreur d'API Claude ({type(exc).__name__}) : {exc}", signals=signals)

    @staticmethod
    def _result(email, status, started, usage, triage=None, extraction=None, checks=(), draft=None, error=None,
                signals=(), note=None):
        return EmailResult(
            email=email, status=status, triage=triage, extraction=extraction, invoice_checks=list(checks),
            draft=draft, error=error, usage=usage, seconds=time.perf_counter() - started,
            fraud_signals=list(signals), note=note,
        )


def _save_and_log(store: EmailStore, result: EmailResult) -> None:
    store.save_result(result)
    store.log(result.email.message_id, "analyse", {
        "statut": result.status.value,
        "categorie": result.triage.category.value if result.triage else None,
        "signaux": [s.code for s in result.fraud_signals],
        "cout_usd": round(result.usage.cost_usd, 5),
        **({"note": result.note} if result.note else {}),
    })


def process_inbox(
    source: MailSource, store: EmailStore, pipeline: Pipeline,
    on_result: Callable[[EmailResult], None] | None = None,
) -> list[EmailResult]:
    fetched = source.fetch_new(store.known_ids())
    for filename, message in fetched.errors:
        store.record_fetch_error(filename, message)
    known = store.known_senders(pipeline.config.company_domain, pipeline.config.extra_domains)
    results = []
    for email in fetched.emails:
        try:
            result = pipeline.process(email, known=known)
        except Exception as exc:
            result = Pipeline._result(email, EmailStatus.ERROR, time.perf_counter(), Usage(),
                                      error=f"Erreur inattendue : {exc}")
        _save_and_log(store, result)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def reprocess_cleared(source: MailSource, store: EmailStore, pipeline: Pipeline, message_id: str) -> EmailResult | None:
    """« Ce n'est pas une fraude » : l'expéditeur devient de confiance et le traitement normal reprend."""
    stored = store.get(message_id)
    if stored is None or stored.status is not EmailStatus.SUSPECT:
        return None
    store.trust_sender(stored.sender_address)
    store.log(message_id, "fraude_levee", {"expediteur": stored.sender_address})
    fetched = source.fetch_new(store.known_ids() - {message_id})
    email = next((e for e in fetched.emails if e.message_id == message_id), None) or stored.to_email()
    result = pipeline.process(email, known=store.known_senders(pipeline.config.company_domain, pipeline.config.extra_domains), check_fraud=False)
    _save_and_log(store, result)
    return result


def regenerate_draft(client, stored: StoredEmail, company: str, model: str = DRAFT_MODEL) -> tuple[str | None, Usage]:
    triage = Triage(
        category=stored.category, urgency=stored.urgency, confidence=stored.confidence or 0.0,
        explanation=stored.explanation or "", summary=stored.summary or "",
    )
    return draft_reply(client, stored.to_email(), triage, stored.extraction, stored.invoice_checks, company, model)
