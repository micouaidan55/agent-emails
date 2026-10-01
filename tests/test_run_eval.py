import anthropic
import pytest

import evals.run as run_module


def test_run_exits_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("sys.argv", ["run.py"])

    def fail(*args, **kwargs):
        raise AssertionError("Aucun client ne doit être créé sans clé.")

    monkeypatch.setattr(anthropic, "Anthropic", fail)
    with pytest.raises(SystemExit) as info:
        run_module.main()
    assert info.value.code == 1


def test_budget_cap_stops_run_and_writes_partial_results(monkeypatch, tmp_path):
    import json
    from agent_emails.models import EmailResult, EmailStatus, Usage
    from tests.factories import make_email

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    items = [{"file": f"{i}.eml", "category": "information", "urgency": "normale", "fields": {},
              "must_mention": [], "draft_expected": False, "attachments": []} for i in range(3)]
    (dataset / "expected.json").write_text(json.dumps({"today": "2026-09-29", "emails": items}))
    for item in items:
        (dataset / item["file"]).write_bytes(b"x")

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, email, known=None, check_fraud=True):
            return EmailResult(email=email, status=EmailStatus.TO_CHECK, usage=Usage(cost_usd=0.6))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr("sys.argv", ["run.py", "--dataset", str(dataset), "--out", str(tmp_path / "out"),
                                     "--max-cost", "1"])
    monkeypatch.setattr(run_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr(run_module, "InvoiceAgent", lambda **kwargs: None)
    monkeypatch.setattr(run_module, "Pipeline", FakePipeline)
    monkeypatch.setattr(run_module, "parse_eml", lambda raw: make_email())
    run_module.main()
    partial = list((tmp_path / "out").glob("*__partiel.json"))
    assert len(partial) == 1
    assert len(json.loads(partial[0].read_text())) == 2  # 0,6 $ puis 1,2 $ >= 1 $ : arrêt après le 2e
    assert "partiel : 2/3" in next((tmp_path / "out").glob("*__partiel.md")).read_text()
