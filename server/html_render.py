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
import re

_INLINE = [
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"),
     r'<a href="\2" target="_blank" rel="noopener">\1</a>'),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*([^*]+)\*"), r"<em>\1</em>"),
]


def _inline(text: str) -> str:
    out = html.escape(text)
    for pat, rep in _INLINE:
        out = pat.sub(rep, out)
    return out


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def md_to_html(md: str) -> str:
    """Convert the render_markdown subset to HTML. Sections split on ## headers."""
    out: list[str] = []
    in_section = False
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
                slug = _slug(title)
                out.append(f'<section class="sec sec-{slug}" data-section="{html.escape(title)}">')
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
ul ul { margin:2px 0; }
blockquote { margin:6px 0 6px 2px; padding:6px 12px; border-left:3px solid #d1d5db;
             color:#374151; background:#fafafa; border-radius:0 6px 6px 0; font-size:14px; }
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:14px; }
td { border-bottom:1px solid var(--line); padding:6px 10px 6px 0; vertical-align:top; }
td:first-child { color:var(--mut); white-space:nowrap; width:1%; }
code { background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:13px; }
a { color:#2563eb; }
/* similarity: one accent hue per section category */
.sec-verdict, .sec-since-last-run { border-left-color:#16a34a; }
.sec-intent-signals, .sec-additional-signals, .sec-competitive-context,
.sec-company-linkedin { border-left-color:#d97706; }
.sec-who-to-work, .sec-recommended-entry-top-3, .sec-wider-buying-center,
.sec-org-chart-sumble---inferred { border-left-color:#2563eb; }
.sec-drafted-messages, .sec-abm-themes-assessment { border-left-color:#7c3aed; }
/* figure/ground: reference material recedes */
details.refs { margin:20px 0; }
details.refs summary { cursor:pointer; color:var(--mut); font-size:13px; }
details.refs section { opacity:.75; font-size:13px; }
/* annotations */
mark.pending { background:#fde68a; border-radius:2px; padding:0 1px; }
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
#submitAll { width:100%; margin-top:12px; }
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
let pendingRange = null;

function sectionOf(node) {
  const el = node.nodeType === 3 ? node.parentElement : node;
  const sec = el && el.closest('section.sec');
  return sec ? sec.dataset.section : '';
}

document.addEventListener('mouseup', (e) => {
  if (pop.contains(e.target)) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !report.contains(sel.anchorNode)) { pop.style.display='none'; return; }
  pendingRange = sel.getRangeAt(0).cloneRange();
  pop.style.display='block';
  pop.style.left = Math.min(e.pageX, window.innerWidth - 300) + 'px';
  pop.style.top = (e.pageY + 8) + 'px';
  popText.value=''; popText.focus();
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
    rm.onclick = () => { queue.splice(i,1); if (c.mark) c.mark.outerHTML = c.mark.innerHTML; renderPanel(); };
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

document.getElementById('popAdd').onclick = () => {
  if (!pendingRange) return;
  const quote = pendingRange.toString().trim();
  if (!quote) { pop.style.display='none'; return; }
  const c = { section: sectionOf(pendingRange.startContainer), quote,
              note: popText.value.trim(), tag: popTag.value };
  try {  // wrap the selection so the rep sees what's covered; ranges across
         // elements can throw — the comment is still stored either way
    const mark = document.createElement('mark');
    mark.className='pending';
    pendingRange.surroundContents(mark);
    c.mark = mark;
  } catch (_) {}
  queue.push(c);
  window.getSelection().removeAllRanges();
  pop.style.display='none';
  renderPanel();
};
document.getElementById('popCancel').onclick = () => { pop.style.display='none'; };

submitBtn.onclick = async () => {
  submitBtn.disabled = true;
  status.className=''; status.textContent='Submitting…';
  const comments = queue.map(({section, quote, note, tag}) => ({section, quote, note, tag}));
  try {
    const r = await fetch(location.pathname.replace(/\\/$/,'') + '/comments', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({comments})
    });
    if (!r.ok) throw new Error(await r.text());
    queue.forEach(c => { if (c.mark) c.mark.className='done'; });
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


def render_html(rec: dict, markdown: str, docx_href: str) -> str:
    """Full annotatable report page. Comments POST to <served path>/comments;
    docx_href adapts to served vs. local-file use."""
    b = rec["brief"]
    body = _reflow(md_to_html(markdown))
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
  </select>
  <div class="row"><button id="popCancel">Cancel</button>
  <button id="popAdd" class="primary">Add comment</button></div>
</div>
<aside id="panel">
  <h2>Your feedback</h2>
  <div class="hint">Queued comments. Nothing is saved until you submit.</div>
  <div id="list"></div>
  <button id="submitAll" class="primary">Submit all feedback (0)</button>
  <div id="status"></div>
</aside>
<script>{_JS}</script>
</body></html>
"""
