"""Interface d'une source d'emails. La V1 lit un dossier ; Gmail ou IMAP viendront en V2."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agent_emails.models import Email


@dataclass
class FetchResult:
    emails: list[Email] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


@runtime_checkable
class MailSource(Protocol):
    def fetch_new(self, known_ids: set[str]) -> FetchResult:
        """Emails pas encore traités, et les messages illisibles (nom, raison)."""

    def save_draft(self, email: Email, text: str) -> str:
        """Dépose un brouillon de réponse (jamais envoyé) et renvoie son emplacement."""
