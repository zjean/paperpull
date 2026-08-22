"""Adding an account: the panel's only write.

Setting an account up meant hand-copying `config.example.json` and editing it,
and until you had, the panel listed that account as one more thing wrong with
your install — seventeen of eighteen rows on a fresh checkout, most of which
nobody will ever use. So the register hides them and `POST /api/accounts`
turns one into a real account.

Two things make this worth testing carefully rather than lightly.

It **writes to the filesystem**, in a process whose whole security posture is
"the only thing that reaches a subprocess is a command built from `ACTIONS`".
Nothing here reaches a shell, but the app name and the account label both
reach a path, so both are checked against something rather than sanitised into
something.

And **what it writes decides whether two accounts corrupt each other.** Two
accounts of one provider sharing an `output_dir` share `progress.json` and
`sentinel.json`, so each would keep overwriting the other's idea of what it had
downloaded and whether it was signed in. That rule is not invented here — it
is the one every app's own `add_account.py` applies and the READMEs promise
("its own profile, port and output folders, so no data mixes").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


EXAMPLE = {
    "owner": "",
    "output_dir": ".",
    "profile_dir": "./browser-profile",
    "cdp_url": "http://localhost:9235",
    "document_types": ["Statement"],
    "pilot_count": 5,
}


@pytest.fixture()
def apps_root(tmp_path, monkeypatch):
    """One discoverable app shipping an example config, and nothing else."""
    root = tmp_path / "apps"
    app_dir = root / "acme"
    app_dir.mkdir(parents=True)
    (app_dir / "acme_docs.py").write_text(
        "# --pilot --discover --all --resume --verify --yes --open-browser\n",
        encoding="utf-8")
    (app_dir / "config.example.json").write_text(
        json.dumps(EXAMPLE), encoding="utf-8")
    monkeypatch.setattr(panel, "APPS_ROOT", root)
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    return app_dir


class _Body:
    """The smallest thing api_add_account's `request.json()` needs."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _add(payload):
    return anyio.run(panel.api_add_account, _Body(payload))


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# -- the happy paths --------------------------------------------------------


def test_a_provider_you_have_never_set_up_gets_the_shipped_example(apps_root):
    """`primary` from the tracked example is the setup this project documents
    everywhere, so nothing is moved out of anything's way - the file is the
    example, verbatim."""
    out = _add({"app": "acme", "account": "primary"})
    written = _read(out["config"])
    assert Path(out["config"]) == apps_root / "config.json"
    assert written == EXAMPLE


def test_the_new_account_appears_in_the_register_immediately(apps_root):
    """Accounts are read per request, so there is no restart and no cache to
    invalidate - the point of the button is that the account is simply there."""
    before = {(a["app"], a["account"]) for a in panel.api_state()["accounts"]}
    _add({"app": "acme", "account": "primary"})
    after = panel.api_state()["accounts"]
    assert ("acme", "primary") not in {(a["app"], a["account"]) for a in after
                                       if a["needs"] == "setup"}
    row = next(a for a in after if (a["app"], a["account"]) == ("acme", "primary"))
    assert row["needs"] != "setup"
    assert before  # the fixture's app was discoverable before the write too


def test_a_second_account_is_based_on_the_first_not_on_the_example(apps_root):
    """A second person's account of a provider you already use should differ
    from yours only in the ways it has to - so the template is your config,
    including anything you changed in it."""
    _add({"app": "acme", "account": "primary"})
    primary = apps_root / "config.json"
    cfg = _read(primary)
    cfg["output_dir"] = "/archive/acme"
    cfg["document_types"] = ["Statement", "Tax Document"]
    primary.write_text(json.dumps(cfg), encoding="utf-8")

    out = _add({"app": "acme", "account": "spouse"})
    written = _read(out["config"])
    assert written["document_types"] == ["Statement", "Tax Document"]


def test_a_second_account_gets_its_own_output_folder(apps_root):
    """The one that matters: a shared output_dir means a shared progress.json
    and a shared sentinel.json, so the two accounts would overwrite each
    other's record of what had been downloaded and whether they were signed
    in."""
    _add({"app": "acme", "account": "primary"})
    primary = apps_root / "config.json"
    cfg = _read(primary)
    cfg["output_dir"] = "/archive/acme"
    primary.write_text(json.dumps(cfg), encoding="utf-8")

    written = _read(_add({"app": "acme", "account": "spouse"})["config"])
    assert written["output_dir"] != "/archive/acme"
    assert "spouse" in written["output_dir"]


def test_a_second_account_gets_its_own_browser_profile(apps_root):
    """A shared profile_dir is a shared signed-in session, which is the same
    corruption one step earlier: both accounts would be whoever signed in
    last."""
    _add({"app": "acme", "account": "primary"})
    written = _read(_add({"app": "acme", "account": "spouse"})["config"])
    assert written["profile_dir"] != EXAMPLE["profile_dir"]
    assert written["profile_dir"].startswith(written["output_dir"])


def test_a_second_account_gets_its_own_debugging_port_natively(apps_root):
    """Natively every account has its own browser on its own port - that is
    upstream's design and add_account.py's rule."""
    _add({"app": "acme", "account": "primary"})
    written = _read(_add({"app": "acme", "account": "spouse"})["config"])
    assert written["cdp_url"] != EXAMPLE["cdp_url"]
    assert ":924" in written["cdp_url"]      # 9235 + one step of 10


def test_two_extra_accounts_do_not_land_on_one_port(apps_root):
    """The step is per existing account, not a constant, or the second and
    third accounts would both attach to the same browser."""
    _add({"app": "acme", "account": "primary"})
    one = _read(_add({"app": "acme", "account": "spouse"})["config"])
    two = _read(_add({"app": "acme", "account": "child"})["config"])
    assert one["cdp_url"] != two["cdp_url"]


def test_the_port_is_left_alone_when_one_browser_serves_everything(
        apps_root, monkeypatch, tmp_path):
    """In the container there is exactly one browser and every app's cdp_url
    points at it on purpose (docs/docker.md, "One shared browser"). Stepping
    the port there would aim the new account at nothing at all - a second
    person needs a second browser service, which is a compose-file decision
    this endpoint cannot make."""
    root = tmp_path / "config"
    (root / "acme").mkdir(parents=True)
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    _add({"app": "acme", "account": "primary"})
    out = _add({"app": "acme", "account": "spouse"})
    written = _read(out["config"])
    assert written["cdp_url"] == EXAMPLE["cdp_url"]
    # ...but the output folder still has to be its own.
    assert "spouse" in written["output_dir"]
    assert out["shared_browser"] is True


def test_the_config_root_layout_is_written_to_the_config_root(
        apps_root, monkeypatch, tmp_path):
    """In the image the app directories are part of the image and read-only;
    the config lives on a volume."""
    root = tmp_path / "config"
    (root / "acme").mkdir(parents=True)
    monkeypatch.setenv("PAPERPULL_CONFIG_ROOT", str(root))
    out = _add({"app": "acme", "account": "primary"})
    assert Path(out["config"]) == root / "acme" / "config.json"


def test_the_reported_output_dir_is_resolved_not_a_bare_dot(apps_root):
    """Every shipped example says "output_dir": ".", which is meaningful to
    the downloader (launched with its own directory as the working directory)
    and meaningless on screen - the page reported that a new account would
    file its documents "into .". """
    out = _add({"app": "acme", "account": "primary"})
    assert Path(out["output_dir"]).is_absolute()
    assert out["output_dir"] != "."


def test_the_label_becomes_the_account_holder_when_nothing_else_has(apps_root):
    """The panel cannot ask for the holder's name - it hands a run a pipe, and
    an app only asks on a real console - so the CSV's Account Holder column
    stays blank forever otherwise. The label is a better guess than blank."""
    _add({"app": "acme", "account": "primary"})
    written = _read(_add({"app": "acme", "account": "jan-wiebe"})["config"])
    assert written["owner"] == "Jan Wiebe"


def test_an_owner_you_already_set_is_not_overwritten(apps_root):
    _add({"app": "acme", "account": "primary"})
    primary = apps_root / "config.json"
    cfg = _read(primary)
    cfg["owner"] = "The Household"
    primary.write_text(json.dumps(cfg), encoding="utf-8")
    written = _read(_add({"app": "acme", "account": "spouse"})["config"])
    assert written["owner"] == "The Household"


# -- what it refuses --------------------------------------------------------


@pytest.mark.parametrize("app_name", [
    "../../etc", "..", "/etc", "acme/../../etc", "nosuchapp", "",
])
def test_only_a_discovered_app_can_be_written_to(apps_root, app_name):
    """The app name reaches a path, so it is checked against the apps actually
    discovered rather than pattern-matched: "../.." is not a discovered app,
    and neither is anything else that is not there."""
    with pytest.raises(HTTPException) as e:
        _add({"app": app_name, "account": "primary"})
    assert e.value.status_code == 404


@pytest.mark.parametrize("label", [
    "../escape", "with/slash", "with\\\\backslash", "..", ".", "",
    "Capital", "space here", "x" * 33, "-leading", "sym!bol",
])
def test_a_label_that_would_not_be_a_safe_filename_is_refused(apps_root, label):
    """It becomes `config.<label>.json` and a directory suffix. Restricted
    rather than sanitised: silently rewriting what someone typed produces an
    account under a name they did not choose and cannot guess. `Capital` is in
    here on purpose - it used to be lowercased and accepted, which is that
    same silent rename, and which makes one account on a case-insensitive
    filesystem and two on Linux."""
    with pytest.raises(HTTPException) as e:
        _add({"app": "acme", "account": label})
    assert e.value.status_code == 400


def test_the_example_label_is_reserved(apps_root):
    """`config.example.json` is the template, not an account - _account_names
    skips it, so an account by that name would be created and then never
    listed anywhere."""
    with pytest.raises(HTTPException) as e:
        _add({"app": "acme", "account": "example"})
    assert e.value.status_code == 400


def test_an_existing_account_is_never_overwritten(apps_root):
    """A config holds an output_dir pointing at documents already downloaded
    and a profile_dir holding a signed-in session. Overwriting one from a
    template is not an edit, it is a loss."""
    _add({"app": "acme", "account": "primary"})
    with pytest.raises(HTTPException) as e:
        _add({"app": "acme", "account": "primary"})
    assert e.value.status_code == 409


def test_an_app_with_no_template_says_so_rather_than_writing_nothing(
        apps_root):
    """Some apps ship no config.example.json. Refusing with a sentence beats
    creating an empty config that every later run fails on."""
    (apps_root / "config.example.json").unlink()
    with pytest.raises(HTTPException) as e:
        _add({"app": "acme", "account": "primary"})
    assert e.value.status_code == 400
    assert "config.example.json" in e.value.detail


def test_a_malformed_template_is_reported_not_propagated(apps_root):
    (apps_root / "config.example.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        _add({"app": "acme", "account": "primary"})
    assert e.value.status_code == 400


@pytest.mark.parametrize("payload", [
    [], "string", {"app": 1, "account": "primary"},
    {"app": "acme", "account": 1},
])
def test_a_malformed_body_is_a_400_not_a_500(apps_root, payload):
    with pytest.raises(HTTPException) as e:
        _add(payload)
    assert e.value.status_code == 400


def test_the_route_carries_the_origin_guard():
    """It writes to disk on a POST, which is exactly what a cross-site request
    would like to do."""
    guarded = {getattr(r, "path", ""): any(
                   d.dependency is panel._same_origin_only
                   for d in getattr(r, "dependencies", []))
               for r in panel.app.routes}
    assert guarded["/api/accounts"] is True


def test_it_is_the_only_route_that_writes():
    """Stated as a test because it is the property the rest of the panel's
    security argument rests on. If a second writing route is added, this is
    the line that should make someone think about it."""
    posts = sorted(getattr(r, "path", "") for r in panel.app.routes
                   if "POST" in (getattr(r, "methods", None) or set()))
    assert posts == ["/api/accounts", "/api/answer", "/api/stop"]
