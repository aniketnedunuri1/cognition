from server import cache
from server.verify import normalize, verify_quote

PAGE = ("## Platform\n\nWe are **migrating our monolith** from Java 8 to Java 17 and "
        "“reducing tech debt” across [payments](https://x.com/p) services this year.")


def doc(url="https://blog.example.com/post", text=PAGE, kind="web", highlights=None):
    return cache.upsert_doc(cache.make_doc(identity=url, kind=kind, url=url, text=text,
                                           highlights=highlights, retrieved_at=cache.now_iso()))


def test_exact_quote_passes_despite_formatting():
    d = doc()
    r = verify_quote(d["source_id"], 'migrating our monolith from Java 8 to Java 17 and "reducing tech debt"')
    assert r["verified"] and r["matched_in"] == "text"


def test_paraphrase_fails():
    d = doc()
    assert not verify_quote(d["source_id"], "moving the monolith from Java 8 to Java 17")["verified"]


def test_ellipsis_parts_must_appear_in_order():
    d = doc()
    assert verify_quote(d["source_id"], "migrating our monolith ... across payments services")["verified"]
    assert not verify_quote(d["source_id"], "across payments services ... migrating our monolith")["verified"]


def test_quote_attached_to_wrong_source_fails():
    doc()
    other = doc(url="https://news.example.com/other", text="Nothing about Java here at all.")
    assert not verify_quote(other["source_id"], "migrating our monolith from Java 8")["verified"]


def test_too_short_fragment_rejected():
    d = doc()
    assert not verify_quote(d["source_id"], "Java 17")["verified"]


def test_unknown_source_and_apollo_records():
    assert verify_quote("src_0000000000", "anything at all here")["reason"] == "unknown source_id"
    a = cache.upsert_doc(cache.make_doc(identity="apollo:org:x.com", kind="apollo_org", label="Apollo",
                                        retrieved_at=cache.now_iso()))
    assert not verify_quote(a["source_id"], "Apollo reports 1,800 employees")["verified"]


def test_fetches_full_text_when_only_highlights_cached(stub):
    d = doc(text=None, highlights=["unrelated excerpt only"])
    stub("server.exa._post", lambda path, body: {
        "results": [{"url": d["url"], "text": PAGE}], "statuses": [{"id": d["url"], "status": "success"}]})
    r = verify_quote(d["source_id"], "reducing tech debt across payments services")
    assert r["verified"] and r["matched_in"] == "text"


def test_falls_back_to_highlights_when_page_unfetchable(stub):
    d = doc(text=None, highlights=["We are migrating our monolith to Java 17"])
    stub("server.exa._post", lambda path, body: {"results": [], "statuses": [{"id": "u", "status": "error"}]})
    r = verify_quote(d["source_id"], "migrating our monolith to Java 17")
    assert r["verified"] and r["matched_in"] == "highlights"


def test_normalize():
    assert normalize("A  **bold** [link](http://x)’s — ok") == "a bold link's - ok"
