from server.html_render import _fold_paren_urls, _stat_url

URLS = {"li": "https://www.linkedin.com/in/adam", "itau": "https://devin.ai/customers/itau",
        "ramp": "https://devin.ai/customers/ramp/"}
TEXT = ("Your tooling cut CI/CD cost by about 30% each.\n\n"
        "Itaú reports a 6x faster migration (https://devin.ai/customers/itau).")
CLAIMS = [{"text": "your tooling cut CI/CD cost by about 30% each", "source_id": "li"}]


def test_stat_prefers_claim_over_other_source_with_same_number():
    # both docs contain "30%"; the claim says the stat is from LinkedIn
    blobs = {"itau": "... 30% fewer incidents ...", "li": "... cost by about 30% each"}
    assert _stat_url("30%", TEXT, CLAIMS, blobs, URLS.get) == URLS["li"]


def test_stat_uses_url_cited_in_its_sentence():
    assert _stat_url("6x", TEXT, [], {"li": "6x"}, URLS.get) == URLS["itau"]


def test_ambiguous_stat_gets_no_link():
    blobs = {"itau": "30%", "li": "30%"}
    assert _stat_url("30%", "Cost fell 30%.", [], blobs, URLS.get) is None
    assert _stat_url("30%", "Cost fell 30%.", [], {"li": "30%"}, URLS.get) == URLS["li"]


def link(url, text, cls=""):
    return f'<a {cls}href="{url}" target="_blank" rel="noopener">{text}</a>'


def test_paren_url_dropped_when_stat_already_links_it():
    u = URLS["itau"]
    block = f'<p>Itaú reports a {link(u, "6x", "class=\"srclink\" ")} faster migration ({link(u, u)}).</p>'
    out = _fold_paren_urls(block)
    assert "(" not in out and out.count(u) == 1


def test_paren_url_folds_onto_claim_without_stat():
    u = URLS["ramp"]
    out = _fold_paren_urls(f"<p>Plate. Ramp says Devin fixes debt ({link(u, u)}).</p>")
    assert out == (f'<p>Plate. <a class="srclink" href="{u}" target="_blank" '
                   f'rel="noopener">Ramp says Devin fixes debt</a>.</p>')
