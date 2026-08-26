from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "surfaces" / "gui" / "src" / "styles.css"
HTML = Path(__file__).resolve().parents[1] / "surfaces" / "gui" / "index.html"


def test_warm_operator_tokens_in_window_styles():
    css = CSS.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")
    assert "#FAF8F3" in css
    assert "#5B8C2A" in css
    assert "#2B2722" in css
    assert "#FFFFFF" not in css
    assert "General Sans" in css
    assert "Geist Mono" in css or "geist mono" in html.lower()
    assert "general-sans" in html.lower()


def test_session_rail_width():
    css = CSS.read_text(encoding="utf-8")
    assert ".app-rail {" in css
    assert "232px" in css
    assert "#FAF8F3" in css
    assert "#5B8C2A" in css


def test_warm_operator_shell():
    css = CSS.read_text(encoding="utf-8")
    assert "--canvas: #FAF8F3" in css
    assert "--accent: #5B8C2A" in css
    assert ".app-shell {" in css
    assert ".app-rail {" in css and "232px" in css
    assert ".connector-strip" not in css
    assert "#FFFFFF" not in css
    assert "Inter" not in css
