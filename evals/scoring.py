"""Métriques d'évaluation (fonctions pures, sans appel au modèle)."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

from agent_emails.models import EmailResult

MONTHS = "janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre"
AMOUNT_RE = re.compile(r"\d[\d\s  .,]*\s?(?:€|euros?\b|EUR\b)", re.IGNORECASE)
DATE_RE = re.compile(
    rf"\b\d{{1,2}}(?:er)?\s+(?:{MONTHS})\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return " ".join(str(text).replace(" ", " ").replace(" ", " ").casefold().split())


def field_value(extraction: dict | None, path: str) -> str | None:
    value = extraction
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if value is None:
        return None
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def field_ok(expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    compact = lambda s: normalize(s).replace(" ", "")  # noqa: E731
    return normalize(expected) in normalize(actual) or compact(expected) in compact(actual)


def _amount_key(text: str) -> str:
    digits = re.sub(r"[^\d,.]", "", text).replace(",", ".").rstrip(".")
    return digits.split(".")[0] if digits.endswith((".0", ".00")) or digits.count(".") == 0 else digits


def _date_keys(text: str) -> set[str]:
    text = normalize(text).replace("1er", "1")
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = iso.groups()
        return {text, f"{int(day)}/{int(month)}/{year}", f"{int(day)}/{int(month)}"}
    slash = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if slash:
        day, month, year = slash.groups()
        keys = {f"{int(day)}/{int(month)}"}
        if year:
            keys.add(f"{int(day)}/{int(month)}/{year}")
        return keys
    return {text}


def inventions(draft: str, source: str) -> list[str]:
    """Montants et dates du brouillon absents de la source (email et analyse)."""
    source_amounts = {_amount_key(m) for m in AMOUNT_RE.findall(source)}
    source_amounts |= {_amount_key(m) for m in re.findall(r"\d+(?:[.,]\d+)?", source)}
    source_dates = set().union(*[_date_keys(m) for m in DATE_RE.findall(source)]) if DATE_RE.search(source) else set()
    invented = [m.strip() for m in AMOUNT_RE.findall(draft) if _amount_key(m) not in source_amounts]
    invented += [m.strip() for m in DATE_RE.findall(draft) if not (_date_keys(m) & source_dates)]
    return invented


def source_text(result: EmailResult) -> str:
    parts = [result.email.subject, result.email.body]
    for check in result.invoice_checks:
        parts.append(check.explanation)
        parts.extend(issue.message for issue in check.issues)
        parts.append(json.dumps(check.invoice or {}, ensure_ascii=False))
    return "\n".join(parts)


@dataclass
class EmailOutcome:
    file: str
    expected: dict
    category: str | None
    urgency: str | None
    extraction: dict | None
    draft: str | None
    invoice_statuses: list[str]
    judge: dict | None
    cost_usd: float
    seconds: float
    error: str | None
    source: str = ""
    suspected: bool = False


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(outcomes: list[EmailOutcome]) -> dict:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fields, mentions, presence, invoices, judges = [], [], [], [], []
    invented = 0
    frauds = [o for o in outcomes if o.expected.get("fraud")]
    legit = [o for o in outcomes if not o.expected.get("fraud")]
    for o in legit:
        expected = o.expected
        if o.category != expected["category"]:
            confusion[expected["category"]][o.category or "aucune"] += 1
        fields += [field_ok(value, field_value(o.extraction, path)) for path, value in expected["fields"].items()]
        presence.append((o.draft is not None) == expected["draft_expected"])
        if o.draft:
            mentions += [normalize(term) in normalize(o.draft) for term in expected["must_mention"]]
            invented += len(inventions(o.draft, o.source or ""))
        expected_statuses = [a["expected_status"] for a in expected.get("attachments", [])]
        invoices += [i < len(o.invoice_statuses) and o.invoice_statuses[i] == s for i, s in enumerate(expected_statuses)]
        if o.judge:
            judges.append(mean(o.judge[k] for k in ("pertinence", "ton", "completude", "fidelite")))
    return {
        "emails": len(outcomes),
        "triage_accuracy": _rate([o.category == o.expected["category"] for o in legit]),
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "urgency_accuracy": _rate([o.urgency == o.expected["urgency"] for o in legit]),
        "field_accuracy": _rate(fields),
        "draft_presence_accuracy": _rate(presence),
        "must_mention_rate": _rate(mentions),
        "invented_facts": invented,
        "judge_mean": mean(judges) if judges else None,
        "invoice_accuracy": _rate(invoices),
        "fraud_cases": len(frauds),
        "fraud_recall": _rate([o.suspected for o in frauds]) if frauds else None,
        "fraud_false_alerts": sum(1 for o in legit if o.suspected),
        "mean_cost_usd": mean(o.cost_usd for o in outcomes) if outcomes else 0.0,
        "mean_seconds": mean(o.seconds for o in outcomes) if outcomes else 0.0,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %".replace(".", ",")


def to_markdown(label: str, summary: dict) -> str:
    judge = f"{summary['judge_mean']:.2f} / 5".replace(".", ",") if summary["judge_mean"] is not None else "non évalué"
    recall = summary["fraud_recall"]
    caught = round(recall * summary["fraud_cases"]) if recall is not None else 0
    fraud = f"{_pct(recall)} ({caught}/{summary['fraud_cases']})" if recall is not None else "aucun cas"
    lines = [
        f"## Résultats — `{label}` ({summary['emails']} emails)", "",
        "| Métrique | Valeur |", "|---|---|",
        f"| Tri correct | {_pct(summary['triage_accuracy'])} |",
        f"| Urgence correcte | {_pct(summary['urgency_accuracy'])} |",
        f"| Champs extraits corrects | {_pct(summary['field_accuracy'])} |",
        f"| Brouillon produit quand attendu | {_pct(summary['draft_presence_accuracy'])} |",
        f"| Éléments obligatoires cités | {_pct(summary['must_mention_rate'])} |",
        f"| Informations inventées | {summary['invented_facts']} |",
        f"| Note moyenne des brouillons (juge) | {judge} |",
        f"| Verdicts de factures corrects | {_pct(summary['invoice_accuracy'])} |",
        f"| Fraudes détectées (rappel) | {fraud} |",
        f"| Fausses alertes de fraude | {summary['fraud_false_alerts']} |",
        f"| Coût moyen par email | {summary['mean_cost_usd']:.4f} $ |",
        f"| Temps moyen par email | {summary['mean_seconds']:.1f} s |",
        "", "### Matrice de confusion (erreurs de tri)", "",
    ]
    if not summary["confusion"]:
        lines.append("Aucune erreur de tri.")
    for expected, predicted in summary["confusion"].items():
        lines += [f"- attendu `{expected}` → " + ", ".join(f"`{p}` ×{n}" for p, n in predicted.items())]
    return "\n".join(lines) + "\n"
