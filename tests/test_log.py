# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_retirement.log import configure, debug, error


def test_debug_logging_is_opt_in(capsys: object) -> None:
    configure(debug_logging=False)
    debug("hidden", count=1)
    assert capsys.readouterr().out == ""

    configure(debug_logging=True)
    debug("evaluation complete", count=2)
    assert capsys.readouterr().out == ("[Card Retirement] DEBUG: evaluation complete count=2\n")
    configure(debug_logging=False)


def test_errors_are_always_logged(capsys: object) -> None:
    configure(debug_logging=False)
    error("invalid configuration", issue="bad value")
    assert capsys.readouterr().out == (
        "[Card Retirement] ERROR: invalid configuration issue='bad value'\n"
    )
