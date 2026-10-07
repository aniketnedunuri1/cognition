import json

import pytest

from server import cache
from server.lint import lint_message

SRC = "Nubank saw an 8x engineering time efficiency gain and 20x cost savings migrating 3,000 jobs."
BODY = ("Saw your team is moving the core ETL monolith to sub-modules. Teams at Nubank used Devin for exactly "
        "that kind of migration and report an 8x engineering time efficiency gain. Devin takes the repetitive "
        "refactors in parallel, opens PRs your engineers review, and keeps the migration moving while the team "
        "ships product. Would it be worth twenty minutes to see how Nubank ran it and whether the same playbook "
        "fits your sub-module plan? Happy to share their rollout notes either way, and the session logs.")


@pytest.fixture
def src(tmp_path, monkeypatch):
    kd = tmp_path / "k"; kd.mkdir()
    (kd / "customers.json").write_text(json.dumps({"customers": [
        {"name": "Nubank", "public_reference": True, "proofs": []},
        {"name": "Odyssey Logistics", "public_reference": False, "proofs": []}]}))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(kd))
    return cache.upsert_doc(cache.make_doc(identity="https://devin.ai/customers/nubank", kind="web",
                                           url="https://devin.ai/customers/nubank", text=SRC,
                                           retrieved_at=cache.now_iso()))["source_id"]


def test_clean_email_passes(src):
    r = lint_message("email", BODY, [src], subject="Your ETL sub-module migration")
    assert r["ok"], r["issues"]


def blocks(r):
    return " ".join(i["issue"] for i in r["issues"] if i["severity"] == "block")


def test_unsourced_numbers_block(src):
    r = lint_message("linkedin_note", "Teams at Nubank cut migration time 12x with Devin.", [src])
    assert "12x" in blocks(r)
    r = lint_message("linkedin_note", "Teams at Nubank saw an 8x gain with Devin.", [])
    assert "8x" in blocks(r)


def test_never_rules_block(src):
    assert "Windsurf" in blocks(lint_message("linkedin_note", "Devin and Windsurf help teams ship.", [src]))
    assert "layoffs" in blocks(lint_message("linkedin_note", "After the recent layoffs, Devin can help.", [src]))
    assert "not a public reference" in blocks(lint_message("linkedin_note", "Odyssey Logistics uses Devin.", [src]))
    assert "placeholder" in blocks(lint_message("linkedin_note", "Hi [First Name], Devin helps.", [src]))
    assert "banned" in blocks(lint_message("linkedin_note", "Quick question about your ETL work.", [src]))


def test_length_is_fix_not_block(src):
    r = lint_message("linkedin_followup", "Too short.", [src])
    assert not r["ok"] and r["issues"][0]["severity"] == "fix"
    r = lint_message("email", BODY, [src], subject="Hi")
    assert any("subject" in i["issue"] for i in r["issues"])


def test_number_units_need_word_boundary(src):
    assert "17" in blocks(lint_message("linkedin_note", "Saw the Java 17 migration at Nubank.", [src]))
    from server.lint import NUMBER
    assert NUMBER.findall("Java 17 migration, 8x faster, $10M, 40%") == ["17", "8x", "$10M", "40%"]
