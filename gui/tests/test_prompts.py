"""Answering a downloader's prompt from the panel.

Every app pauses and asks for a keypress in three situations: the provider
signed you out mid-run, the provider is showing a security challenge, and the
`--verify` pass wants a new summary typed in. Natively you answer those in the
console window the launcher opened. In the container there is no console, so
the panel is the only thing that can answer - and until it could, a sign-out
ended the run with "no interactive console available" and a `--verify` pass was
unusable altogether.

Two things had to be true, and neither is obvious:

1. The run needs a real stdin. It used to get DEVNULL, deliberately.
2. The prompt has to be VISIBLE. `input(prompt)` writes its prompt with no
   trailing newline, so a reader that calls readline() blocks on a line that
   never comes: the page showed a run that started and then went silent. The
   stream is therefore read as bytes, not lines.

These tests pin both down, plus the sanitising on the way in - the answer is
the one piece of user text that reaches a running downloader.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


# -- the answer itself -----------------------------------------------------
#
# An answer is a reply to exactly one prompt. A newline inside it would end
# that reply early and leave the rest sitting in the pipe, to be read as the
# answer to whatever the app asks NEXT - so "YES\nq" would confirm a full run
# and then quit the verify pass. Newlines are collapsed rather than rejected
# because the only free-text prompt is a receipt summary, where a pasted line
# break is a typo and not an attempt at anything.


def test_a_plain_answer_survives_intact():
    assert panel._answer_line("YES") == "YES"


def test_an_empty_answer_stays_empty():
    """Pressing Continue sends nothing at all - that is the common case."""
    assert panel._answer_line("") == ""


@pytest.mark.parametrize("raw", ["YES\nq", "YES\r\nq", "YES\rq"])
def test_newlines_cannot_smuggle_a_second_answer(raw):
    assert "\n" not in panel._answer_line(raw)
    assert "\r" not in panel._answer_line(raw)


def test_collapsed_newlines_keep_the_text():
    """A summary pasted over two lines still arrives as that summary."""
    assert panel._answer_line("Garden hose\nand fittings") == "Garden hose and fittings"


def test_an_over_long_answer_is_truncated():
    assert len(panel._answer_line("x" * 5000)) == panel.ANSWER_MAX_CHARS


# -- delivering it to the running app --------------------------------------


def _prompting_child(tmp_path: Path, name: str = "child.py") -> Path:
    """A stand-in for an app that has paused to ask something."""
    p = tmp_path / name
    p.write_text(
        "import sys\n"
        'print("!! Amazon appears to have signed you out.")\n'
        'ans = input("Press Enter after you are signed in again... ")\n'
        'print("resumed with %r, tty=%s" % (ans, sys.stdin.isatty()))\n',
        encoding="utf-8")
    return p


@pytest.fixture()
def paused_run(tmp_path):
    """A registered run, blocked on input(), cleaned up however the test ends."""
    proc = subprocess.Popen(
        [sys.executable, str(_prompting_child(tmp_path))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, env={"PYTHONUNBUFFERED": "1"})
    run_id = panel._register_run(proc)
    try:
        yield run_id, proc
    finally:
        panel._RUNS.pop(run_id, None)
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_the_answer_reaches_the_waiting_app(paused_run):
    run_id, proc = paused_run
    panel._send_answer(run_id, "typed by hand")
    out = proc.communicate(timeout=10)[0].decode()
    assert "resumed with 'typed by hand'" in out


def test_a_pipe_is_not_a_console(paused_run):
    """`ensure_owner` skips its question unless stdin is a tty, and that has to
    keep being true: the panel gives the run a pipe, not a terminal, so the
    first-run "Whose account is this?" prompt stays skipped and `owner` stays a
    config-file field in the container."""
    run_id, proc = paused_run
    panel._send_answer(run_id, "")
    out = proc.communicate(timeout=10)[0].decode()
    assert "tty=False" in out


def test_an_unknown_run_is_refused():
    with pytest.raises(HTTPException) as e:
        panel._send_answer("not-a-run", "")
    assert e.value.status_code == 404


def test_a_finished_run_is_refused(paused_run):
    """The id outlives the process by a moment. Writing to it must not look
    like it worked."""
    run_id, proc = paused_run
    proc.kill()
    proc.wait()
    with pytest.raises(HTTPException) as e:
        panel._send_answer(run_id, "")
    assert e.value.status_code == 404


def test_stopping_an_unknown_run_is_refused():
    with pytest.raises(HTTPException) as e:
        panel._stop_run("not-a-run")
    assert e.value.status_code == 404


def test_stop_ends_the_paused_run(paused_run):
    """A run blocked on a prompt nobody answers is now possible, so there has
    to be a way out of it that is not "close the tab"."""
    run_id, proc = paused_run
    panel._stop_run(run_id)
    assert proc.wait(timeout=10) != 0


# -- who is allowed to answer ----------------------------------------------


def test_both_new_routes_carry_the_origin_guard():
    """These two are POSTs that reach into a running downloader, so a route
    added without the guard is the one mistake that must not go unnoticed.
    Checked on the routing table rather than by request, because there is no
    HTTP client in this project's dependencies."""
    guarded = {}
    for route in panel.app.routes:
        deps = getattr(route, "dependencies", [])
        guarded[getattr(route, "path", "")] = any(
            d.dependency is panel._same_origin_only for d in deps)
    assert guarded["/api/answer"] is True
    assert guarded["/api/stop"] is True


# -- what the page receives ------------------------------------------------


def _frames(raw: str):
    """The SSE frames in a blob of stream output, as (event, data) pairs."""
    out = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data.append(line[len("data: "):])
        out.append((event, "\n".join(data)))
    return out


def _console_text(frames) -> str:
    """What the page would have appended to the console element."""
    return "".join(json.loads(d)["t"] for e, d in frames if e == "message")


@pytest.fixture()
def prompting_app(tmp_path, monkeypatch):
    """An apps root holding one app that pauses for a keypress."""
    app_dir = tmp_path / "apps" / "amazon"
    app_dir.mkdir(parents=True)
    _prompting_child(app_dir, "amazon_receipts.py")
    monkeypatch.setattr(panel, "APPS_ROOT", tmp_path / "apps")
    monkeypatch.delenv("PAPERPULL_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("PAPERPULL_REMOTE_BROWSER", raising=False)
    return app_dir


def test_the_stream_shows_a_prompt_that_has_no_newline(prompting_app):
    """The whole point. Read line by line, this prompt is invisible."""
    async def drive():
        response = panel.api_run(app="amazon", account="primary", action="pilot")
        seen, run_id = "", None
        with anyio.fail_after(20):
            async for frame in response.body_iterator:
                for event, data in _frames(frame):
                    if event == "run":
                        run_id = data
                seen += _console_text(_frames(frame))
                if seen.endswith("in again... "):
                    break
        return seen, run_id, response

    seen, run_id, response = anyio.run(drive)
    assert "!! Amazon appears to have signed you out." in seen
    # No trailing newline: this is exactly what readline() could never yield.
    assert seen.endswith("Press Enter after you are signed in again... ")
    assert run_id, "the page needs a run id before it can answer anything"
    anyio.run(response.body_iterator.aclose)


def test_answering_the_stream_lets_the_run_finish(prompting_app):
    async def drive():
        response = panel.api_run(app="amazon", account="primary", action="pilot")
        seen, run_id, code, answered = "", None, None, False
        with anyio.fail_after(30):
            async for frame in response.body_iterator:
                for event, data in _frames(frame):
                    if event == "run":
                        run_id = data
                    elif event == "done":
                        code = data
                seen += _console_text(_frames(frame))
                if not answered and seen.endswith("in again... "):
                    panel._send_answer(run_id, "signed in")
                    answered = True
        return seen, code, run_id

    seen, code, run_id = anyio.run(drive)
    assert "resumed with 'signed in'" in seen
    assert code == "0"
    assert run_id not in panel._RUNS, "a finished run must not stay answerable"
