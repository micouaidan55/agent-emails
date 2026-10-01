import json
from collections import Counter
from email import policy
from email.parser import BytesParser

from evals.generate import build_specs, generate


def test_dataset_composition_matches_the_spec():
    specs = build_specs()
    assert len(specs) == 48
    assert len({s["message_id"] for s in specs}) == 48
    assert Counter(s["category"] for s in specs) == {
        "demande_devis": 9, "reclamation": 7, "facture": 11, "rendez_vous": 6, "information": 9, "prospection": 6,
    }
    assert sum(1 for s in specs if s["fraud"]) == 6
    attachments = [a for s in specs for a in s["attachments"]]
    assert len(attachments) == 6
    assert Counter(a["expected_status"] for a in attachments) == {"ok": 5, "anomalie": 1}
    assert sum(1 for s in specs if s["lang"] == "en") == 4
    assert sum(1 for s in specs if s.get("html_only")) == 3
    assert sum(1 for s in specs if s.get("reply_to")) == 1
    assert all(s["draft_expected"] == (s["category"] != "prospection" and not s["fraud"]) for s in specs)


def test_generate_writes_readable_emails(tmp_path):
    generate(tmp_path)
    expected = json.loads((tmp_path / "expected.json").read_text())
    assert expected["today"] == "2026-09-29"
    assert "hotel-canuts.fr" in expected["known_domains"]
    assert sum(1 for item in expected["emails"] if item["fraud"]) == 6
    for item in expected["emails"]:
        message = BytesParser(policy=policy.default).parsebytes((tmp_path / item["file"]).read_bytes())
        assert message["Message-ID"] == item["message_id"]
        names = [part.get_filename() for part in message.iter_attachments()]
        assert names == [a["file"] for a in item["attachments"]]
        for part in message.iter_attachments():
            assert part.get_payload(decode=True).startswith(b"%PDF")


def test_generation_is_reproducible(tmp_path):
    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"
    generate(dir1)
    generate(dir2)
    for item in json.loads((dir1 / "expected.json").read_text())["emails"]:
        file1 = (dir1 / item["file"]).read_bytes()
        file2 = (dir2 / item["file"]).read_bytes()
        assert file1 == file2, f"Files {item['file']} differ between runs"
