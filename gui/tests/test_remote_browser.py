"""The two switches that let the panel run in a container.

Natively, the panel serves localhost and launches the sign-in browser itself.
In Docker it is reached through a reverse proxy and the browser lives in
another container, so two things have to change - and neither may change
anything for a native install. That is what these tests pin down.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


class FakeRequest:
    """Just enough Request for _same_origin_only: a headers mapping."""

    def __init__(self, **headers):
        self.headers = {k.lower(): v for k, v in headers.items()}


# -- the origin guard ------------------------------------------------------


def test_localhost_origins_allowed_with_no_env(monkeypatch):
    monkeypatch.delenv("PAPERPULL_ALLOWED_HOSTS", raising=False)
    for origin in ("http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"):
        panel._same_origin_only(FakeRequest(origin=origin))


def test_no_origin_header_allowed(monkeypatch):
    """A same-origin GET may carry neither header; that must stay allowed."""
    monkeypatch.delenv("PAPERPULL_ALLOWED_HOSTS", raising=False)
    panel._same_origin_only(FakeRequest())


def test_foreign_origin_refused_by_default(monkeypatch):
    monkeypatch.delenv("PAPERPULL_ALLOWED_HOSTS", raising=False)
    with pytest.raises(HTTPException) as e:
        panel._same_origin_only(FakeRequest(origin="https://evil.example"))
    assert e.value.status_code == 403


def test_proxied_host_allowed_when_listed(monkeypatch):
    monkeypatch.setenv("PAPERPULL_ALLOWED_HOSTS", "paperpull.example.com")
    panel._same_origin_only(FakeRequest(origin="https://paperpull.example.com"))


def test_allowlist_is_case_insensitive_and_trims(monkeypatch):
    monkeypatch.setenv("PAPERPULL_ALLOWED_HOSTS", "  PaperPull.Example.COM , other.test ")
    panel._same_origin_only(FakeRequest(referer="https://paperpull.example.com/"))
    panel._same_origin_only(FakeRequest(referer="https://other.test/"))


def test_allowlist_does_not_admit_unlisted_hosts(monkeypatch):
    """Adding one proxy host must not open the door to every host."""
    monkeypatch.setenv("PAPERPULL_ALLOWED_HOSTS", "paperpull.example.com")
    with pytest.raises(HTTPException):
        panel._same_origin_only(FakeRequest(origin="https://evil.example"))


def test_allowlist_does_not_match_a_suffix(monkeypatch):
    """`evil-paperpull.example.com.attacker.net` is not `paperpull.example.com`."""
    monkeypatch.setenv("PAPERPULL_ALLOWED_HOSTS", "paperpull.example.com")
    with pytest.raises(HTTPException):
        panel._same_origin_only(
            FakeRequest(origin="https://paperpull.example.com.attacker.net"))


def test_referer_is_checked_too(monkeypatch):
    monkeypatch.delenv("PAPERPULL_ALLOWED_HOSTS", raising=False)
    with pytest.raises(HTTPException):
        panel._same_origin_only(FakeRequest(referer="https://evil.example/x"))


# -- the login action -----------------------------------------------------


@pytest.fixture()
def script_supporting_open_browser(tmp_path):
    """An entry script that offers --open-browser, as every app's does."""
    p = tmp_path / "ally_docs.py"
    p.write_text('ap.add_argument("--open-browser", action="store_true")\n',
                 encoding="utf-8")
    return p


def test_native_login_prefers_open_browser(monkeypatch, script_supporting_open_browser):
    """Unchanged behaviour: natively the panel opens the sign-in window."""
    monkeypatch.delenv("PAPERPULL_REMOTE_BROWSER", raising=False)
    assert panel._login_flag(script_supporting_open_browser) == "--open-browser"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_remote_login_checks_the_connection_instead(
        monkeypatch, script_supporting_open_browser, value):
    """With the browser in another container there is nothing to launch, so
    Login means "am I attached and signed in?" - which is what --login does."""
    monkeypatch.setenv("PAPERPULL_REMOTE_BROWSER", value)
    assert panel._login_flag(script_supporting_open_browser) == "--login"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_falsey_values_leave_native_behaviour(
        monkeypatch, script_supporting_open_browser, value):
    monkeypatch.setenv("PAPERPULL_REMOTE_BROWSER", value)
    assert panel._login_flag(script_supporting_open_browser) == "--open-browser"


def test_script_without_open_browser_still_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPERPULL_REMOTE_BROWSER", raising=False)
    p = tmp_path / "x_docs.py"
    p.write_text("nothing here\n", encoding="utf-8")
    assert panel._login_flag(p) == "--login"


# -- what the page is told ------------------------------------------------


def test_api_apps_reports_no_remote_browser_by_default(monkeypatch):
    monkeypatch.delenv("PAPERPULL_REMOTE_BROWSER", raising=False)
    monkeypatch.delenv("PAPERPULL_BROWSER_URL", raising=False)
    payload = panel.api_apps()
    assert payload["remote_browser"] is False
    assert payload["browser_url"] == ""


def test_api_apps_passes_the_desktop_link_through(monkeypatch):
    monkeypatch.setenv("PAPERPULL_REMOTE_BROWSER", "1")
    monkeypatch.setenv("PAPERPULL_BROWSER_URL", "https://browser.example.com/")
    payload = panel.api_apps()
    assert payload["remote_browser"] is True
    assert payload["browser_url"] == "https://browser.example.com/"


def test_remote_browser_hides_the_missing_venv_warning(monkeypatch):
    """In the image every app runs on the one interpreter, so there is no
    per-app .venv and the panel must not warn about its absence."""
    monkeypatch.setenv("PAPERPULL_REMOTE_BROWSER", "1")
    assert panel.api_apps()["expect_venvs"] is False


def test_native_still_expects_venvs(monkeypatch):
    monkeypatch.delenv("PAPERPULL_REMOTE_BROWSER", raising=False)
    assert panel.api_apps()["expect_venvs"] is True


# -- configs kept outside the app directory --------------------------------
#
# Natively an app reads config.json from its own folder. In the image that
# folder is inside the image, so the real files live on a volume and the panel
# passes an absolute --config instead. That keeps the app directories
# read-only, which is what lets the container run as any uid rather than the
# one baked into the image.


@pytest.fixture()
def fake_app(tmp_path):
    app_dir = tmp_path / "apps" / "ally"
    app_dir.mkdir(parents=True)
    (app_dir / "ally_docs.py").write_text("x\n", encoding="utf-8")
    (app_dir / "config.example.json").write_text("{}", encoding="utf-8")
    return app_dir


def test_accounts_read_from_the_app_dir_natively(monkeypatch, fake_app):
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    (fake_app / "config.spouse.json").write_text("{}", encoding="utf-8")
    assert panel._accounts(fake_app) == ["primary", "spouse"]


def test_accounts_read_from_the_config_root_when_set(monkeypatch, fake_app, tmp_path):
    root = tmp_path / "config"
    (root / "ally").mkdir(parents=True)
    (root / "ally" / "config.json").write_text("{}", encoding="utf-8")
    (root / "ally" / "config.spouse.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    # A stray file in the app dir must not be picked up: the volume is the
    # source of truth once a config root is set.
    (fake_app / "config.stale.json").write_text("{}", encoding="utf-8")
    assert panel._accounts(fake_app) == ["primary", "spouse"]


def test_a_new_account_file_is_seen_without_a_restart(monkeypatch, fake_app, tmp_path):
    root = tmp_path / "config"
    (root / "ally").mkdir(parents=True)
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    assert panel._accounts(fake_app) == ["primary"]
    (root / "ally" / "config.spouse.json").write_text("{}", encoding="utf-8")
    assert panel._accounts(fake_app) == ["primary", "spouse"]


def _meta(app_dir, accounts):
    return {"name": app_dir.name, "dir": str(app_dir), "script": "ally_docs.py",
            "python": "python", "login_flag": "--login", "accounts": accounts,
            "has_venv": False}


def test_native_primary_passes_no_config_flag(monkeypatch, fake_app):
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    cmd = panel._build_cmd(_meta(fake_app, ["primary"]), "primary", "pilot")
    assert "--config" not in cmd


def test_native_named_account_passes_a_relative_config(monkeypatch, fake_app):
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    cmd = panel._build_cmd(_meta(fake_app, ["primary", "spouse"]), "spouse", "pilot")
    assert cmd[-2:] == ["--config", "config.spouse.json"]


def test_config_root_passes_an_absolute_path_even_for_primary(monkeypatch, fake_app, tmp_path):
    root = tmp_path / "config"
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    cmd = panel._build_cmd(_meta(fake_app, ["primary"]), "primary", "pilot")
    assert cmd[-2] == "--config"
    assert cmd[-1] == str(root / "ally" / "config.json")


def test_config_root_names_the_account_file(monkeypatch, fake_app, tmp_path):
    root = tmp_path / "config"
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    cmd = panel._build_cmd(_meta(fake_app, ["primary", "spouse"]), "spouse", "pilot")
    assert cmd[-1] == str(root / "ally" / "config.spouse.json")


def test_an_unknown_account_is_still_refused(monkeypatch, fake_app, tmp_path):
    """The account name reaches a command line, so it stays allowlisted."""
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(tmp_path / "config"))
    with pytest.raises(HTTPException):
        panel._build_cmd(_meta(fake_app, ["primary"]), "../../etc/passwd", "pilot")
