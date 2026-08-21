"""--unattended parks instead of asking a question nobody hears.

The flag-combination guard in main() runs before App(args) is constructed -
it needs no config, no sentinel, and no browser - so these tests exercise it
directly through main()'s return code and the message a person actually
sees. See youfone_docs.main() and its "--unattended works with ..." refusal.

Ported from apps/simyo/tests/test_unattended.py - the guard's shape is
provider-agnostic, so the tests are too.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import youfone_docs

REFUSAL = ("--unattended works with --discover, --resume, --verify, "
           "or --all --yes.")
ADOPT_REFUSAL = "--adopt-identity is never done unattended."


def test_unattended_all_without_yes_is_refused(capsys):
    """--all on its own asks for a typed YES, and there is nobody there to
    type it. --yes is what answers that prompt ahead of time, so it is the
    difference between a runnable schedule and a container blocked on stdin -
    see the test below."""
    assert youfone_docs.main(["--unattended", "--all"]) == 2
    assert REFUSAL in capsys.readouterr().out


def test_unattended_discover_with_adopt_identity_is_refused(capsys):
    """--discover is otherwise allowed unattended, but --adopt-identity is
    the one moment an account's identity is taken on trust - that stays a
    human decision even alongside an allowed command."""
    assert youfone_docs.main(["--unattended", "--discover", "--adopt-identity"]) == 2
    assert ADOPT_REFUSAL in capsys.readouterr().out


def test_unattended_with_no_command_is_refused(capsys):
    """No command at all is not one of the three unattended-safe ones
    either, so this must be refused rather than falling through to
    print_help() and exiting 0."""
    assert youfone_docs.main(["--unattended"]) == 2
    assert REFUSAL in capsys.readouterr().out


def test_unattended_resume_is_not_rejected_by_the_guard():
    """Negative control: the guard is specific, not a blanket rejection of
    --unattended. --resume is one of the three commands it allows, so the
    exact condition main() checks before constructing App must hold for
    this combination - proving the three refusals above are about the flag
    combination, not about --unattended itself.

    This stops short of calling main(["--unattended", "--resume"]): that
    would proceed past the guard into App(args), which needs a real config
    and a browser - out of scope for this test.
    """
    args = youfone_docs.build_parser().parse_args(["--unattended", "--resume"])
    assert args.unattended is True
    assert args.resume is True
    assert (args.discover or args.resume or args.verify
            or (args.all and args.yes)) and not args.adopt_identity


def test_unattended_all_with_yes_passes_the_guard():
    """The combination a scheduled run actually uses. It has to pass the
    guard, because it is the only unattended command that asks the provider
    what exists - --resume selects from the local discovery.json and so can
    never fetch a document nobody has seen yet. Same reason as the test
    above for stopping short of calling main(): past the guard lies App(args),
    a real config and a browser."""
    args = youfone_docs.build_parser().parse_args(
        ["--unattended", "--all", "--yes"])
    assert (args.discover or args.resume or args.verify
            or (args.all and args.yes)) and not args.adopt_identity
