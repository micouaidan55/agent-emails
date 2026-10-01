from datetime import datetime, timezone, timedelta

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
    store.correct("<b>", "category", "information")
    stats = store.stats()
    assert stats.total == 4
    assert stats.by_category == {"demande_devis": 2, "prospection": 1, "information": 1}
    assert stats.pending_high == 2
    assert (stats.drafts_to_review, stats.to_check, stats.validated) == (1, 1, 1)
    assert stats.validated_unchanged == 1
    assert stats.corrected == 1
    # Test that urgency-only correction does not increase corrected
    store.correct("<a>", "urgency", "basse")
    stats_after_urgency_correction = store.stats()
    assert stats_after_urgency_correction.corrected == 1


def test_save_result_preserves_decided_emails(store):
    """Fix 1: save_result must not overwrite a decided email."""
    store.save_result(result("<decided>", category="demande_devis"))
    stored1 = store.get("<decided>")
    assert stored1.status is EmailStatus.DRAFT_READY
    
    # Mark as VALIDATED
    store.set_status("<decided>", EmailStatus.VALIDATED)
    store.update_draft("<decided>", "Brouillon modifié")
    stored2 = store.get("<decided>")
    assert stored2.status is EmailStatus.VALIDATED
    assert stored2.draft == "Brouillon modifié"
    assert stored2.category is Category.QUOTE
    
    # Try to save a new result with the same message_id - should not overwrite
    store.save_result(result("<decided>", category="prospection", draft="Nouveau brouillon"))
    stored3 = store.get("<decided>")
    assert stored3.status is EmailStatus.VALIDATED, "VALIDATED status should be preserved"
    assert stored3.draft == "Brouillon modifié", "Modified draft should be preserved"
    assert stored3.category is Category.QUOTE, "Category should be preserved"


def test_save_result_updates_non_decided_emails(store):
    """Fix 1b: save_result should update non-decided rows like ERROR."""
    store.record_fetch_error("erreur.eml", "Expéditeur introuvable")
    stored1 = store.get("fichier:erreur.eml")
    assert stored1.status is EmailStatus.ERROR
    assert stored1.error == "Expéditeur introuvable"
    
    # Simulate saving a result that now has a proper sender (non-decided row should be updated)
    # We save a result with the same identifier pattern
    store.save_result(result("fichier:erreur.eml", category="demande_devis", draft="Brouillon"))
    stored2 = store.get("fichier:erreur.eml")
    # Non-decided (ERROR is not in VALIDATED/IGNORED) so it should be updated
    assert stored2.status is EmailStatus.DRAFT_READY
    assert stored2.draft == "Brouillon"
    assert stored2.category is Category.QUOTE


def test_correct_validates_category_enum(store):
    """Fix 3: correct() must validate that new_value is a valid Category."""
    store.save_result(result())
    stored1 = store.get("<a@x.fr>")
    assert stored1.category is Category.QUOTE
    
    # Try to set invalid category value - should raise ValueError before writing
    with pytest.raises(ValueError):
        store.correct("<a@x.fr>", "category", "foo")
    
    # Verify the row still loads and is unchanged
    stored2 = store.get("<a@x.fr>")
    assert stored2.category is Category.QUOTE


def test_correct_validates_urgency_enum(store):
    """Fix 3b: correct() must validate that new_value is a valid Urgency."""
    store.save_result(result())
    stored1 = store.get("<a@x.fr>")
    assert stored1.urgency is Urgency.NORMAL
    
    # Try to set invalid urgency value - should raise ValueError before writing
    with pytest.raises(ValueError):
        store.correct("<a@x.fr>", "urgency", "tres_urgente")
    
    # Verify the row still loads and is unchanged
    stored2 = store.get("<a@x.fr>")
    assert stored2.urgency is Urgency.NORMAL


def test_correct_on_unknown_message_id(store):
    """Fix 4: correct() on unknown message_id must raise ValueError and insert nothing."""
    with pytest.raises(ValueError):
        store.correct("<unknown>", "category", "prospection")
    
    # Verify nothing was inserted
    assert store.get("<unknown>") is None


def test_received_at_stored_in_utc(store):
    """Fix 5: received_at must be stored normalized to UTC."""
    # Create two emails with different timezone info but same wall time
    # 10:00+02:00 is 08:00 UTC
    # 09:30+00:00 is 09:30 UTC (so later)
    tz_plus_2 = timezone(timedelta(hours=2))
    tz_utc = timezone.utc
    
    email1_time = datetime(2026, 9, 28, 10, 0, tzinfo=tz_plus_2)  # 08:00 UTC
    email2_time = datetime(2026, 9, 28, 9, 30, tzinfo=tz_utc)     # 09:30 UTC
    
    # Manually create emails with specific times
    from agent_emails.models import Email
    from agent_emails.models import Attachment
    
    email1 = Email(
        message_id="<tz1>",
        sender_name="Sender1",
        sender_address="sender1@example.com",
        subject="Test 1",
        received_at=email1_time,
        body="Content 1",
        attachments=[],
    )
    email2 = Email(
        message_id="<tz2>",
        sender_name="Sender2",
        sender_address="sender2@example.com",
        subject="Test 2",
        received_at=email2_time,
        body="Content 2",
        attachments=[],
    )
    
    result1 = EmailResult(
        email=email1,
        status=EmailStatus.DRAFT_READY,
        triage=Triage(category="demande_devis", urgency="normale", confidence=0.9,
                      explanation="Exp.", summary="Sum"),
        extraction=None,
        invoice_checks=[],
        draft=None,
        usage=Usage(input_tokens=0, output_tokens=0, cost_usd=0.0),
        seconds=0,
    )
    result2 = EmailResult(
        email=email2,
        status=EmailStatus.DRAFT_READY,
        triage=Triage(category="demande_devis", urgency="normale", confidence=0.9,
                      explanation="Exp.", summary="Sum"),
        extraction=None,
        invoice_checks=[],
        draft=None,
        usage=Usage(input_tokens=0, output_tokens=0, cost_usd=0.0),
        seconds=0,
    )
    
    store.save_result(result1)
    store.save_result(result2)
    
    # email2 is later (09:30 UTC vs 08:00 UTC), so should come first
    emails = store.list_emails()
    assert [e.message_id for e in emails] == ["<tz2>", "<tz1>"]
    
    # Verify that get() returns datetimes equal to the originals
    stored1 = store.get("<tz1>")
    stored2 = store.get("<tz2>")
    assert stored1.received_at == email1_time
    assert stored2.received_at == email2_time


def test_known_ids_excludes_emails_in_error_so_they_are_retried(store):
    ok, failed = result(), result()
    ok.email.message_id, failed.email.message_id = "<ok@x.fr>", "<ko@x.fr>"
    failed.status = EmailStatus.ERROR
    store.save_result(ok)
    store.save_result(failed)
    assert store.known_ids() == {"<ok@x.fr>"}


from agent_emails.models import FraudRisk, FraudSeverity, FraudSignal, Link


def test_fraud_fields_roundtrip(store):
    signal = FraudSignal(code="lien_trompeur", severity=FraudSeverity.STRONG, message="Lien trompeur.")
    r = result(status=EmailStatus.SUSPECT, fraud_signals=[signal])
    r.email.reply_to = "paiement@ailleurs.com"
    r.email.links = [Link(text="www.banque.fr", url="http://voleur.xyz")]
    r.triage.fraud_risk = FraudRisk.HIGH
    r.triage.fraud_reason = "Changement de RIB."
    store.save_result(r)
    stored = store.get("<a@x.fr>")
    assert stored.status is EmailStatus.SUSPECT
    assert stored.fraud_signals == [signal]
    assert stored.fraud_risk == "eleve" and stored.fraud_reason == "Changement de RIB."
    assert stored.reply_to == "paiement@ailleurs.com"
    assert stored.to_email().links == [Link(text="www.banque.fr", url="http://voleur.xyz")]


def test_suspects_come_first(store):
    store.save_result(result(message_id="<a@x.fr>", status=EmailStatus.TO_CHECK, urgency="haute"))
    store.save_result(result(message_id="<b@x.fr>", status=EmailStatus.SUSPECT, urgency="basse"))
    assert [e.message_id for e in store.list_emails()][0] == "<b@x.fr>"


def test_confirmed_fraud_is_never_overwritten(store):
    store.save_result(result(status=EmailStatus.SUSPECT))
    store.set_status("<a@x.fr>", EmailStatus.FRAUD)
    store.save_result(result(status=EmailStatus.DRAFT_READY))
    assert store.get("<a@x.fr>").status is EmailStatus.FRAUD


def test_known_senders(store):
    store.save_result(EmailResult(email=make_email(message_id="<v@x.fr>", sender_address="a@client.fr"),
                                  status=EmailStatus.DRAFT_READY))
    store.set_status("<v@x.fr>", EmailStatus.VALIDATED)
    store.save_result(EmailResult(email=make_email(message_id="<p@x.fr>", sender_address="p@pending.fr"),
                                  status=EmailStatus.DRAFT_READY))
    store.trust_sender("Artisan.Dupuis@Gmail.com")
    store.trust_sender("compta@fournisseur.fr")
    known = store.known_senders("atelier-lumiere.fr")
    assert known.domains == {"atelier-lumiere.fr", "client.fr", "fournisseur.fr"}
    assert known.addresses == {"artisan.dupuis@gmail.com"}


def test_fraud_stats(store):
    store.save_result(result(message_id="<a@x.fr>", status=EmailStatus.SUSPECT))
    store.save_result(result(message_id="<b@x.fr>", status=EmailStatus.SUSPECT))
    store.set_status("<b@x.fr>", EmailStatus.FRAUD)
    store.log("<c@x.fr>", "fraude_levee")
    stats = store.stats()
    assert (stats.suspects, stats.fraud_confirmed, stats.fraud_cleared) == (1, 1, 1)


def test_connect_migrates_an_old_database(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE emails (message_id TEXT PRIMARY KEY, sender_name TEXT NOT NULL DEFAULT '',"
                " sender_address TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL,"
                " body TEXT NOT NULL DEFAULT '', attachments TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,"
                " category TEXT, urgency TEXT, confidence REAL, explanation TEXT, summary TEXT, extraction TEXT,"
                " invoice_checks TEXT NOT NULL DEFAULT '[]', draft TEXT, draft_original TEXT, error TEXT,"
                " cost_usd REAL NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0,"
                " processed_at TEXT NOT NULL DEFAULT (datetime('now')), decided_at TEXT)")
    old.commit()
    old.close()
    migrated = EmailStore(connect(str(path)))
    migrated.save_result(result(status=EmailStatus.SUSPECT))
    assert migrated.get("<a@x.fr>").status is EmailStatus.SUSPECT
