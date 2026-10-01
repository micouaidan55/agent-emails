from datetime import date

from agent_factures.extraction.models import Invoice, Issue

from agent_emails.models import InvoiceCheck, Triage, Usage
from agent_emails.pipeline.drafting import DraftOutput, correction_request, draft_reply
from tests.factories import make_email
from tests.fakes import FakeClient, reply

TRIAGE = Triage(category="demande_devis", urgency="normale", confidence=0.9, explanation="Devis.", summary="Devis")


def anomaly_check(direction="recue", code="TOTAL_INCOHERENT") -> InvoiceCheck:
    invoice = Invoice(doc_type="facture", direction=direction, supplier="TechNet Services",
                      customer="Atelier Lumière SAS", number="TN-960", issue_date=date(2026, 9, 22),
                      amount_excl_tax="240.00", vat_amount="60.00", amount_incl_tax="288.00")
    return InvoiceCheck(filename="TN-960.pdf", path="/tmp/TN-960.pdf", status="anomalie", explanation="TVA.",
                        issues=[Issue(code=code, message="HT + TVA = 300,00 € mais le TTC indiqué est 288,00 €.")],
                        invoice=invoice.model_dump(mode="json"))


def test_prospecting_gets_no_draft():
    triage = TRIAGE.model_copy(update={"category": "prospection"})
    client = FakeClient([])
    assert draft_reply(client, make_email(), triage, None, [], "X") == (None, Usage())
    assert client.messages.calls == []


def test_invoice_amount_error_reuses_agent_factures_correction_request():
    triage = TRIAGE.model_copy(update={"category": "facture"})
    client = FakeClient([])
    draft, usage = draft_reply(client, make_email(), triage, None, [anomaly_check()], "Atelier Lumière SAS")
    assert "facture rectificative" in draft and "TN-960" in draft and "TechNet Services" in draft
    assert usage == Usage()
    assert client.messages.calls == []


def test_correction_request_ignores_other_cases():
    assert correction_request([anomaly_check(code="DOUBLON")]) is None
    assert correction_request([anomaly_check(direction="emise")]) is None
    assert correction_request([]) is None


def test_regular_draft_uses_sonnet_with_the_analysis():
    client = FakeClient([reply(DraftOutput(body="Bonjour Madame Martin,\nMerci pour votre demande."))])
    extraction = {"contact": {"name": "Claire Martin", "company": "Hôtel des Canuts"}, "quantity": "12"}
    draft, usage = draft_reply(client, make_email(), TRIAGE, extraction, [], "Atelier Lumière SAS")
    assert draft.startswith("Bonjour Madame Martin")
    sent = client.messages.calls[0]
    assert sent["model"] == "claude-sonnet-5" and sent["max_tokens"] == 16000
    assert sent["output_format"] is DraftOutput
    assert "n'invente" in sent["system"] and "Atelier Lumière SAS" in sent["system"]
    content = sent["messages"][0]["content"]
    assert content.startswith("<email>") and "<analyse>" in content and "Hôtel des Canuts" in content
    assert usage.input_tokens == 100
