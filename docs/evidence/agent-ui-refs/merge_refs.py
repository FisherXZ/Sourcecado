"""Merge per-product fragments and manifests into refs/fragment.html and refs/manifest.json, then assemble."""
import json, pathlib, subprocess
D = pathlib.Path(__file__).resolve().parent
R = D / "refs"
order = ["claude", "clay", "attio"]
parts, manifest = [], []
for p in order:
    f = R / f"fragment-{p}.html"
    if f.exists():
        parts.append(f'<div class="ev-product" id="refs-{p}">\n{f.read_text()}\n</div>')
    m = R / f"manifest-{p}.json"
    if m.exists():
        try:
            data = json.loads(m.read_text())
            manifest.extend(data if isinstance(data, list) else data.get("images", []) + data.get("gaps", []))
        except json.JSONDecodeError as e:
            print("bad manifest", m.name, e)
(R / "fragment.html").write_text("\n<hr>\n".join(parts) if parts else '<div class="empty">Reference research running.</div>')
(R / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("merged", [p for p in order if (R / f"fragment-{p}.html").exists()], "manifest entries:", len(manifest))
subprocess.run(["python3", str(D / "assemble.py")], check=True)
