"""Source d'emails V1 : un dossier de fichiers .eml, et un dossier de brouillons."""

import hashlib
from email.message import EmailMessage
from pathlib import Path

from agent_emails.connectors.base import FetchResult
from agent_emails.models import Email
from agent_emails.parsing import EmailParseError, parse_eml


def draft_filename(message_id: str) -> str:
    return f"brouillon_{hashlib.sha1(message_id.encode()).hexdigest()[:16]}.eml"


class FolderSource:
    def __init__(self, inbox: Path, drafts: Path):
        self.inbox = inbox
        self.drafts = drafts

    def fetch_new(self, known_ids: set[str]) -> FetchResult:
        result = FetchResult()
        if not self.inbox.is_dir():
            return result
        seen_ids = set(known_ids)
        for path in sorted(self.inbox.glob("*.eml")):
            try:
                email = parse_eml(path.read_bytes())
            except EmailParseError as exc:
                result.errors.append((path.name, str(exc)))
                continue
            except OSError as exc:
                result.errors.append((path.name, f"Fichier illisible : {exc.strerror or exc}"))
                continue
            if email.message_id not in seen_ids:
                result.emails.append(email)
                seen_ids.add(email.message_id)
        return result

    def save_draft(self, email: Email, text: str) -> str:
        message = EmailMessage()
        message["To"] = email.sender_address
        message["Subject"] = f"Re: {email.subject}"
        message["In-Reply-To"] = email.message_id
        message["References"] = email.message_id
        message.set_content(text)
        self.drafts.mkdir(parents=True, exist_ok=True)
        path = self.drafts / draft_filename(email.message_id)
        path.write_bytes(message.as_bytes())
        return str(path)
