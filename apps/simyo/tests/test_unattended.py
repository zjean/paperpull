"""--unattended parks instead of asking a question nobody hears.

The flag-combination guard in main() runs before App(args) is constructed -
it needs no config, no sentinel, and no browser - so these tests exercise it
directly through main()'s return code and the message a person actually
sees. See simyo_docs.main() and its "--unattended works with ..." refusal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simyo_docs

REFUSAL = "--unattended works with --discover, --resume or --verify only."
ADOPT_REFUSAL = "--adopt-identity is never done unattended."


def test_unattended_all_is_refused(capsys):
    """--all asks for a typed YES confirmation, so it may never run
    unattended - there is no one there to type it."""
    assert simyo_docs.main(["--unattended", "--all"]) == 2
    assert REFUSAL in capsys.readouterr().out


def test_unattended_discover_with_adopt_identity_is_refused(capsys):
    """--discover is otherwise allowed unattended, but --adopt-identity is
    the one moment an account's identity is taken on trust - that stays a
    human decision even alongside an allowed command."""
    assert simyo_docs.main(["--unattended", "--discover", "--adopt-identity"]) == 2
    assert ADOPT_REFUSAL in capsys.readouterr().out


def test_unattended_with_no_command_is_refused(capsys):
    """No command at all is not one of the three unattended-safe ones
    either, so this must be refused rather than falling through to
    print_help() and exiting 0."""
    assert simyo_docs.main(["--unattended"]) == 2
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
    args = simyo_docs.build_parser().parse_args(["--unattended", "--resume"])
    assert args.unattended is True
    assert args.resume is True
    assert (args.discover or args.resume or args.verify) and not args.adopt_identity
