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


from agent_emails.models import FraudRisk, Link
from agent_emails.pipeline.fraud import KnownSenders
from agent_emails.pipeline.runner import reprocess_cleared


def fraud_client(category="facture", risk="aucun"):
    triage = Triage(category=category, urgency="haute", confidence=0.9, explanation="E.", summary="S",
                    fraud_risk=risk, fraud_reason="Raison.")

    def respond(kwargs):
        model = kwargs["output_format"]
        return reply(triage if model is Triage else OUTPUTS[model], input_tokens=1000, output_tokens=100)

    return FakeClient(respond)


class FailingAnalyzer:
    def process(self, path):
        raise AssertionError("une facture suspecte ne doit pas être analysée")


KNOWN = KnownSenders(domains=frozenset({"hotel-canuts.fr"}))


def test_high_risk_from_the_model_stops_the_pipeline(tmp_path):
    client = fraud_client(risk="eleve")
    result = pipeline(client, tmp_path, FailingAnalyzer()).process(
        make_email(attachments=[pdf_attachment()]), known=KNOWN)
    assert result.status is EmailStatus.SUSPECT
    assert result.extraction is None and result.draft is None and result.invoice_checks == []
    assert len(client.messages.calls) == 1
    assert result.triage.fraud_risk is FraudRisk.HIGH


def test_strong_signal_alone_stops_the_pipeline(tmp_path):
    client = fraud_client()
    result = pipeline(client, tmp_path).process(make_email(sender_address="c.martin@hotel-canut.fr"), known=KNOWN)
    assert result.status is EmailStatus.SUSPECT
    assert [s.code for s in result.fraud_signals] == ["domaine_sosie"]


def test_one_weak_signal_does_not_block(tmp_path):
    client = fraud_client(category="demande_devis")
    result = pipeline(client, tmp_path).process(make_email(reply_to="autre@ailleurs.fr"), known=KNOWN)
    assert result.status is EmailStatus.DRAFT_READY
    assert [s.code for s in result.fraud_signals] == ["reponse_ailleurs"]


def test_signals_still_protect_when_the_api_fails(tmp_path):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient([anthropic.APIConnectionError(request=request)])
    email = make_email(links=[Link(text="www.banque.fr", url="http://voleur.xyz")])
    result = pipeline(client, tmp_path).process(email, known=KNOWN)
    assert result.status is EmailStatus.SUSPECT


def test_check_fraud_false_processes_normally(tmp_path):
    client = fraud_client(category="demande_devis", risk="eleve")
    result = pipeline(client, tmp_path).process(make_email(), known=KNOWN, check_fraud=False)
    assert result.status is EmailStatus.DRAFT_READY
    assert result.draft == "Bonjour Claire,"


def test_company_domain_is_known_by_default(tmp_path):
    client = fraud_client(category="demande_devis")
    p = Pipeline(client, None, PipelineConfig(company="Atelier Lumière SAS", attachments_dir=tmp_path,
                                             company_domain="atelier-lumiere.fr"))
    result = p.process(make_email(sender_address="direction@atelier-lumiere.co"))
    assert result.status is EmailStatus.SUSPECT


def test_cleared_alert_reprocesses_the_original_email(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.eml").write_bytes(build_eml(message_id="<a@x.fr>", sender="Bob <bob@nouveau-client.fr>",
                                            reply_to="compta@ailleurs.fr", body="Nouveau RIB : merci d'en tenir compte."))
    store = EmailStore(connect())
    source = FolderSource(inbox, tmp_path / "drafts")
    p = pipeline(fraud_client(category="demande_devis"), tmp_path)
    [first] = process_inbox(source, store, p)
    assert first.status is EmailStatus.SUSPECT
    result = reprocess_cleared(source, store, p, "<a@x.fr>")
    assert result.status is EmailStatus.DRAFT_READY
    assert store.get("<a@x.fr>").status is EmailStatus.DRAFT_READY
    assert "nouveau-client.fr" in store.known_senders().domains
    assert [e["action"] for e in store.list_log()][:2] == ["analyse", "fraude_levee"]


def test_reprocess_cleared_ignores_non_suspect_emails(tmp_path):
    store = EmailStore(connect())
    assert reprocess_cleared(FolderSource(tmp_path, tmp_path), store, pipeline(fraud_client(), tmp_path), "<x@y>") is None


def test_process_inbox_uses_extra_domains_for_lookalikes(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.eml").write_bytes(build_eml(message_id="<l@x>", sender="C. Martin <c.martin@hotel-canut.fr>"))
    config = PipelineConfig(company="Atelier Lumière SAS", attachments_dir=tmp_path / "pj",
                            extra_domains=("hotel-canuts.fr",))
    store = EmailStore(connect())
    results = process_inbox(FolderSource(inbox, tmp_path / "drafts"), store,
                            Pipeline(fraud_client(category="demande_devis"), None, config))
    assert results[0].status is EmailStatus.SUSPECT


def test_reply_to_a_reminder_is_left_to_agent_relances(tmp_path):
    client = FakeClient([])
    email = make_email(subject="Re: Rappel facture AL-1 [Réf. R-00042]")
    result = pipeline(client, tmp_path).process(email)
    assert result.status is EmailStatus.TO_IGNORE
    assert result.triage is None and result.draft is None and result.error is None
    assert result.usage.cost_usd == 0
    assert result.note == "Réponse à une relance : traitée par agent-relances"
    assert client.messages.calls == []


def test_reply_to_an_installment_plan_is_left_to_agent_relances(tmp_path):
    client = FakeClient([])
    result = pipeline(client, tmp_path).process(make_email(subject="Re: Échéancier [Réf. E-00007]"))
    assert result.status is EmailStatus.TO_IGNORE and client.messages.calls == []


def test_email_without_reference_follows_the_normal_pipeline(tmp_path):
    client = client_for()
    result = pipeline(client, tmp_path).process(make_email(subject="Demande de devis [Réf. interne]"))
    assert result.status is EmailStatus.DRAFT_READY and len(client.messages.calls) == 3


def test_reminder_reply_is_journaled(tmp_path):
    source_dir = tmp_path / "inbox"
    source_dir.mkdir()
    (source_dir / "a.eml").write_bytes(build_eml(subject="Re: Rappel [Réf. R-00001]"))
    store = EmailStore(connect())
    results = process_inbox(FolderSource(source_dir, tmp_path / "drafts"), store, pipeline(FakeClient([]), tmp_path))
    assert results[0].status is EmailStatus.TO_IGNORE
    entry = store.list_log()[0]
    assert entry["detail"]["note"] == "Réponse à une relance : traitée par agent-relances"


def test_fake_reference_does_not_bypass_the_fraud_check(tmp_path):
    client = fraud_client()
    email = make_email(sender_address="c.martin@hotel-canut.fr", subject="Re: Rappel facture AL-1 [Réf. R-00042]")
    result = pipeline(client, tmp_path).process(email, known=KNOWN)
    assert result.status is EmailStatus.SUSPECT
    assert result.note is None
    assert [s.code for s in result.fraud_signals] == ["domaine_sosie"]


def test_clean_reminder_reply_is_still_ignored_without_model_call(tmp_path):
    client = FakeClient([])
    email = make_email(sender_address="c.martin@hotel-canuts.fr", subject="Re: Rappel facture AL-1 [Réf. R-00042]")
    result = pipeline(client, tmp_path).process(email, known=KNOWN)
    assert result.status is EmailStatus.TO_IGNORE
    assert result.note == "Réponse à une relance : traitée par agent-relances"
    assert client.messages.calls == []
