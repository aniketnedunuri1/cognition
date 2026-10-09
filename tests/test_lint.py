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
    r = lint_message("email", BODY, [src], subject="Cognition <> Brex")
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


def test_spelled_out_numbers_need_sources(src):
    for text in ("Your team built a library of eleven skills for Devin.",
                 "Nubank ran a dozen migrations with Devin.",
                 "Three teams at Nubank use Devin for migrations.",
                 "Nubank saved millions with Devin on migrations."):
        assert "numbers not found" in blocks(lint_message("linkedin_note", text, [src])), text
    other = cache.upsert_doc(cache.make_doc(identity="https://brex.com/blog/skills", kind="web",
                                            url="https://brex.com/blog/skills", retrieved_at=cache.now_iso(),
                                            text="We wrote 11 skills and three teams adopted them in 2011."))["source_id"]
    assert lint_message("linkedin_note", "Saw your team wrote eleven skills; three teams adopted them.", [other])["ok"]
    assert "twelve" in blocks(lint_message("linkedin_note", "Twelve teams at Nubank adopted Devin.", [other]))


def test_one_is_not_treated_as_a_claim(src):
    assert lint_message("linkedin_note", "One more angle: Nubank used Devin on migrations.", [src])["ok"]


def test_meeting_length_is_not_a_claim(src):
    for ask in ("Worth twenty minutes next week?", "Open to a 15-minute call?", "Do you have 20 min Thursday?",
                "Would twenty minutes next week be useful?"):
        assert lint_message("linkedin_note", f"Teams at Nubank used Devin on migrations. {ask}", [src])["ok"], ask
    for claim in ("Nubank cut 40 minutes off every build with Devin.", "Could Devin save you 40 minutes per build?"):
        assert "40" in blocks(lint_message("linkedin_note", claim, [src])), claim


def test_no_em_dashes(src):
    for bad in ("Nubank uses Devin — it handles the migration.", "Saw the post – nice work.", "A―B"):
        assert "em/en dashes" in blocks(lint_message("linkedin_note", bad, [src])), bad
    assert lint_message("linkedin_note", "Nubank uses Devin; it handles the migration.", [src])["ok"]
