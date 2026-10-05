from core.agents.test_execution.captcha_solve import main, solve_image
from core.tool_gateway.playwright_client import refresh_auth_fixture


def test_solve_image_prints_the_discovery_reading(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(
        "core.agents.test_execution.captcha_solve._captcha_answer_from_reading",
        lambda page_text, widget_text, image_png: "eWKB6r" if image_png == b"png" else None,
    )
    image = tmp_path / "captcha.png"
    image.write_bytes(b"png")
    assert solve_image(b"png") == "eWKB6r"
    assert main([str(image)]) == 0
    assert capsys.readouterr().out == "eWKB6r"


def test_solve_image_ignores_an_empty_file() -> None:
    assert solve_image(b"") is None


def test_refresh_replaces_the_svg_only_login(tmp_path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "auth.ts").write_text(
        'const configured = "https://shop.example";\nimg[alt="CAPTCHA"]\n',
        encoding="utf-8",
    )
    refresh_auth_fixture(tmp_path, {})
    text = (fixtures / "auth.ts").read_text(encoding="utf-8")
    assert 'img[alt="CAPTCHA"]' not in text
    assert 'const configured = "https://shop.example";' in text
    assert "captcha_solve" in text
    assert 'img[alt*="captcha" i]' in text
    assert "sign\\s*out" in text
    assert 'name: "Logout", exact: true' not in text
    assert "openSignedIn" in text
    assert "navigate.call(page, target.href, options)" in text
    assert 'tag === "SELECT"' in text
