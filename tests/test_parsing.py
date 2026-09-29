import pytest

from agent_emails.parsing import EmailParseError, html_to_text, parse_eml
from tests.factories import build_eml


def test_plain_text_email():
    email = parse_eml(build_eml())
    assert email.message_id == "<abc@exemple.fr>"
    assert (email.sender_name, email.sender_address) == ("Marie Dubois", "marie@exemple.fr")
    assert email.subject == "Demande de devis"
    assert email.body.startswith("Bonjour, pouvez-vous")
    assert email.received_at.utcoffset() is not None
    assert email.attachments == []


def test_html_only_email_is_converted_to_text():
    html = "<html><style>p{color:red}</style><p>Bonjour,</p><p>Je veux <b>2 lampadaires</b>.<br>Merci&nbsp;!</p><script>x()</script></html>"
    email = parse_eml(build_eml(body=None, html=html))
    assert "<" not in email.body
    assert "color" not in email.body and "x()" not in email.body
    assert "Je veux 2 lampadaires." in email.body
    assert "Merci" in email.body


def test_plain_part_is_preferred_over_html():
    email = parse_eml(build_eml(body="Version texte", html="<p>Version HTML</p>"))
    assert email.body.strip() == "Version texte"


def test_attachments_are_kept_with_their_bytes():
    email = parse_eml(build_eml(attachments=(("F-2026-190.pdf", "application/pdf", b"%PDF-1.4 x"),)))
    assert [(a.filename, a.content_type) for a in email.attachments] == [("F-2026-190.pdf", "application/pdf")]
    assert email.attachments[0].data == b"%PDF-1.4 x"


def test_encoded_subject_and_accents():
    email = parse_eml(build_eml(subject="Réclamation — commande n°42", body="Ça ne marche pas, j'ai été déçue."))
    assert email.subject == "Réclamation — commande n°42"
    assert "déçue" in email.body


def test_missing_message_id_gets_a_stable_fallback():
    raw = build_eml(message_id=None)
    assert parse_eml(raw).message_id == parse_eml(raw).message_id
    assert parse_eml(raw).message_id.startswith("sha1:")


def test_missing_sender_is_an_error():
    with pytest.raises(EmailParseError, match="Expéditeur"):
        parse_eml(build_eml(sender=None))


def test_html_to_text_collapses_blank_lines():
    assert html_to_text("<div>A</div><div></div><div></div><p>B</p>") == "A\n\nB"
