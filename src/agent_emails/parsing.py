"""Lecture d'un fichier .eml en Email."""

import hashlib
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from agent_emails.models import Attachment, Email


class EmailParseError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    lines = [" ".join(line.split()) for line in "".join(extractor.parts).splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


def _received_at(value) -> datetime:
    try:
        parsed = parsedate_to_datetime(str(value)) if value else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_eml(raw: bytes) -> Email:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        name, address = parseaddr(str(msg["From"] or ""))
        if not address:
            raise EmailParseError("Expéditeur introuvable dans l'email.")
        message_id = str(msg["Message-ID"] or "").strip() or "sha1:" + hashlib.sha1(raw).hexdigest()
        body_part = msg.get_body(preferencelist=("plain", "html"))
        body = ""
        if body_part is not None:
            content = body_part.get_content()
            body = html_to_text(content) if body_part.get_content_type() == "text/html" else content
        attachments = [
            Attachment(
                filename=part.get_filename() or "piece_jointe",
                content_type=part.get_content_type(),
                data=part.get_payload(decode=True) or b"",
            )
            for part in msg.iter_attachments()
        ]
        return Email(
            message_id=message_id,
            sender_name=name,
            sender_address=address,
            subject=str(msg["Subject"] or "(sans objet)"),
            received_at=_received_at(msg["Date"]),
            body=body,
            attachments=attachments,
        )
    except EmailParseError:
        raise
    except Exception as exc:
        raise EmailParseError(f"Email illisible : {exc}") from exc
