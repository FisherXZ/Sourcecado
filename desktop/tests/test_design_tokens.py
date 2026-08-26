from pathlib import Path

GUI_SRC = Path(__file__).resolve().parents[1] / "surfaces" / "gui" / "src"
HTML = Path(__file__).resolve().parents[1] / "surfaces" / "gui" / "index.html"


def read_css() -> str:
    """Every stylesheet the app ships, concatenated.

    styles.css used to hold the whole sheet. It was split into styles/*.css
    (tokens, shell, chat, route, responsive) and now mostly @imports them, so
    reading it alone made these assertions pass vacuously. Reading all of them
    keeps each assertion meaning what it always meant -- and keeps the negative
    assertions honest, since a banned value must be absent from every file, not
    just from whichever one it used to live in.
    """
    sheets = [GUI_SRC / "styles.css", *sorted((GUI_SRC / "styles").glob("*.css"))]
    return "\n".join(p.read_text(encoding="utf-8") for p in sheets)


def test_warm_operator_tokens_in_window_styles():
    css = read_css()
    html = HTML.read_text(encoding="utf-8")
    assert "#FAF8F3" in css
    assert "#5B8C2A" in css
    assert "#2B2722" in css
    assert "#FFFFFF" not in css
    assert "General Sans" in css
    assert "Geist Mono" in css or "geist mono" in html.lower()
    assert "general-sans" in html.lower()


def test_session_rail_width():
    css = read_css()
    assert ".app-rail {" in css
    assert "232px" in css
    assert "#FAF8F3" in css
    assert "#5B8C2A" in css


def test_warm_operator_shell():
    css = read_css()
    assert "--canvas: #FAF8F3" in css
    assert "--accent: #5B8C2A" in css
    assert ".app-shell {" in css
    assert ".app-rail {" in css and "232px" in css
    assert ".connector-strip" not in css
    assert "#FFFFFF" not in css
    assert "Inter" not in css


def test_split_stylesheets_are_all_imported():
    """The split only holds if styles.css actually pulls in every part."""
    entry = (GUI_SRC / "styles.css").read_text(encoding="utf-8")
    for part in sorted((GUI_SRC / "styles").glob("*.css")):
        assert f'@import "./styles/{part.name}"' in entry, part.name
