import anthropic
import httpx
from agent_factures.agent.loop import AgentResult
from agent_factures.extraction.models import Status, Verdict

from agent_emails.connectors.folder import FolderSource
from agent_emails.models import (
    AppointmentRequest, Category, Contact, EmailStatus, InvoiceNotice, QuoteRequest, Triage,
)
from agent_emails.pipeline.drafting import DraftOutput
from agent_emails.pipeline.runner import Pipeline, PipelineConfig, process_inbox, regenerate_draft
from agent_emails.storage.db import connect
from agent_emails.storage.repository import EmailStore
from tests.factories import build_eml, make_email, pdf_attachment
from tests.fakes import FakeClient, reply

CONTACT = Contact(name="Claire Martin", company=None)
OUTPUTS = {
    QuoteRequest: QuoteRequest(contact=CONTACT, product_or_service="suspensions", quantity="12", desired_date=None, budget=None),
    InvoiceNotice: InvoiceNotice(contact=CONTACT, supplier="Bureau Plus SARL", invoice_number="F-2026-190"),
    AppointmentRequest: AppointmentRequest(contact=CONTACT, proposed_slots=["jeudi 14 h"], purpose="showroom"),
    DraftOutput: DraftOutput(body="Bonjour Claire,"),
}


def client_for(category="demande_devis", urgency="normale", confidence=0.9):
    triage = Triage(category=category, urgency=urgency, confidence=confidence, explanation="E.", summary="S")

    def respond(kwargs):
        model = kwargs["output_format"]
        return reply(triage if model is Triage else OUTPUTS[model], input_tokens=1000, output_tokens=100)

    return FakeClient(respond)


class StubAnalyzer:
    def process(self, path):
        return AgentResult(invoice=None, partial_extraction=None,
                           verdict=Verdict(status=Status.OK, explanation="Conforme."),
                           issues=[], trace=[], input_tokens=0, output_tokens=0, cost_usd=0.03)


def pipeline(client, tmp_path, analyzer=None):
    return Pipeline(client, analyzer, PipelineConfig(company="Atelier Lumière SAS", attachments_dir=tmp_path))


def test_quote_goes_through_every_step(tmp_path):
    client = client_for()
    result = pipeline(client, tmp_path).process(make_email())
    assert result.status is EmailStatus.DRAFT_READY
    assert result.triage.category is Category.QUOTE
    assert result.extraction["quantity"] == "12"
    assert result.draft == "Bonjour Claire,"
    assert [c["model"] for c in client.messages.calls] == ["claude-haiku-4-5", "claude-haiku-4-5", "claude-sonnet-5"]
    expected_cost = 2 * (1000 * 1 + 100 * 5) / 1e6 + (1000 * 2 + 100 * 10) / 1e6
    assert abs(result.usage.cost_usd - expected_cost) < 1e-9
    assert result.seconds >= 0


def test_prospecting_stops_after_triage(tmp_path):
    client = client_for(category="prospection", urgency="basse")
    result = pipeline(client, tmp_path).process(make_email())
    assert result.status is EmailStatus.TO_IGNORE
    assert result.extraction is None and result.draft is None
    assert len(client.messages.calls) == 1


def test_low_confidence_needs_review(tmp_path):
    result = pipeline(client_for(confidence=0.5), tmp_path).process(make_email())
    assert result.status is EmailStatus.TO_CHECK
    assert result.draft == "Bonjour Claire,"


def test_invoice_attachments_are_checked_and_costed(tmp_path):
    client = client_for(category="facture")
    email = make_email(attachments=[pdf_attachment("F-2026-190.pdf")])
    result = pipeline(client, tmp_path, StubAnalyzer()).process(email)
    assert [c.status for c in result.invoice_checks] == ["ok"]
    assert result.usage.cost_usd > 0.03


def test_step_error_keeps_the_email_for_review(tmp_path):
    client = FakeClient([reply(None), reply(None)])
    result = pipeline(client, tmp_path).process(make_email())
    assert result.status is EmailStatus.TO_CHECK
    assert "tentatives" in result.error
    assert result.usage.input_tokens == 200


def test_api_error_is_an_error_status(tmp_path):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    result = pipeline(FakeClient([error]), tmp_path).process(make_email())
    assert result.status is EmailStatus.ERROR
    assert "API" in result.error


def test_process_inbox_saves_results_and_skips_known_emails(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "01.eml").write_bytes(build_eml(message_id="<1@x>"))
    (inbox / "02.eml").write_bytes(build_eml(message_id="<2@x>"))
    (inbox / "03.eml").write_bytes(build_eml(message_id="<3@x>", sender=None))
    store = EmailStore(connect())
    seen = []
    results = process_inbox(FolderSource(inbox, tmp_path / "drafts"), store, pipeline(client_for(), tmp_path),
                            on_result=seen.append)
    assert [r.email.message_id for r in results] == ["<1@x>", "<2@x>"] == [r.email.message_id for r in seen]
    assert store.get("<1@x>").status is EmailStatus.DRAFT_READY
    assert store.get("fichier:03.eml").status is EmailStatus.ERROR
    assert process_inbox(FolderSource(inbox, tmp_path / "drafts"), store, pipeline(client_for(), tmp_path)) == []
    assert [e["action"] for e in store.list_log()][:2] == ["analyse", "analyse"]


def test_regenerate_draft_rebuilds_inputs_from_storage(tmp_path):
    store = EmailStore(connect())
    store.save_result(pipeline(client_for(), tmp_path).process(make_email()))
    client = FakeClient([reply(DraftOutput(body="Nouveau brouillon"))])
    draft, usage = regenerate_draft(client, store.get("<devis-1@exemple.fr>"), "Atelier Lumière SAS")
    assert draft == "Nouveau brouillon"
    assert "12" in client.messages.calls[0]["messages"][0]["content"]


def _inbox(tmp_path, count):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for number in range(1, count + 1):
        (inbox / f"0{number}.eml").write_bytes(build_eml(message_id=f"<{number}@x>"))
    return FolderSource(inbox, tmp_path / "drafts")


def test_email_in_api_error_is_processed_again_on_next_run(tmp_path):
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    store = EmailStore(connect())
    source = _inbox(tmp_path, 1)
    process_inbox(source, store, pipeline(FakeClient([error]), tmp_path))
    assert store.get("<1@x>").status is EmailStatus.ERROR
    results = process_inbox(source, store, pipeline(client_for(), tmp_path))
    assert [r.status for r in results] == [EmailStatus.DRAFT_READY]
    assert store.get("<1@x>").status is EmailStatus.DRAFT_READY


def test_unexpected_exception_does_not_stop_the_inbox(tmp_path):
    store = EmailStore(connect())
    good = client_for()
    calls = []

    def respond(kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return RuntimeError("boum")
        return good.messages._responses(kwargs)

    results = process_inbox(_inbox(tmp_path, 2), store, pipeline(FakeClient(respond), tmp_path))
    assert [r.status for r in results] == [EmailStatus.ERROR, EmailStatus.DRAFT_READY]
    assert "boum" in results[0].error
    assert store.get("<1@x>").status is EmailStatus.ERROR
