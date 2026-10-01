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
