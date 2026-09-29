import pytest

from agent_emails.models import Category, Complaint, Contact, QuoteRequest, Triage
from agent_emails.pipeline.extraction import extract_info
from agent_emails.pipeline.triage import triage_email
from tests.factories import make_email
from tests.fakes import FakeClient, reply

TRIAGE = Triage(category="demande_devis", urgency="normale", confidence=0.9, explanation="Devis.", summary="Devis suspensions")


def test_triage_uses_haiku_and_protects_against_injection():
    client = FakeClient([reply(TRIAGE)])
    triage, usage = triage_email(client, make_email(), "Atelier Lumière SAS")
    assert triage.category is Category.QUOTE
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-haiku-4-5"
    assert sent["output_format"] is Triage
    assert "Atelier Lumière SAS" in sent["system"]
    assert "<email>" in sent["system"] and "instructions" in sent["system"]
    for value in ("demande_devis", "reclamation", "facture", "rendez_vous", "information", "prospection"):
        assert value in sent["system"]
    assert sent["messages"][0]["content"].startswith("<email>")
    assert usage.input_tokens == 100


def test_triage_model_can_be_changed():
    client = FakeClient([reply(TRIAGE)])
    triage_email(client, make_email(), "X", model="claude-sonnet-5")
    assert client.messages.calls[0]["model"] == "claude-sonnet-5"


def test_extraction_uses_the_schema_of_the_category():
    expected = QuoteRequest(contact=Contact(name="Claire Martin", company="Hôtel des Canuts"),
                            product_or_service="suspensions en laiton", quantity="12", desired_date=None, budget=None)
    client = FakeClient([reply(expected)])
    info, _ = extract_info(client, make_email(), Category.QUOTE, "Atelier Lumière SAS")
    assert info.quantity == "12"
    sent = client.messages.calls[0]
    assert sent["output_format"] is QuoteRequest
    assert "demande_devis" in sent["system"] and "null" in sent["system"]


def test_extraction_for_complaints():
    client = FakeClient([reply(Complaint(contact=Contact(name=None, company=None), order_reference="CMD-2291",
                                         problem="abat-jour cassé", expectation="remplacement"))])
    info, _ = extract_info(client, make_email(), Category.COMPLAINT, "X")
    assert info.order_reference == "CMD-2291"
    assert client.messages.calls[0]["output_format"] is Complaint


def test_no_extraction_for_prospecting():
    with pytest.raises(ValueError):
        extract_info(FakeClient([]), make_email(), Category.PROSPECTING, "X")
