from datetime import datetime, timezone

from agent_emails.models import Attachment, Email


def make_email(**overrides) -> Email:
    data = {
        "message_id": "<devis-1@exemple.fr>",
        "sender_name": "Claire Martin",
        "sender_address": "claire.martin@hotel-canuts.fr",
        "subject": "Demande de devis pour des suspensions",
        "received_at": datetime(2026, 9, 28, 9, 15, tzinfo=timezone.utc),
        "body": "Bonjour, nous souhaiterions un devis pour 12 suspensions en laiton. Cordialement, Claire Martin",
        "attachments": [],
    }
    data.update(overrides)
    return Email.model_validate(data)


def pdf_attachment(name: str = "facture.pdf") -> Attachment:
    return Attachment(filename=name, content_type="application/pdf", data=b"%PDF-1.4 fake")
