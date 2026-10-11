"""HTML brief render: the annotatable report.

Reuses render_markdown's output so the report always matches the .md/.docx. The
markdown subset it emits (headers, tables, lists, blockquotes, bold/italic/links)
is converted to HTML here — no markdown dependency.

Visual encoding (Gestalt): each ## section is a card (common region), accent color
marks the section's category (similarity), Sources and the validator line recede
inside a collapsed <details> (figure/ground), tight spacing inside blocks, larger
between sections (proximity).

Annotation: the rep selects text, leaves a comment, queues as many as they like,
and submits once. POST /report/<token>/comments stores them for the agent to
distill into playbook rules. Pending comments highlight yellow, submitted green.
"""

from __future__ import annotations

import html
import json
import re

_INLINE = [
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"),
     r'<a href="\2" target="_blank" rel="noopener">\1</a>'),
    # bare URLs in draft bodies become clickable links
    (re.compile(r"(?<![\"'=>/])(https?://[^\s<)]+)"),
     r'<a href="\1" target="_blank" rel="noopener">\1</a>'),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*([^*]+)\*"), r"<em>\1</em>"),
    # citation markers [n] jump to the Sources list entry
    (re.compile(r"(?<![\w/])\[(\d{1,2})\]"), r'<a class="cite" href="#src-\1">[\1]</a>'),
]

# Inline badges: the brief's own status vocabulary gets one visual treatment each.
_BADGES = [
    (re.compile(r"\b(Tier \d|Strong signal)\b"), r'<span class="bdg tier">\1</span>'),
    (re.compile(r"\b(Champion|Incumbent owner)\b"), r'<span class="bdg champ">\1</span>'),
    (re.compile(r"\b(Economic buyer|Technical evaluator|Gatekeeper|Parent-company approver)\b"),
     lambda m: f'<span class="bdg role {_slug(m[1])}">{m[1]}</span>'),
    # strip the render's parens around status words — the badge replaces them
    (re.compile(r"\(\s*(verified)\s*\)"), r'<span class="bdg ver">\1</span>'),
    (re.compile(r"\(\s*(single source)\s*\)"), r'<span class="bdg warn">\1</span>'),
    (re.compile(r"\b(may be outdated|Low-confidence draft)\b"),
     r'<span class="bdg warn">\1</span>'),
]


def _inline(text: str) -> str:
    out = html.escape(text)
    for pat, rep in _INLINE + _BADGES:
        out = pat.sub(rep, out)
    return out


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def md_to_html(md: str) -> str:
    """Convert the render_markdown subset to HTML. Sections split on ## headers."""
    out: list[str] = []
    in_section = False
    sec = ""
    uls = 0
    in_table = False

    def close_blocks() -> None:
        nonlocal uls, in_table
        while uls:
            out.append("</ul>")
            uls -= 1
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if m := re.match(r"^(#{1,3}) (.*)", line):
            close_blocks()
            level, title = len(m[1]), m[2].strip()
            if level == 2:
                if in_section:
                    out.append("</section>")
                sec = _slug(title)
                out.append(f'<section class="sec sec-{sec}" data-section="{html.escape(title)}">')
                in_section = True
            out.append(f"<h{level}>{_inline(title)}</h{level}>")
        elif line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            if not in_table:
                out.append("<table><tbody>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        elif m := re.match(r"^(\s*)- (.*)", line):
            if in_table:
                out.append("</tbody></table>")
                in_table = False
            level = len(m[1]) // 2 + 1
            while uls < level:
                out.append("<ul>")
                uls += 1
            while uls > level:
                out.append("</ul>")
                uls -= 1
            out.append(f"<li>{_inline(m[2])}</li>")
        elif line.startswith("> "):
            close_blocks()
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif not line:
            close_blocks()
        elif m := re.match(r"^\*(.*)\*$", line.strip()):
            close_blocks()
            out.append(f'<p class="meta"><em>{_inline(m[1])}</em></p>')
        else:
            close_blocks()
            # Sources entries are "N. [title](url) — ...": anchor them for [n] cites
            if sec == "sources" and (n := re.match(r"^(\d{1,2})\. ", line)):
                out.append(f'<p id="src-{n[1]}">{_inline(line)}</p>')
            else:
                out.append(f"<p>{_inline(line)}</p>")

    close_blocks()
    if in_section:
        out.append("</section>")
    return "\n".join(out)


_CSS = """
:root { --ink:#1a1d21; --mut:#6b7280; --line:#e5e7eb; --bg:#f7f7f5; --card:#fff; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       color:var(--ink); background:var(--bg); }
.page { max-width:880px; margin:0 auto; padding:32px 20px 120px; }
header.top { display:flex; flex-wrap:wrap; gap:12px; align-items:baseline;
             justify-content:space-between; margin-bottom:24px; }
header.top h1 { font-size:22px; margin:0; }
header.top .sub { color:var(--mut); font-size:13px; }
header.top .dl { font-size:13px; color:#2563eb; text-decoration:none; }
/* common region: one card per section */
section.sec { background:var(--card); border:1px solid var(--line); border-radius:10px;
              padding:18px 22px; margin:20px 0; border-left:4px solid #d1d5db; }
section.sec > h2 { margin:0 0 10px; font-size:15px; text-transform:uppercase;
                   letter-spacing:.04em; color:var(--mut); }
h3 { font-size:14px; margin:18px 0 6px; }
p { margin:6px 0; }
p.meta { color:var(--mut); font-size:13px; }
ul { margin:6px 0; padding-left:22px; }
li { margin:5px 0; }
section.sec > ul > li + li { margin-top:12px; }  /* proximity: room between entities */
ul ul { margin:2px 0; }
blockquote { margin:6px 0 6px 2px; padding:6px 12px; border-left:3px solid #d1d5db;
             color:#374151; background:#fafafa; border-radius:0 6px 6px 0; font-size:14px; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:14px; }
td { border-bottom:1px solid var(--line); padding:6px 10px 6px 0; vertical-align:top; }
td:first-child { color:var(--mut); white-space:nowrap; width:1%; }
code { background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:13px; }
a { color:#2563eb; }
a.cite { color:var(--mut); text-decoration:none; font-size:12px; vertical-align:1px; }
a.cite:hover { color:#2563eb; text-decoration:underline; }
a.srclink { color:#2563eb; border-bottom:1px dotted #2563eb; text-decoration:none; }
details.refs p:target { background:#fef3c7; border-radius:4px; }
/* similarity: one accent hue per section category — chip + left border + faint card tint */
section.sec > h2 { display:inline-block; padding:2px 10px; border-radius:6px; }
.sec-verdict, .sec-since-last-run { border-left-color:#16a34a; background:#fbfefc; }
.sec-verdict > h2, .sec-since-last-run > h2 { color:#15803d; background:#dcfce7; }
.sec-intent-signals, .sec-additional-signals, .sec-competitive-context,
.sec-company-linkedin { border-left-color:#d97706; background:#fffdf9; }
.sec-intent-signals > h2, .sec-additional-signals > h2,
.sec-competitive-context > h2, .sec-company-linkedin > h2 { color:#b45309; background:#fef3c7; }
.sec-who-to-work, .sec-recommended-entry-top-3, .sec-wider-buying-center,
.sec-org-chart-sumble---inferred { border-left-color:#2563eb; background:#fcfdff; }
.sec-who-to-work > h2, .sec-recommended-entry-top-3 > h2,
.sec-wider-buying-center > h2, .sec-org-chart-sumble---inferred > h2 {
  color:#1d4ed8; background:#dbeafe; }
.sec-drafted-messages, .sec-abm-themes-assessment { border-left-color:#7c3aed; background:#fefdff; }
.sec-drafted-messages > h2, .sec-abm-themes-assessment > h2 { color:#6d21a8; background:#f3e8ff; }
/* small inline badges */
.bdg { display:inline-block; font-size:11px; font-weight:600; border-radius:4px;
       padding:0 6px; vertical-align:1px; }
.bdg.tier { color:#b45309; background:#fef3c7; }
.bdg.champ { color:#1d4ed8; background:#dbeafe; }
.bdg.warn { color:#b91c1c; background:#fee2e2; }
.bdg.ver { color:#15803d; background:#dcfce7; }
/* one hue per deal role (similarity) */
.bdg.role.economic-buyer { color:#15803d; background:#dcfce7; }
.bdg.role.technical-evaluator { color:#1d4ed8; background:#dbeafe; }
.bdg.role.gatekeeper { color:#b45309; background:#fef3c7; }
.bdg.role.parent-company-approver { color:#6d21a8; background:#f3e8ff; }
/* nested lines (evidence, hooks) recede: figure/ground */
ul ul { font-size:13px; color:#4b5563; }
ul ul .bdg { background:none; box-shadow:inset 0 0 0 1px #c7cbd1; color:#6b7280;
             font-weight:500; }
ul ul li { margin:3px 0; }
em .bdg { font-style:normal; }
/* figure/ground: reference material recedes */
details.refs { margin:20px 0; }
details.refs summary { cursor:pointer; color:var(--mut); font-size:13px; }
details.refs section { opacity:.75; font-size:13px; }
/* annotations */
::selection { background:#fde68a; }
mark.draft { background:#fef9c3; border-radius:2px; padding:0 1px;
             outline:1px dashed #eab308; }
mark.pending { background:#fde68a; border-radius:2px; padding:0 1px;
               box-shadow:0 0 0 1px #f59e0b inset; }
mark.done { background:#bbf7d0; border-radius:2px; padding:0 1px; }
#pop { position:absolute; z-index:50; display:none; width:280px; background:#fff;
       border:1px solid var(--line); border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,.12);
       padding:10px; }
#pop textarea { width:100%; height:64px; font:inherit; font-size:13px; resize:vertical;
                border:1px solid var(--line); border-radius:6px; padding:6px; }
#pop select { width:100%; margin:6px 0; font:inherit; font-size:13px; }
#pop .row { display:flex; gap:8px; justify-content:flex-end; }
button { font:inherit; font-size:13px; border:1px solid var(--line); border-radius:6px;
         background:#fff; padding:5px 10px; cursor:pointer; }
button.primary { background:#1a1d21; color:#fff; border-color:#1a1d21; }
#panel { position:fixed; top:0; right:0; bottom:0; width:300px; background:#fff;
         border-left:1px solid var(--line); padding:16px; overflow-y:auto; display:none; }
#panel h2 { font-size:14px; margin:0 0 4px; }
#panel .hint { color:var(--mut); font-size:12px; margin-bottom:12px; }
.comment { border:1px solid var(--line); border-radius:8px; padding:10px; margin:8px 0;
           font-size:13px; }
.comment q { display:block; color:var(--mut); font-size:12px; margin-bottom:4px;
             border-left:2px solid #fde68a; padding-left:6px; }
.comment .sec-tag, .comment .tag-tag { display:inline-block; font-size:11px; color:var(--mut);
             background:#f3f4f6; border-radius:4px; padding:1px 6px; margin-right:4px; }
.comment .rm { float:right; border:none; color:#b91c1c; padding:0; }
/* copyable draft blocks */
.cp { position:relative; border:1px dashed var(--line); border-radius:8px;
      padding:10px 14px; margin:10px 0; }
.cpbtn { position:absolute; top:8px; right:8px; font-size:11px; color:var(--mut);
         background:#fff; }
.cpbtn:hover { color:#1a1d21; border-color:#9ca3af; }
.cpbtn.ok { color:#15803d; border-color:#86efac; background:#f0fdf4; font-weight:600; }
.cp .cplabel { margin-top:0; }
#repName { width:100%; margin-top:12px; box-sizing:border-box; }
#submitAll { width:100%; margin-top:8px; }
#status { font-size:12px; margin-top:8px; color:var(--mut); }
#status.err { color:#b91c1c; }
body.has-panel .page { margin-right:320px; }
#fab { position:fixed; right:16px; bottom:16px; box-shadow:0 4px 14px rgba(0,0,0,.15); }
"""

_JS = """
const queue = [];
const report = document.getElementById('report');
const pop = document.getElementById('pop');
const popText = document.getElementById('popText');
const popTag = document.getElementById('popTag');
const panel = document.getElementById('panel');
const list = document.getElementById('list');
const status = document.getElementById('status');
const submitBtn = document.getElementById('submitAll');
const repName = document.getElementById('repName');
let pendingRange = null;
let pendingQuote = '';
let pendingSection = '';
let draftMarks = [];

function clearDraft() {
  draftMarks.forEach(m => { m.outerHTML = m.innerHTML; });
  draftMarks = [];
}

function hidePop() {
  pop.style.display='none';
  clearDraft();
  pendingRange = null;
  pendingQuote = '';
}

function sectionOf(node) {
  const el = node.nodeType === 3 ? node.parentElement : node;
  const sec = el && el.closest('section.sec');
  return sec ? sec.dataset.section : '';
}

document.addEventListener('mouseup', (e) => {
  if (pop.contains(e.target)) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !report.contains(sel.anchorNode)) { hidePop(); return; }
  clearDraft();
  pendingRange = sel.getRangeAt(0).cloneRange();
  // capture quote + section now: highlightRange mutates/splits the text nodes the
  // range points at, so reading them later (at Add) comes back empty
  pendingQuote = pendingRange.toString().trim();
  pendingSection = sectionOf(pendingRange.startContainer);
  draftMarks = highlightRange(pendingRange, 'draft');  // visible while the popover is open
  pop.style.display='block';
  pop.style.left = Math.min(e.pageX, window.innerWidth - 300) + 'px';
  pop.style.top = (e.pageY + 8) + 'px';
  popText.value='';   // no autofocus: it would collapse the selection and break Cmd+C
});

function renderPanel() {
  panel.style.display = queue.length ? 'block' : 'none';
  document.body.classList.toggle('has-panel', !!queue.length);
  list.innerHTML='';
  queue.forEach((c, i) => {
    const d = document.createElement('div');
    d.className='comment';
    const rm = document.createElement('button');
    rm.className='rm'; rm.textContent='✕';
    rm.onclick = () => { queue.splice(i,1);
      (c.marks||[]).forEach(m => { m.outerHTML = m.innerHTML; });
      renderPanel(); };
    const q = document.createElement('q'); q.textContent = c.quote;
    const meta = document.createElement('div');
    meta.innerHTML = `<span class="sec-tag">${c.section||'—'}</span>` +
      (c.tag ? `<span class="tag-tag">${c.tag}</span>` : '');
    const n = document.createElement('div'); n.textContent = c.note;
    d.append(rm, q, meta, n);
    list.appendChild(d);
  });
  submitBtn.textContent = `Submit all feedback (${queue.length})`;
}

function highlightRange(range, cls) {
  // wrap every text node the range touches — surroundContents alone fails on
  // selections that span elements, which is most of them
  const marks = [];
  const wrap = (r) => {
    const m = document.createElement('mark');
    m.className = cls;
    try { r.surroundContents(m); marks.push(m); } catch (_) {}
  };
  const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      if (!n.nodeValue.trim() || !range.intersectsNode(n)) return NodeFilter.FILTER_SKIP;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  if (!nodes.length) {
    try {  // selection inside one text node
      const r = range.cloneRange();
      wrap(r);
    } catch (_) {}
    return marks;
  }
  nodes.forEach((n) => {
    const r = document.createRange();
    r.selectNodeContents(n);
    if (n === range.startContainer) r.setStart(n, range.startOffset);
    if (n === range.endContainer) r.setEnd(n, range.endOffset);
    wrap(r);
  });
  return marks;
}

document.getElementById('popAdd').onclick = () => {
  if (!pendingRange) return;
  if (!pendingQuote) { pop.style.display='none'; return; }
  const c = { section: pendingSection, quote: pendingQuote,
              note: popText.value.trim(), tag: popTag.value };
  c.marks = draftMarks;               // promote the visible draft highlight
  draftMarks = [];
  c.marks.forEach(m => m.className = 'pending');
  queue.push(c);
  window.getSelection().removeAllRanges();
  pop.style.display='none';
  pendingRange = null;
  pendingQuote = '';
  renderPanel();
};
document.getElementById('popCancel').onclick = hidePop;

// citation links open the collapsed Sources block before jumping
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.cite');
  if (a) document.querySelectorAll('details.refs').forEach(d => d.open = true);
});

// drafted messages: group each labeled block (Email / LinkedIn note / follow-up)
// into a .cp wrapper with a copy button, so reps can paste straight into email.
document.querySelectorAll('.sec-drafted-messages').forEach(sec => {
  const labels = { 'Email': 'Copy email',
                   'LinkedIn connection note': 'Copy note',
                   'LinkedIn follow-up': 'Copy follow-up' };
  let group = null;
  [...sec.children].forEach(el => {
    if (el.matches('h3')) { group = null; return; }
    // draft labels render as <p class="meta"><em><em>Label</em>… — the nested
    // em marks a block boundary; anything else belongs to the open group
    const inner = el.tagName === 'P' && el.querySelector('em em');
    if (inner) {
      const label = labels[inner.textContent.trim()];
      group = null;   // unknown labels (e.g. rep-only "Send notes") end the group
      if (label) {
        group = document.createElement('div');
        group.className = 'cp';
        sec.insertBefore(group, el);
        const btn = document.createElement('button');
        btn.className = 'cpbtn'; btn.type = 'button';
        btn.dataset.l = label; btn.textContent = label;
        const g = group;               // capture now — `group` is reused per label
        btn.onclick = () => copyGroup(g, btn);
        group.appendChild(btn);
        el.classList.add('cplabel');
      }
    }
    if (group) group.appendChild(el);
  });
});

async function copyGroup(g, btn) {
  const c = g.cloneNode(true);
  c.querySelectorAll('.cpbtn,.cplabel').forEach(n => n.remove());
  // drop rep-only trailers that can trail into the last group of a draft
  c.querySelectorAll('p > em:first-child').forEach(em => {
    if (/^(Built from|Lint)\b/.test(em.textContent)) em.parentElement.remove();
  });
  // innerText needs layout: park the clone offscreen so paragraph breaks survive
  c.style.cssText = 'position:fixed;left:-9999px;top:0';
  document.body.appendChild(c);
  const text = c.innerText.trim(), markup = c.innerHTML;
  c.remove();
  let copied = false;
  try {
    await navigator.clipboard.write([new ClipboardItem({
      'text/plain': new Blob([text], {type:'text/plain'}),
      'text/html': new Blob([markup], {type:'text/html'})
    })]);
    copied = true;
  } catch (_) {}
  if (!copied) {
    // fallback that needs no clipboard permission: hide the chrome, select the
    // block, and let the browser's own copy command carry the rich HTML
    const hidden = [];
    g.querySelectorAll('.cpbtn,.cplabel').forEach(n => {
      hidden.push([n, n.style.display]); n.style.display = 'none';
    });
    const r = document.createRange();
    r.selectNodeContents(g);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(r);
    try { copied = document.execCommand('copy'); } catch (_) {}
    sel.removeAllRanges();
    hidden.forEach(([n, d]) => { n.style.display = d; });
  }
  btn.classList.add('ok');
  btn.textContent = '✓ Copied';
  setTimeout(() => { btn.classList.remove('ok'); btn.textContent = btn.dataset.l; }, 1600);
}

// Cmd/Ctrl+C with no selection inside a draft block copies that whole block;
// with a selection the browser's native copy already carries rich HTML.
document.addEventListener('copy', (e) => {
  const sel = window.getSelection();
  if (!sel || !sel.isCollapsed || !report.contains(sel.anchorNode)) return;
  const el = sel.anchorNode && sel.anchorNode.nodeType === 3
    ? sel.anchorNode.parentElement : sel.anchorNode;
  const g = el && el.closest('.cp');
  if (!g) return;
  const c = g.cloneNode(true);
  c.querySelectorAll('.cpbtn,.cplabel').forEach(n => n.remove());
  c.querySelectorAll('p > em:first-child').forEach(em => {
    if (/^(Built from|Lint)\b/.test(em.textContent)) em.parentElement.remove();
  });
  c.style.cssText = 'position:fixed;left:-9999px;top:0';
  document.body.appendChild(c);
  e.clipboardData.setData('text/plain', c.innerText.trim());
  e.clipboardData.setData('text/html', c.innerHTML);
  c.remove();
  e.preventDefault();
  const b = g.querySelector('.cpbtn');   // same confirmation as the button
  if (b) { b.classList.add('ok'); b.textContent = '✓ Copied';
    setTimeout(() => { b.classList.remove('ok'); b.textContent = b.dataset.l; }, 1600); }
});

submitBtn.onclick = async () => {
  submitBtn.disabled = true;
  status.className=''; status.textContent='Submitting…';
  const comments = queue.map(({section, quote, note, tag}) => ({section, quote, note, tag}));
  try {
    const r = await fetch(location.pathname.replace(/\\/$/,'') + '/comments', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({comments, rep: repName && repName.value.trim() || null})
    });
    if (!r.ok) throw new Error(await r.text());
    queue.forEach(c => { (c.marks||[]).forEach(m => m.className='done'); });
    queue.length = 0;
    renderPanel();
    status.textContent='Saved — thank you. The next brief run will pick these up.';
  } catch (e) {
    status.className='err';
    status.textContent='Could not save — your comments are still queued. Click Submit to retry.';
  } finally {
    submitBtn.disabled = false;
  }
};
"""


def _reflow(html_body: str) -> str:
    """Sources + validator recede into a collapsed details block (figure/ground)."""
    marker = '<section class="sec sec-sources"'
    head, sep, tail = html_body.partition(marker)
    if not sep:
        return html_body
    return head + '<details class="refs"><summary>Sources &amp; validator report</summary>' + \
        sep + tail + "</details>"


_STAT = re.compile(r"~?\$?\d[\d,]*(?:\.\d+)?(?:%|x|k|m|b|bn)(?![\w%])", re.I)
_URL = re.compile(r"https?://[^\s)<]+")


def _stat_url(tok: str, text: str, claims: list[dict], blobs: dict, url_of) -> str | None:
    """Source URL for a stat token in a draft, most explicit evidence first.
    Several sources can contain the same number, so an ambiguous match gets no
    link — a wrong source is worse than none."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if tok in sentence and (u := _URL.search(sentence)):
            return u[0].rstrip(".,")
    for c in claims:
        if tok in (c.get("text") or "") + (c.get("quote") or "") and (u := url_of(c.get("source_id"))):
            return u
    urls = {url_of(sid) for sid, blob in blobs.items() if tok in blob} - {None}
    return urls.pop() if len(urls) == 1 else None


def _link_draft_stats(body_html: str, rec: dict) -> str:
    """Hyperlink each stat token in a draft to the cited source whose document
    contains it (claims and built_from cover proof metrics too). Report-only —
    the .docx/.md stay plain."""
    from .briefs import _url
    from .cache import get_doc

    sec = body_html.find("sec-drafted-messages")
    if sec < 0:
        return body_html
    end = body_html.find("</section>", sec)
    seg, tail = body_html[sec:end], body_html[end:]
    drafts = {d["name"]: d for d in rec["brief"].get("drafts") or []}
    parts = re.split(r"(<h3>.*?</h3>)", seg)
    for i in range(2, len(parts), 2):
        name = re.sub(r"<[^>]+>", "", parts[i - 1]).strip()
        d = drafts.get(name)
        if not d:
            continue
        block = parts[i]
        claims = d.get("claims") or []
        # ordered, not a set: several sources can contain the same stat token
        sids = dict.fromkeys(e.get("source_id") for e in claims + (d.get("built_from") or []))
        docs = {sid: json.dumps(get_doc(sid) or {}) for sid in sids if sid}
        text = "\n".join([d["email"]["body"], d.get("linkedin_note") or "",
                          d.get("linkedin_followup") or ""])
        for tok in dict.fromkeys(_STAT.findall(text)):
            url = _stat_url(tok, text, claims, docs, _url)
            if not url:
                continue
            m = re.search(rf"(?<![\w>\"'/]){re.escape(tok)}(?![\w<])", block)
            if m:
                block = (block[:m.start()] +
                         f'<a class="srclink" href="{html.escape(url)}" '
                         f'target="_blank" rel="noopener">{tok}</a>' + block[m.end():])
        parts[i] = block
    return body_html[:sec] + "".join(map(_fold_paren_urls, parts)) + tail


_PAREN_URL = re.compile(r'([^<>.!?\s][^<>.!?]*?)\s*\(<a href="([^"]+)"[^>]*>\2</a>\)')


def _fold_paren_urls(block: str) -> str:
    """Drafts cite proof as "claim (url)". Drop the raw url when a stat already
    links to it; otherwise move the link onto the claim clause itself."""
    def fold(m: re.Match) -> str:
        if f'class="srclink" href="{m[2]}"' in block:
            return m[1]
        return f'<a class="srclink" href="{m[2]}" target="_blank" rel="noopener">{m[1]}</a>'
    return _PAREN_URL.sub(fold, block)


def render_html(rec: dict, markdown: str, docx_href: str) -> str:
    """Full annotatable report page. Comments POST to <served path>/comments;
    docx_href adapts to served vs. local-file use."""
    b = rec["brief"]
    # the page header covers the md's title + date preamble; skip them
    body = _link_draft_stats(_reflow(md_to_html(re.sub(r"\A# .*\n\*.*\*\n\n?", "", markdown))), rec)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(b['company_name'])} — account brief</title>
<style>{_CSS}</style>
</head><body>
<div class="page">
<header class="top">
  <div>
    <h1>{html.escape(b['company_name'])} — account brief</h1>
    <div class="sub">{rec['date']} · {html.escape(b['domain'])} ·
      select any text to comment, then Submit all feedback</div>
  </div>
  <a class="dl" href="{html.escape(docx_href)}">Download .docx</a>
</header>
<main id="report">
{body}
</main>
</div>
<div id="pop">
  <textarea id="popText" placeholder="Comment on this selection…"></textarea>
  <select id="popTag">
    <option value="">Just this brief</option>
    <option value="applies always">Applies always</option>
    <option value="keep this">Keep this (positive)</option>
  </select>
  <div class="row"><button id="popCancel">Cancel</button>
  <button id="popAdd" class="primary">Add comment</button></div>
</div>
<aside id="panel">
  <h2>Your feedback</h2>
  <div class="hint">Queued comments. Nothing is saved until you submit.</div>
  <div id="list"></div>
  <input id="repName" placeholder="Your handle (optional — defaults to the requester)">
  <button id="submitAll" class="primary">Submit all feedback (0)</button>
  <div id="status"></div>
</aside>
<script>{_JS}</script>
</body></html>
"""
