import pytest

from agent_emails.models import EmailResult, EmailStatus, InvoiceCheck, Triage
from evals.scoring import (
    EmailOutcome, field_ok, field_value, inventions, normalize, source_text, summarize, to_markdown,
)
from tests.factories import make_email


def test_normalize_and_field_matching():
    assert normalize("  Hôtel des   Canuts ") == "hôtel des canuts"
    assert field_value({"contact": {"name": "Claire Martin"}}, "contact.name") == "Claire Martin"
    assert field_value({"proposed_slots": ["jeudi 1er octobre 14 h", "vendredi"]}, "proposed_slots") == "jeudi 1er octobre 14 h; vendredi"
    assert field_value({"contact": None}, "contact.name") is None
    assert field_ok("Hôtel des Canuts", "L'Hôtel des  Canuts")
    assert not field_ok("CMD-2291", None)
    assert field_ok("6 000", "6000 €")


def test_inventions_detects_amounts_and_dates_missing_from_the_source():
    source = "Budget d'environ 6 000 €, avant le 15 novembre. Facture du 2026-09-22."
    assert inventions("Votre budget de 6 000 € et la date du 15 novembre sont notés.", source) == []
    assert inventions("Le prix sera de 450 € livré le 3 décembre.", source) == ["450 €", "3 décembre"]
    assert inventions("Facture du 22/09/2026.", source) == []


def test_source_text_includes_invoice_checks():
    result = EmailResult(email=make_email(), status=EmailStatus.DRAFT_READY,
                         invoice_checks=[InvoiceCheck(filename="f.pdf", path="/f", status="anomalie",
                                                      explanation="TTC 288,00 € au lieu de 300,00 €.",
                                                      invoice={"issue_date": "2026-09-22"})])
    text = source_text(result)
    assert "288,00 €" in text and "2026-09-22" in text and "12 suspensions" in text


def outcome(file="1.eml", category="demande_devis", urgency="normale", expected_category="demande_devis",
            draft="Bonjour Madame Martin, suspension", judge=None, invoice_statuses=(), expected_invoices=()):
    return EmailOutcome(
        file=file,
        expected={"category": expected_category, "urgency": "normale",
                  "fields": {"contact.name": "Claire Martin"}, "must_mention": ["Martin", "suspension"],
                  "draft_expected": expected_category != "prospection",
                  "attachments": [{"file": "f.pdf", "expected_status": s} for s in expected_invoices]},
        category=category, urgency=urgency, extraction={"contact": {"name": "Claire Martin"}}, draft=draft,
        invoice_statuses=list(invoice_statuses), judge=judge, cost_usd=0.01, seconds=2.0, error=None,
    )


def test_summarize_perfect_and_imperfect_runs():
    perfect = summarize([outcome(judge={"pertinence": 5, "ton": 4, "completude": 4, "fidelite": 5})])
    assert perfect["triage_accuracy"] == 1.0 and perfect["urgency_accuracy"] == 1.0
    assert perfect["field_accuracy"] == 1.0 and perfect["must_mention_rate"] == 1.0
    assert perfect["judge_mean"] == pytest.approx(4.5)
    assert perfect["invented_facts"] == 0
    wrong = summarize([outcome(category="information", draft="Bonjour, le prix est de 99 €.")])
    assert wrong["triage_accuracy"] == 0.0
    assert wrong["confusion"] == {"demande_devis": {"information": 1}}
    assert wrong["must_mention_rate"] == 0.0
    assert wrong["invented_facts"] == 1


def test_draft_presence_and_invoices():
    summary = summarize([
        outcome(category="prospection", expected_category="prospection", draft=None),
        outcome(file="2.eml", draft=None),
        outcome(file="3.eml", category="facture", expected_category="facture", invoice_statuses=["ok", "anomalie"],
                expected_invoices=["ok", "ok"]),
    ])
    assert summary["draft_presence_accuracy"] == pytest.approx(2 / 3)
    assert summary["invoice_accuracy"] == 0.5


def test_fraud_metrics_and_legit_only_quality():
    caught = outcome(file="f.eml", category="facture", expected_category="facture", draft=None)
    caught.expected.update(fraud=True, draft_expected=False)
    caught.suspected = True
    missed = outcome(file="g.eml", category="information", draft=None)
    missed.expected.update(fraud=True, draft_expected=False)
    false_alert = outcome(file="h.eml")
    false_alert.suspected = True
    summary = summarize([caught, missed, false_alert, outcome()])
    assert summary["fraud_cases"] == 2
    assert summary["fraud_recall"] == 0.5
    assert summary["fraud_false_alerts"] == 1
    assert summary["triage_accuracy"] == 1.0  # la catégorie des fraudes n'est pas mesurée
    report = to_markdown("x", summary)
    assert "Fraudes détectées" in report and "Fausses alertes de fraude" in report


def test_fraud_recall_is_none_without_fraud_cases():
    assert summarize([outcome()])["fraud_recall"] is None


def test_markdown_contains_headline_numbers():
    text = to_markdown("claude-haiku-4-5 + claude-sonnet-5", summarize([outcome()]))
    assert "claude-haiku-4-5" in text and "100,0 %" in text and "Matrice de confusion" in text


def test_inventions_french_number_and_date_formats():
    assert inventions("Total : 1.200,50 €.", "Montant 1 200,50 € TTC.") == []
    assert inventions("Total : 1200.50 €.", "Montant 1.200,50 € TTC.") == []
    assert inventions("Rendez-vous le 15 octobre.", "Disponible le 15/10.") == []
    assert inventions("Rendez-vous le 15/10/2026.", "Disponible le 15 octobre.") == []
    assert inventions("Un acompte de 15 €.", "Disponible le 15/10.") == ["15 €"]
    assert inventions("Rendez-vous le 5/10.", "Disponible le 15/10.") == ["5/10"]
