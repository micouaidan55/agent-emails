from datetime import date
from decimal import Decimal
from pathlib import Path

from agent_factures.agent.loop import AgentResult
from agent_factures.extraction.models import Invoice, Issue, Status, Verdict

from agent_emails.models import Attachment
from agent_emails.pipeline.invoices import check_invoices
from tests.factories import make_email, pdf_attachment

INVOICE = Invoice(
    doc_type="facture", direction="recue", supplier="TechNet Services", customer="Atelier Lumière SAS",
    number="TN-960", issue_date=date(2026, 9, 22), due_date=date(2026, 10, 22),
    amount_excl_tax=Decimal("240.00"), vat_amount=Decimal("60.00"), amount_incl_tax=Decimal("288.00"),
)


class StubAnalyzer:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.paths = result, error, []

    def process(self, path: Path) -> AgentResult:
        self.paths.append(path)
        if self.error:
            raise self.error
        return self.result


def anomaly() -> AgentResult:
    return AgentResult(
        invoice=INVOICE, partial_extraction=None,
        verdict=Verdict(status=Status.ANOMALY, explanation="TVA incohérente."),
        issues=[Issue(code="TOTAL_INCOHERENT", message="HT + TVA = 300,00 € mais le TTC indiqué est 288,00 €.")],
        trace=[], input_tokens=100, output_tokens=10, cost_usd=0.03,
    )


def test_supported_attachments_are_written_and_analyzed(tmp_path):
    email = make_email(attachments=[pdf_attachment("../TN-960.pdf"), Attachment(filename="logo.gif", content_type="image/gif", data=b"GIF")])
    analyzer = StubAnalyzer(result=anomaly())
    checks = check_invoices(email, analyzer, tmp_path)
    assert len(checks) == 1
    written = analyzer.paths[0]
    assert written.name == "TN-960.pdf" and written.parent.parent == tmp_path
    assert written.read_bytes() == b"%PDF-1.4 fake"
    check = checks[0]
    assert (check.filename, check.status) == ("TN-960.pdf", "anomalie")
    assert check.issues[0].code == "TOTAL_INCOHERENT"
    assert check.invoice["number"] == "TN-960"
    assert check.path == str(written.resolve())
    assert check.cost_usd == 0.03


def test_analyzer_failure_becomes_a_review(tmp_path):
    checks = check_invoices(make_email(attachments=[pdf_attachment()]), StubAnalyzer(error=RuntimeError("panne")), tmp_path)
    assert checks[0].status == "a_revoir"
    assert "panne" in checks[0].explanation
    assert checks[0].invoice is None


def test_no_attachment_no_check(tmp_path):
    assert check_invoices(make_email(), StubAnalyzer(result=anomaly()), tmp_path) == []


def test_first_analyzer_fails_second_ok(tmp_path):
    email = make_email(attachments=[pdf_attachment("first.pdf"), pdf_attachment("second.pdf")])
    analyzer = StubAnalyzer(result=anomaly())
    analyzer.result_for_call = {0: RuntimeError("premier échoue"), 1: anomaly()}

    class SelectiveErrorAnalyzer:
        def __init__(self):
            self.paths = []
            self.call_count = 0

        def process(self, path: Path) -> AgentResult:
            self.paths.append(path)
            if self.call_count == 0:
                self.call_count += 1
                raise RuntimeError("premier échoue")
            self.call_count += 1
            return anomaly()

    analyzer = SelectiveErrorAnalyzer()
    checks = check_invoices(email, analyzer, tmp_path)
    assert len(checks) == 2
    assert checks[0].status == "a_revoir"
    assert "premier échoue" in checks[0].explanation
    assert checks[1].status == "anomalie"
    assert checks[1].invoice["number"] == "TN-960"


def test_duplicate_basenames_get_suffixed(tmp_path):
    email = make_email(attachments=[pdf_attachment("facture.pdf"), pdf_attachment("facture.pdf")])
    analyzer = StubAnalyzer(result=anomaly())
    checks = check_invoices(email, analyzer, tmp_path)
    assert len(checks) == 2
    paths = [Path(c.path) for c in checks]
    assert paths[0].name == "facture.pdf"
    assert paths[1].name == "facture_2.pdf"
    assert paths[0] != paths[1]
    assert paths[0].read_bytes() == b"%PDF-1.4 fake"
    assert paths[1].read_bytes() == b"%PDF-1.4 fake"


def test_write_failure_becomes_a_revoir(tmp_path, monkeypatch):
    email = make_email(attachments=[pdf_attachment()])
    analyzer = StubAnalyzer(result=anomaly())

    original_write = Path.write_bytes

    def failing_write(self, data):
        raise OSError("Écriture impossible")

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    checks = check_invoices(email, analyzer, tmp_path)
    assert len(checks) == 1
    assert checks[0].status == "a_revoir"
    assert "Écriture impossible" in checks[0].explanation
    assert checks[0].invoice is None
