from email import policy
from email.parser import BytesParser

from agent_emails.connectors.base import MailSource
from agent_emails.connectors.folder import FolderSource, draft_filename
from tests.factories import build_eml, make_email


def write_inbox(inbox):
    inbox.mkdir()
    (inbox / "02_b.eml").write_bytes(build_eml(message_id="<b@x.fr>", subject="B"))
    (inbox / "01_a.eml").write_bytes(build_eml(message_id="<a@x.fr>", subject="A"))
    (inbox / "03_cassé.eml").write_bytes(build_eml(message_id="<c@x.fr>", sender=None))
    (inbox / "notes.txt").write_text("ignoré")


def test_folder_source_is_a_mail_source(tmp_path):
    assert isinstance(FolderSource(tmp_path / "in", tmp_path / "out"), MailSource)


def test_fetch_new_returns_sorted_emails_and_reports_unreadable_files(tmp_path):
    write_inbox(tmp_path / "inbox")
    result = FolderSource(tmp_path / "inbox", tmp_path / "drafts").fetch_new(set())
    assert [e.subject for e in result.emails] == ["A", "B"]
    assert [name for name, _ in result.errors] == ["03_cassé.eml"]
    assert "Expéditeur" in result.errors[0][1]


def test_known_emails_are_not_fetched_again(tmp_path):
    write_inbox(tmp_path / "inbox")
    result = FolderSource(tmp_path / "inbox", tmp_path / "drafts").fetch_new({"<a@x.fr>"})
    assert [e.message_id for e in result.emails] == ["<b@x.fr>"]


def test_missing_inbox_is_empty(tmp_path):
    result = FolderSource(tmp_path / "absent", tmp_path / "drafts").fetch_new(set())
    assert result.emails == [] and result.errors == []


def test_save_draft_writes_a_reply_eml(tmp_path):
    source = FolderSource(tmp_path / "inbox", tmp_path / "drafts")
    email = make_email()
    location = source.save_draft(email, "Bonjour Claire,\nVoici notre proposition.")
    saved = BytesParser(policy=policy.default).parsebytes((tmp_path / "drafts" / draft_filename(email.message_id)).read_bytes())
    assert location.endswith(draft_filename(email.message_id))
    assert saved["To"] == "claire.martin@hotel-canuts.fr"
    assert saved["Subject"] == "Re: Demande de devis pour des suspensions"
    assert saved["In-Reply-To"] == "<devis-1@exemple.fr>"
    assert "Voici notre proposition." in saved.get_content()


def test_draft_filename_is_safe_and_stable():
    name = draft_filename("<../../etc/passwd@x>")
    assert name == draft_filename("<../../etc/passwd@x>")
    assert "/" not in name and name.endswith(".eml")
