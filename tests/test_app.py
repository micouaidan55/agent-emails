from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent_emails.models import Category, EmailResult, EmailStatus, Triage
from agent_emails.storage.db import connect
from agent_emails.storage.repository import EmailStore
from tests.factories import make_email

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
ID = "<devis-1@exemple.fr>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "emails.db"
    monkeypatch.setenv("AGENT_EMAILS_DB", str(db))
    monkeypatch.setenv("AGENT_EMAILS_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("AGENT_EMAILS_DRAFTS", str(tmp_path / "drafts"))
    monkeypatch.setenv("AGENT_EMAILS_ATTACHMENTS", str(tmp_path / "pj"))
    monkeypatch.setenv("AGENT_FACTURES_DB", str(tmp_path / "factures.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    store = EmailStore(connect(str(db)))
    store.save_result(EmailResult(
        email=make_email(), status=EmailStatus.DRAFT_READY,
        triage=Triage(category="demande_devis", urgency="haute", confidence=0.95, explanation="Devis.", summary="12 suspensions"),
        extraction={"contact": {"name": "Claire Martin", "company": "Hôtel des Canuts"}, "quantity": "12"},
        draft="Bonjour Madame Martin,",
    ))
    return tmp_path, store


def run_app(page: str | None = None) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    if page:
        at.switch_page(page).run()
    assert not at.exception
    return at


def test_every_page_renders(env):
    for page in (None, "app_pages/tableau_de_bord.py", "app_pages/journal_valides.py",
                 "app_pages/journal_ignores.py", "app_pages/journal_historique.py"):
        run_app(page)


def test_inbox_lists_the_email(env):
    at = run_app()
    assert any("Demande de devis pour des suspensions" in m.value for m in at.markdown)


def test_fetch_without_api_key_shows_an_error(env):
    at = run_app()
    next(b for b in at.button if b.label == "Relever les emails").click().run()
    assert not at.exception
    assert any("Clé d'API" in e.value for e in at.error)


def test_validating_a_draft_saves_it_and_records_the_decision(env):
    tmp_path, store = env
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["selected"] = ID
    at.run()
    at.text_area(key=f"draft-{ID}").input("Bonjour Madame Martin, merci !").run()
    next(b for b in at.button if b.key == f"valider-{ID}").click().run()
    assert not at.exception
    stored = store.get(ID)
    assert stored.status is EmailStatus.VALIDATED
    assert stored.draft == "Bonjour Madame Martin, merci !"
    assert list((tmp_path / "drafts").glob("*.eml"))
    assert store.list_log()[0]["detail"]["modifie"] is True


def test_correcting_the_category_is_recorded(env):
    _, store = env
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["selected"] = ID
    at.run()
    at.selectbox(key=f"cat-{ID}").set_value(Category.COMPLAINT).run()
    assert not at.exception
    assert store.get(ID).category is Category.COMPLAINT
    assert store.stats().corrected == 1


def test_dashboard_shows_product_metrics(env):
    at = run_app("app_pages/tableau_de_bord.py")
    labels = [m.label for m in at.metric]
    for label in ("Emails traités", "Urgences en attente", "Brouillons validés sans modification",
                  "Catégories corrigées", "Coût moyen par email", "Temps gagné estimé"):
        assert label in labels
