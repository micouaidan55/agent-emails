from datetime import datetime, timezone

import pytest

from agent_emails.models import (
    Category, EmailResult, EmailStatus, InvoiceCheck, Triage, Urgency, Usage,
)
from agent_emails.storage.db import connect
from agent_emails.storage.repository import EmailStore
from tests.factories import make_email, pdf_attachment


def result(message_id="<a@x.fr>", category="demande_devis", urgency="normale", status=EmailStatus.DRAFT_READY,
           confidence=0.9, draft="Bonjour", day=28, **kw) -> EmailResult:
    return EmailResult(
        email=make_email(message_id=message_id, received_at=datetime(2026, 9, day, 9, tzinfo=timezone.utc),
                         attachments=[pdf_attachment()]),
        status=status,
        triage=Triage(category=category, urgency=urgency, confidence=confidence,
                      explanation="Explication.", summary="Résumé court"),
        extraction={"contact": {"name": "Claire Martin", "company": None}},
        invoice_checks=[InvoiceCheck(filename="facture.pdf", path="/tmp/f.pdf", status="ok", explanation="Conforme.")],
        draft=draft,
        usage=Usage(input_tokens=100, output_tokens=20, cost_usd=0.012),
        seconds=3.5,
        **kw,
    )


@pytest.fixture
def store():
    return EmailStore(connect())


def test_save_and_get_roundtrip(store):
    store.save_result(result())
    stored = store.get("<a@x.fr>")
    assert stored.category is Category.QUOTE and stored.urgency is Urgency.NORMAL
    assert stored.status is EmailStatus.DRAFT_READY
    assert stored.attachments == ["facture.pdf"]
    assert stored.extraction["contact"]["name"] == "Claire Martin"
    assert stored.invoice_checks[0].status == "ok"
    assert stored.draft == stored.draft_original == "Bonjour"
    assert stored.cost_usd == pytest.approx(0.012)
    assert stored.received_at == datetime(2026, 9, 28, 9, tzinfo=timezone.utc)
    assert store.get("<absent>") is None


def test_to_email_rebuilds_the_message(store):
    store.save_result(result())
    email = store.get("<a@x.fr>").to_email()
    assert email.sender_address == "claire.martin@hotel-canuts.fr"
    assert [a.filename for a in email.attachments] == ["facture.pdf"]


def test_known_ids_and_fetch_errors(store):
    store.save_result(result())
    store.record_fetch_error("cassé.eml", "Expéditeur introuvable")
    assert store.known_ids() == {"<a@x.fr>", "fichier:cassé.eml"}
    error = store.get("fichier:cassé.eml")
    assert error.status is EmailStatus.ERROR and error.error == "Expéditeur introuvable"


def test_list_orders_by_attention_then_urgency_then_date(store):
    store.save_result(result("<low>", urgency="basse", day=28))
    store.save_result(result("<high-old>", urgency="haute", day=20))
    store.save_result(result("<high-new>", urgency="haute", day=27))
    store.save_result(result("<check>", urgency="basse", status=EmailStatus.TO_CHECK, confidence=0.4))
    assert [e.message_id for e in store.list_emails()] == ["<check>", "<high-new>", "<high-old>", "<low>"]
    assert [e.message_id for e in store.list_emails({EmailStatus.TO_CHECK})] == ["<check>"]


def test_update_draft_and_status(store):
    store.save_result(result())
    store.update_draft("<a@x.fr>", "Bonjour modifié")
    store.set_status("<a@x.fr>", EmailStatus.VALIDATED)
    stored = store.get("<a@x.fr>")
    assert (stored.draft, stored.draft_original) == ("Bonjour modifié", "Bonjour")
    assert stored.status is EmailStatus.VALIDATED
    store.update_draft("<a@x.fr>", "Nouveau brouillon", original=True)
    assert store.get("<a@x.fr>").draft_original == "Nouveau brouillon"


def test_correct_records_the_change(store):
    store.save_result(result())
    store.correct("<a@x.fr>", "category", "reclamation")
    assert store.get("<a@x.fr>").category is Category.COMPLAINT
    with pytest.raises(ValueError):
        store.correct("<a@x.fr>", "draft", "x")


def test_log(store):
    store.log("<a@x.fr>", "brouillon_valide", {"modifie": True})
    entries = store.list_log()
    assert entries[0]["action"] == "brouillon_valide" and entries[0]["detail"] == {"modifie": True}


def test_stats(store):
    store.save_result(result("<a>", category="demande_devis", urgency="haute"))
    store.save_result(result("<b>", category="reclamation", urgency="haute", status=EmailStatus.TO_CHECK))
    store.save_result(result("<c>", category="prospection", urgency="basse", status=EmailStatus.TO_IGNORE, draft=None))
    store.save_result(result("<d>", category="demande_devis"))
    store.set_status("<d>", EmailStatus.VALIDATED)
    store.correct("<b>", "urgency", "normale")
    stats = store.stats()
    assert stats.total == 4
    assert stats.by_category == {"demande_devis": 2, "reclamation": 1, "prospection": 1}
    assert stats.pending_high == 1
    assert (stats.drafts_to_review, stats.to_check, stats.validated) == (1, 1, 1)
    assert stats.validated_unchanged == 1
    assert stats.corrected == 1
    assert stats.mean_cost_usd == pytest.approx(0.012)
