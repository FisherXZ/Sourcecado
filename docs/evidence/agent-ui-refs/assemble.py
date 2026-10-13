"""Rebuild index.html from status.json, refs/fragment.html and <ticket>/fragment.html.
Idempotent; safe to run any time, from anywhere. Lives inside the board folder."""
import json, html, pathlib, datetime
D = pathlib.Path(__file__).resolve().parent
S = json.loads((D / "status.json").read_text())
T = S.get("tickets", {})
COL = {"waiting": "rgba(255,248,235,.45)", "running": "#E8A230", "review": "#E8A230", "changes": "#D4614E",
       "pr": "#5CB77A", "blocked-human": "#D4614E", "done": "#5CB77A"}
LABEL = {"waiting": "Waiting", "running": "Building", "review": "In review", "changes": "Changes requested",
         "pr": "PR open, reviewed", "blocked-human": "Needs you", "done": "Merged"}
order = sorted(T, key=int) if all(k.isdigit() for k in T) else list(T)

# dependency graph: one column per depth
depth = {}
def d(k):
    if k in depth: return depth[k]
    b = T[k].get("blocked", []); depth[k] = 0 if not b else 1 + max(d(str(x)) for x in b); return depth[k]
for k in T: d(k)
cols = {}
for k in order: cols.setdefault(depth[k], []).append(k)
graph_panel = ""
if T:
    W, H, BW, BH = 150, 64, 132, 44
    pos = {k: (20 + c * W, 20 + i * H) for c, ks in cols.items() for i, k in enumerate(ks)}
    gh = 40 + max(len(v) for v in cols.values()) * H
    gw = 40 + len(cols) * W
    edges = "".join(
        f'<path d="M{pos[str(b)][0]+BW},{pos[str(b)][1]+BH/2} C{pos[str(b)][0]+BW+20},{pos[str(b)][1]+BH/2} {pos[k][0]-20},{pos[k][1]+BH/2} {pos[k][0]},{pos[k][1]+BH/2}" class="e"/>'
        for k in T for b in T[k].get("blocked", []))
    nodes = "".join(
        f'<a href="#t{k}"><g class="n"><rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="8" style="stroke:{COL[T[k]["state"]]}"/>'
        f'<circle cx="{x+12}" cy="{y+14}" r="4" fill="{COL[T[k]["state"]]}"/>'
        f'<text x="{x+22}" y="{y+18}" class="k">#{html.escape(k)} · {html.escape(T[k].get("type", ""))}</text>'
        f'<text x="{x+10}" y="{y+34}" class="s">{html.escape(T[k]["title"][:22])}</text></g></a>'
        for k, (x, y) in pos.items())
    graph_panel = (f'<div class="panel"><div class="over" style="margin-bottom:8px">Dependency graph · color = state</div>'
                   f'<svg viewBox="0 0 {gw} {gh}" class="graph">{edges}{nodes}</svg></div>')

counts = {}
for v in T.values(): counts[v["state"]] = counts.get(v["state"], 0) + 1
tiles = "".join(f'<div class="tile"><span class="dot" style="background:{COL[s]}"></span>{LABEL[s]} <b>{n}</b></div>' for s, n in counts.items())
log = "".join(f'<li><span class="lt">{html.escape(e["t"])}</span>{e["m"]}</li>' for e in reversed(S.get("log", [])))
top = f'<div class="top{"" if T else " solo"}">{graph_panel}<div class="panel"><div class="over">Orchestrator log</div><ul class="log">{log}</ul></div></div>' if (T or log) else ""

issue_url = S.get("issue_url")  # e.g. "https://github.com/OWNER/REPO/issues/{id}"
secs = []
for k in order:
    v = T[k]; f = D / k / "fragment.html"
    body = f.read_text() if f.exists() else '<div class="empty">No evidence yet. The agent working this ticket writes it here.</div>'
    pr = f'<a class="pill prl" href="{v["pr"]}">PR</a>' if v.get("pr") else ""
    num = f'<a class="num" href="{issue_url.format(id=k)}">#{html.escape(k)}</a>' if issue_url else f'<span class="num">#{html.escape(k)}</span>'
    typ = f'<span class="pill">{html.escape(v["type"])}</span>' if v.get("type") else ""
    secs.append(f'<section id="t{k}"><div class="sh"><span class="dot" style="background:{COL[v["state"]]}"></span>'
                f'{num}<h2>{html.escape(v["title"])}</h2>{typ}<span class="pill">{LABEL[v["state"]]}</span>{pr}</div>'
                f'<div class="fx">{body}</div></section>')

rf = D / "refs" / "fragment.html"
refs = ""
if rf.exists() or S.get("refs_title"):
    rbody = rf.read_text() if rf.exists() else '<div class="empty">Reference research running.</div>'
    refs = (f'<section id="refs"><div class="sh"><span class="dot" style="background:#E8A230"></span>'
            f'<h2>{html.escape(S.get("refs_title", "Reference board"))}</h2></div><div class="fx">{rbody}</div></section>')

tpl = (D / "template.html").read_text()
out = (tpl.replace("{{TITLE}}", html.escape(S.get("title", "Build evidence")))
          .replace("{{INTRO}}", S.get("intro", ""))
          .replace("{{TILES}}", tiles).replace("{{TOP}}", top)
          .replace("{{REFS}}", refs).replace("{{SECTIONS}}", "\n".join(secs))
          .replace("{{STAMP}}", datetime.datetime.now().strftime("%a %d %b %H:%M")))
(D / "index.html").write_text(out)
print("assembled", len(secs), "ticket sections", "+ refs" if refs else "")
