"""The one place PaperPull makes an outbound call.

Nothing here touches the network: `send` takes an `opener`, and every test
passes a fake one. A test that reached ntfy.sh would be a test that fails
when someone runs the suite on a train.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import notify


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for urllib.request.urlopen, and remembers what it was given."""

    def __init__(self, status=200, raises=None):
        self.status = status
        self.raises = raises
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.raises is not None:
            raise self.raises
        return FakeResponse(self.status)


CONFIGURED = {"PAPERPULL_NTFY_URL": "https://ntfy.example.com/paperpull"}


def test_an_install_that_set_nothing_is_not_configured():
    assert notify.configured({}) is False
    assert notify.configured({"PAPERPULL_NTFY_URL": "   "}) is False


def test_a_url_makes_it_configured():
    assert notify.configured(CONFIGURED) is True


def test_sending_while_unconfigured_does_nothing_at_all():
    """Not an error. An install that never set the variable is not broken."""
    opener = FakeOpener()
    assert notify.send("t", "m", env={}, opener=opener) is False
    assert opener.calls == []


def test_a_send_posts_the_message_to_the_topic():
    opener = FakeOpener()
    assert notify.send("Two need you", "youfone/primary - no signed-in tab",
                       env=CONFIGURED, opener=opener) is True
    request, timeout = opener.calls[0]
    assert request.full_url == "https://ntfy.example.com/paperpull"
    assert request.get_method() == "POST"
    assert request.data == b"youfone/primary - no signed-in tab"
    assert request.get_header("Title") == "Two need you"
    assert timeout == notify.TIMEOUT_SECONDS


def test_tags_and_priority_ride_along_when_given():
    opener = FakeOpener()
    notify.send("t", "m", tags=("warning", "bell"), priority=4,
                env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Tags") == "warning,bell"
    assert request.get_header("Priority") == "4"


def test_no_tags_or_priority_means_no_such_headers():
    opener = FakeOpener()
    notify.send("t", "m", env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Tags") is None
    assert request.get_header("Priority") is None


def test_a_token_becomes_a_bearer_header():
    opener = FakeOpener()
    notify.send("t", "m", opener=opener,
                env={**CONFIGURED, "PAPERPULL_NTFY_TOKEN": "tk_secret"})
    request, _ = opener.calls[0]
    assert request.get_header("Authorization") == "Bearer tk_secret"


def test_no_token_means_no_authorization_header():
    opener = FakeOpener()
    notify.send("t", "m", env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Authorization") is None


def test_an_unreachable_server_is_reported_not_raised():
    """The whole point of this module. A missed notification must never
    turn a working document run into a failed one."""
    opener = FakeOpener(raises=OSError("no route to host"))
    assert notify.send("t", "m", env=CONFIGURED, opener=opener) is False


def test_a_refused_message_is_reported_not_raised():
    opener = FakeOpener(status=403)
    assert notify.send("t", "m", env=CONFIGURED, opener=opener) is False


def test_a_malformed_url_is_reported_not_raised():
    """A schemeless URL (e.g. missing https://) must not break the run.
    This tests the headline guarantee: misconfiguration is survivable."""
    opener = FakeOpener()
    assert notify.send("t", "m", env={"PAPERPULL_NTFY_URL": "ntfy.example.com/paperpull"},
                       opener=opener) is False
