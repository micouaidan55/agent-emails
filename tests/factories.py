from datetime import datetime, timezone
from email.message import EmailMessage

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
        "reply_to": "",
        "links": [],
    }
    data.update(overrides)
    return Email.model_validate(data)


def pdf_attachment(name: str = "facture.pdf") -> Attachment:
    return Attachment(filename=name, content_type="application/pdf", data=b"%PDF-1.4 fake")


def build_eml(
    *,
    subject: str = "Demande de devis",
    body: str | None = "Bonjour, pouvez-vous m'envoyer un devis ?",
    html: str | None = None,
    attachments: tuple = (),
    message_id: str | None = "<abc@exemple.fr>",
    sender: str | None = "Marie Dubois <marie@exemple.fr>",
    reply_to: str | None = None,
    date: str = "Mon, 28 Sep 2026 09:15:00 +0200",
) -> bytes:
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = "contact@atelier-lumiere.fr"
    msg["Subject"] = subject
    msg["Date"] = date
    if message_id:
        msg["Message-ID"] = message_id
    if reply_to:
        msg["Reply-To"] = reply_to
    if body is None:
        msg.set_content(html or "", subtype="html")
    else:
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
    for name, content_type, data in attachments:
        maintype, subtype = content_type.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()
