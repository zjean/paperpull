"""A terminated run leaves no session slot held.

SIGTERM is how a run normally ends when a person changes their mind: the
panel's Stop button sends it, and so does the panel's own cleanup when the
browser tab streaming a run is closed. Nothing here caught it, so the process
died between bytecodes and the `finally` in `locks.hold` never ran - leaving
this provider's lock file on disk with nothing behind it. The panel then
refused that provider's Run button for six hours (STALE_AFTER), and reported
it as "connection lost".

This runs a real child process, because that is the only honest way to send a
real signal: an in-process test would have to call the handler by hand, which
proves nothing about whether the signal was installed or whether the stack
actually unwinds through the context manager. The child touches no browser, no
config and no provider - it holds a lock in a temporary directory and sleeps.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = APP_DIR.parents[1] / "core"

CHILD = textwrap.dedent('''
    import sys, time
    sys.path[:0] = [{app!r}, {core!r}]
    import simyo_docs
    from paperpull_core import locks

    simyo_docs._stop_on_sigterm()
    try:
        with locks.hold({lock_dir!r}, "simyo", 1, "the-child"):
            print("held", flush=True)
            time.sleep(60)
    except simyo_docs.Terminated:
        print("unwound", flush=True)
''')


def _run_child(lock_dir: Path):
    code = CHILD.format(app=str(APP_DIR), core=str(CORE_DIR),
                        lock_dir=str(lock_dir))
    env = dict(os.environ, PYTHONPATH=str(CORE_DIR), PYTHONUNBUFFERED="1")
    return subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.PIPE, text=True)


def _wait_for_line(proc, expected: str, seconds: float = 15.0) -> bool:
    """Read one line, with a bound - a hung child must fail the test, not the
    suite."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.strip() == expected:
            return True
        if line == "" and proc.poll() is not None:
            return False
    return False


def test_a_terminated_run_gives_its_session_slot_back(tmp_path):
    locks_dir = tmp_path / ".locks"
    proc = _run_child(locks_dir)
    try:
        assert _wait_for_line(proc, "held"), "the child never took the lock"
        assert list(locks_dir.glob("simyo.*.lock")), "no lock file was written"

        proc.send_signal(signal.SIGTERM)

        assert _wait_for_line(proc, "unwound"), \
            "SIGTERM did not unwind through locks.hold"
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()

    # The whole point: nothing left holding a slot nobody is using.
    assert list(locks_dir.glob("simyo.*.lock")) == []


def test_the_next_run_can_take_the_slot_immediately(tmp_path):
    """Stated the way a user meets it: press Stop, press Run again. Before
    this, the second press was refused for six hours."""
    sys.path[:0] = [str(APP_DIR), str(CORE_DIR)]
    from paperpull_core import locks

    locks_dir = tmp_path / ".locks"
    proc = _run_child(locks_dir)
    try:
        assert _wait_for_line(proc, "held")
        proc.send_signal(signal.SIGTERM)
        assert _wait_for_line(proc, "unwound")
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()

    # No ProviderBusy, and no waiting out STALE_AFTER.
    assert locks.acquire(locks_dir, "simyo", 1, "next-run").path.exists()
