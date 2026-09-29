"""Lecture d'un fichier .eml en Email."""

import hashlib
import mimetypes
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from agent_emails.models import Attachment, Email


class EmailParseError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "hr", "blockquote", "ul", "ol", "pre", "section", "article",
        "header", "footer"
    }
    CELL_TAGS = {"td", "th"}
    SKIP_TAGS = {"script", "style", "title", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1
        elif tag in self.CELL_TAGS:
            self.parts.append(" ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in self.CELL_TAGS:
            self.parts.append(" ")
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


def _attachment_name(part, index: int, used: set[str]) -> str:
    """Generate a unique attachment name."""
    filename = part.get_filename()
    if filename:
        name = filename
    else:
        ext = mimetypes.guess_extension(part.get_content_type()) or ""
        name = f"piece_jointe_{index}{ext}"

    if name not in used:
        used.add(name)
        return name

    # Name is used; insert _2, _3, etc. before extension
    if "." in name:
        base, ext = name.rsplit(".", 1)
        ext = "." + ext
    else:
        base = name
        ext = ""

    counter = 2
    while True:
        new_name = f"{base}_{counter}{ext}"
        if new_name not in used:
            used.add(new_name)
            return new_name
        counter += 1


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
            try:
                content = body_part.get_content()
            except LookupError:
                # Unknown charset; fallback to UTF-8 with error replacement
                payload = body_part.get_payload(decode=True) or b""
                content = payload.decode("utf-8", errors="replace")
            body = html_to_text(content) if body_part.get_content_type() == "text/html" else content

        used_names: set[str] = set()
        attachments: list[Attachment] = []
        for idx, part in enumerate(msg.iter_attachments()):
            if part.get_content_type() == "message/rfc822":
                data = part.get_payload(0).as_bytes()
            else:
                data = part.get_payload(decode=True) or b""

            filename = _attachment_name(part, idx, used_names)
            attachments.append(
                Attachment(
                    filename=filename,
                    content_type=part.get_content_type(),
                    data=data,
                )
            )

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
