"""Signaux de fraude vérifiés par le code, et règle de décision (spec §12).

Le code repère les signaux objectifs ; le modèle juge le contexte dans l'étape de tri ;
le code tranche avec une règle simple et testée.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urlparse

from agent_emails.models import Email, FraudRisk, FraudSeverity, FraudSignal, neutralize

FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr",
    "yahoo.com", "yahoo.fr", "icloud.com", "me.com", "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
    "gmx.com", "gmx.fr", "proton.me", "protonmail.com", "aol.com",
})
EXECUTABLE_SUFFIXES = frozenset({
    ".exe", ".scr", ".bat", ".cmd", ".com", ".msi", ".js", ".vbs", ".jar", ".ps1", ".lnk", ".iso", ".hta",
})
DOCUMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".txt"})
_ORGANISATION = re.compile(
    r"\b(banque|bank|impots|tresor|urssaf|service|support|direction|president|pdg|ceo|comptabilite|sarl|sas|ltd|inc"
    r"|gmbh|societe|administrateur|microsoft|paypal|amazon)\b"
)
_HOMOGLYPHS = str.maketrans({"0": "o", "1": "l", "i": "l", "3": "e", "5": "s", "4": "a", "7": "t", "8": "b"})
_URL_IN_TEXT = re.compile(r"(?:https?://|www\.)([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b", re.IGNORECASE)
_BANK_CHANGE = re.compile(
    r"nouve(?:au|lles?)\s+(?:rib|iban|coordonnées\s+bancaires|compte\s+bancaire)"
    r"|changement\s+de\s+(?:rib|banque|coordonnées\s+bancaires|compte\s+bancaire)"
    r"|new\s+bank\s+(?:details|account)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KnownSenders:
    domains: frozenset[str] = frozenset()
    addresses: frozenset[str] = frozenset()


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[1].strip().lower().rstrip(".") if "@" in address else ""


def trust_key(address: str) -> str:
    domain = domain_of(address)
    return address.strip().lower() if domain in FREE_MAIL_DOMAINS else domain


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _same_or_sub(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _label_and_tld(domain: str) -> tuple[str, str]:
    parts = domain.split(".")
    return (parts[-2], parts[-1]) if len(parts) >= 2 else (parts[0], "")


def _skeleton(label: str) -> str:
    return label.replace("rn", "m").translate(_HOMOGLYPHS).replace("-", "")


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def lookalike_of(domain: str, known: Iterable[str]) -> str | None:
    known = sorted(k for k in known if k)
    if not domain or any(_same_or_sub(domain, k) for k in known):
        return None
    label, tld = _label_and_tld(domain)
    for candidate in known:
        if candidate in FREE_MAIL_DOMAINS:
            continue
        known_label, known_tld = _label_and_tld(candidate)
        if label == known_label and tld != known_tld:
            return candidate
        if _skeleton(label) == _skeleton(known_label):
            return candidate
        if len(known_label) >= 6 and _distance(label, known_label) <= 2:
            return candidate
    return None


def _signal(code: str, severity: FraudSeverity, message: str) -> FraudSignal:
    return FraudSignal(code=code, severity=severity, message=message)


def fraud_signals(email: Email, known: KnownSenders) -> list[FraudSignal]:
    signals: list[FraudSignal] = []
    sender_domain = domain_of(email.sender_address)

    imitated = lookalike_of(sender_domain, known.domains)
    if imitated:
        signals.append(_signal("domaine_sosie", FraudSeverity.STRONG,
                               f"Le domaine {sender_domain} imite {imitated}, un domaine connu."))

    for attachment in email.attachments:
        suffixes = [s.lower() for s in PurePath(attachment.filename).suffixes]
        if suffixes and suffixes[-1] in EXECUTABLE_SUFFIXES:
            disguised = len(suffixes) >= 2 and suffixes[-2] in DOCUMENT_SUFFIXES
            detail = "se fait passer pour un document mais est un programme" if disguised else "est un programme"
            signals.append(_signal("piece_jointe_dangereuse", FraudSeverity.STRONG,
                                   f"La pièce jointe {attachment.filename} {detail}."))

    for link in email.links:
        shown = _URL_IN_TEXT.search(link.text)
        host = _host(link.url)
        if shown and host:
            shown_domain = shown.group(1).lower().removeprefix("www.")
            if not _same_or_sub(host, shown_domain):
                signals.append(_signal("lien_trompeur", FraudSeverity.STRONG,
                                       f"Un lien affiche {shown_domain} mais mène à {host}."))

    reply_domain = domain_of(email.reply_to)
    if reply_domain and not (_same_or_sub(reply_domain, sender_domain) or _same_or_sub(sender_domain, reply_domain)):
        signals.append(_signal("reponse_ailleurs", FraudSeverity.WEAK,
                               f"Les réponses partiraient vers {email.reply_to}, pas vers {email.sender_address}."))

    other = [a for a in _EMAIL_IN_TEXT.findall(email.sender_name) if a.lower() != email.sender_address.lower()]
    if other:
        signals.append(_signal("nom_affiche_trompeur", FraudSeverity.WEAK,
                               f"Le nom affiché contient une autre adresse ({other[0]})."))

    if (sender_domain in FREE_MAIL_DOMAINS and email.sender_address.lower() not in known.addresses
            and _ORGANISATION.search(_plain(email.sender_name))):
        signals.append(_signal("messagerie_gratuite", FraudSeverity.WEAK,
                               f"« {email.sender_name} » écrit depuis une messagerie gratuite ({sender_domain})."))

    text = f"{email.subject}\n{email.body}"
    if _IBAN.search(text) or _BANK_CHANGE.search(text):
        signals.append(_signal("coordonnees_bancaires", FraudSeverity.WEAK,
                               "L'email contient un IBAN ou annonce un changement de coordonnées bancaires."))
    return signals


def is_suspicious(signals: list[FraudSignal], risk: FraudRisk | None) -> bool:
    strong = sum(1 for s in signals if s.severity is FraudSeverity.STRONG)
    weak = len(signals) - strong
    return strong >= 1 or weak >= 2 or risk is FraudRisk.HIGH


def signals_block(signals: list[FraudSignal]) -> str:
    lines = "\n".join(f"- [{s.severity.value}] {neutralize(s.message)}" for s in signals) or "- aucun"
    return f"<signaux>\n{lines}\n</signaux>"
