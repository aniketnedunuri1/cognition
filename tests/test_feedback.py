import json

import pytest

from server import feedback


@pytest.fixture
def pb(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYBOOK_DIR", str(tmp_path))
    monkeypatch.setattr("server.feedback.commit", lambda d, m: True)
    return tmp_path


def test_rep_rules_are_layered(pb):
    (pb / "locked_rules.json").write_text(json.dumps([{"rule": "No auto-send", "scope": "global"}]))
    feedback.add_playbook_rule("Short emails only", "global", rep="alice")
    feedback.add_playbook_rule("Long emails are fine", "global", rep="bob")

    alice = feedback.get_playbook("alice")
    assert [r["rule"] for r in alice["rep_rules"]] == ["Short emails only"]
    assert alice["locked_rules"] == [{"rule": "No auto-send", "scope": "global"}]
    assert alice["learned_rules"] == []
    # Bob's rule never leaks into alice's playbook, and neither hits team defaults
    assert feedback.get_playbook()["learned_rules"] == []
    assert [r["rule"] for r in feedback.get_playbook("bob")["rep_rules"]] == ["Long emails are fine"]
    assert (pb / "reps" / "alice.json").exists()


def test_rep_replaces_own_rule_only(pb):
    feedback.add_playbook_rule("Old rule", "global")
    r = feedback.add_playbook_rule("New rule", "global", rep="alice", replaces="Old rule")
    assert not r["ok"] and "alice's own rules" in r["reason"]  # a rep can't edit team defaults
    feedback.add_playbook_rule("Old rule", "global", rep="alice")
    r = feedback.add_playbook_rule("New rule", "global", rep="alice", replaces="Old rule")
    assert r["ok"] and r["replaced"] == "Old rule"
    assert [x["rule"] for x in feedback.get_playbook("alice")["rep_rules"]] == ["New rule"]


def test_locked_rules_cannot_be_replaced(pb):
    (pb / "locked_rules.json").write_text(json.dumps([{"rule": "No auto-send", "scope": "global"}]))
    r = feedback.add_playbook_rule("Auto-send is fine", "global", replaces="No auto-send")
    assert not r["ok"] and "locked" in r["reason"]


def test_rules_are_never_anonymous(pb):
    r = feedback.add_playbook_rule("Lead with the peer metric", "persona:economic_buyer")
    assert not r["ok"] and "rep is required" in r["reason"]
    assert not feedback.add_golden_example("technical_evaluator", "email", "msg")["ok"]
    assert feedback.get_playbook()["learned_rules"] == []


def test_promotion_opens_pr_at_threshold(pb, monkeypatch):
    prs = []
    monkeypatch.setattr("server.feedback.open_pr",
                        lambda d, branch, msg, title, body: prs.append(branch) or f"https://pr/{branch}")
    for rep in ("alice", "bob"):
        feedback.add_playbook_rule("Vary the CTA across drafts", "global", rep=rep)
        assert not prs  # not yet: only 2 reps
    out = feedback.add_playbook_rule("Vary the CTA across drafts", "global", rep="carol")
    assert prs == ["playbook-promote-" + __import__("hashlib").sha1(b"vary the cta across drafts").hexdigest()[:10]]
    assert out["promotion"]["pr_url"].startswith("https://pr/") and out["promotion"]["reps"] == ["alice", "bob", "carol"]

    rules = feedback.get_playbook()["learned_rules"]
    assert rules[0]["sponsors"] == ["alice", "bob", "carol"] and rules[0]["evidence"] == 3
    assert "Vary the CTA" in (pb / "learned_rules.md").read_text()
    # promoted rep copies are marked and stop appearing as rep_rules
    assert feedback.get_playbook("alice")["rep_rules"] == []
    # a fourth rep adding the same rule is just evidence on the team rule
    out = feedback.add_playbook_rule("Vary the CTA across drafts", "global", rep="dana")
    assert "promotion" not in out


def test_rep_golden_examples_layer_on_top(pb):
    (pb / "golden_examples.json").write_text(json.dumps(
        {"technical_evaluator/email": [{"message": "team example"}]}))
    feedback.add_golden_example("technical_evaluator", "email", "alice example", rep="alice")
    pb_all = feedback.get_playbook("alice")["golden_examples"]["technical_evaluator/email"]
    assert [e["message"] for e in pb_all] == ["alice example", "team example"]
    assert feedback.get_playbook("bob")["golden_examples"]["technical_evaluator/email"][0]["message"] == "team example"


def test_rep_slug(pb):
    assert feedback.rep_slug("Aniket N16") == "aniketn16"
    assert feedback.rep_slug("alice@corp.com") == "alice"
    assert feedback.rep_slug(None) is None and feedback.rep_slug("***") is None
