"""Modèles de données de l'agent de tri des emails."""

import re
from datetime import datetime
from enum import StrEnum

from agent_factures.extraction.models import Issue
from pydantic import BaseModel, Field


_DELIMITERS = re.compile(r"<(/?)(email|analyse|brouillon|signaux)\b", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Empêche un contenu non fiable de fermer ou d'ouvrir nos balises de délimitation."""
    return _DELIMITERS.sub(r"‹\1\2", text)


class Category(StrEnum):
    QUOTE = "demande_devis"
    COMPLAINT = "reclamation"
    INVOICE = "facture"
    APPOINTMENT = "rendez_vous"
    INFORMATION = "information"
    PROSPECTING = "prospection"


class Urgency(StrEnum):
    HIGH = "haute"
    NORMAL = "normale"
    LOW = "basse"


class EmailStatus(StrEnum):
    TO_CHECK = "a_verifier"
    DRAFT_READY = "brouillon_pret"
    TO_IGNORE = "a_ignorer"
    VALIDATED = "valide"
    IGNORED = "ignore"
    ERROR = "erreur"
    SUSPECT = "suspect"
    FRAUD = "fraude"


class FraudRisk(StrEnum):
    NONE = "aucun"
    LOW = "faible"
    HIGH = "eleve"


class FraudSeverity(StrEnum):
    STRONG = "forte"
    WEAK = "faible"


class FraudSignal(BaseModel):
    code: str
    severity: FraudSeverity
    message: str


class Attachment(BaseModel):
    filename: str
    content_type: str
    data: bytes = Field(default=b"", exclude=True, repr=False)


class Link(BaseModel):
    text: str
    url: str


class Email(BaseModel):
    message_id: str
    sender_name: str = ""
    sender_address: str
    subject: str
    received_at: datetime
    body: str
    attachments: list[Attachment] = Field(default_factory=list)
    reply_to: str = ""
    links: list[Link] = Field(default_factory=list)


class Triage(BaseModel):
    category: Category
    urgency: Urgency
    confidence: float = Field(description="Confiance dans la catégorie, entre 0 et 1.")
    explanation: str = Field(description="Une phrase qui justifie la catégorie et l'urgence.")
    summary: str = Field(description="Résumé de l'email en moins de 15 mots.")
    fraud_risk: FraudRisk = Field(default=FraudRisk.NONE, description="Risque que l'email soit une fraude : aucun, faible ou eleve.")
    fraud_reason: str = Field(default="", description="Une phrase en français qui justifie le risque de fraude.")


class Contact(BaseModel):
    name: str | None = Field(description="Nom de la personne qui écrit, ou null.")
    company: str | None = Field(description="Entreprise ou organisation de l'expéditeur, ou null.")


class QuoteRequest(BaseModel):
    contact: Contact
    product_or_service: str | None
    quantity: str | None
    desired_date: str | None
    budget: str | None


class Complaint(BaseModel):
    contact: Contact
    order_reference: str | None = Field(description="Numéro de commande ou de facture concerné, ou null.")
    problem: str | None
    expectation: str | None = Field(description="Ce que le client demande (remplacement, remboursement…), ou null.")


class AppointmentRequest(BaseModel):
    contact: Contact
    proposed_slots: list[str] = Field(description="Créneaux proposés, tels qu'écrits dans l'email.")
    purpose: str | None


class InvoiceNotice(BaseModel):
    contact: Contact
    supplier: str | None
    invoice_number: str | None = Field(description="Numéro de facture cité dans le texte de l'email, ou null.")


class InformationRequest(BaseModel):
    contact: Contact
    question: str | None


EXTRACTION_MODELS: dict[Category, type[BaseModel]] = {
    Category.QUOTE: QuoteRequest,
    Category.COMPLAINT: Complaint,
    Category.INVOICE: InvoiceNotice,
    Category.APPOINTMENT: AppointmentRequest,
    Category.INFORMATION: InformationRequest,
}


class InvoiceCheck(BaseModel):
    filename: str
    path: str
    status: str
    explanation: str
    issues: list[Issue] = Field(default_factory=list)
    invoice: dict | None = None
    cost_usd: float | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class EmailResult(BaseModel):
    email: Email
    status: EmailStatus
    triage: Triage | None = None
    extraction: dict | None = None
    invoice_checks: list[InvoiceCheck] = Field(default_factory=list)
    draft: str | None = None
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)
    seconds: float = 0.0
    fraud_signals: list[FraudSignal] = Field(default_factory=list)


def render_email(email: Email) -> str:
    """Email présenté au modèle. Les balises <email> marquent un contenu non fiable."""
    attachments = ", ".join(neutralize(a.filename) for a in email.attachments) or "aucune"
    reply_to = f"Répondre à : {neutralize(email.reply_to)}\n" if email.reply_to else ""
    return (
        "<email>\n"
        f"De : {neutralize(email.sender_name)} <{neutralize(email.sender_address)}>\n"
        f"{reply_to}"
        f"Date : {email.received_at:%d/%m/%Y %H:%M}\n"
        f"Objet : {neutralize(email.subject)}\n"
        f"Pièces jointes : {attachments}\n\n"
        f"{neutralize(email.body.strip())}\n"
        "</email>"
    )
