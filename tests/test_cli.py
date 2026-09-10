import argparse
from pathlib import Path

from reconcile.cli import build_parser, main, parse_keys
from tests.xlsxutil import write_csv


def test_cli_help_exit_zero():
    assert main(["--help"]) == 0


def test_cli_missing_identity_exit_2():
    assert main([]) == 2


def test_cli_load_and_hard_fail_missing(tmp_path: Path, capsys):
    code = main(["--a", str(tmp_path / "no.csv"), "--b", str(tmp_path / "no2.csv"), "--keys", "id"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Missing path" in err


def test_cli_mix_session_exit_2(tmp_path: Path, capsys):
    code = main(["--session", str(tmp_path / "x.recon.zip"), "--a", "a.csv"])
    assert code == 2
    assert "Mixing --session" in capsys.readouterr().err


def test_parser_has_sheet_flags():
    p = build_parser()
    ns = p.parse_args(["--a", "a.xlsx", "--b", "b.xlsx", "--a-sheet", "S", "--b-sheet", "T", "--keys", "id"])
    assert ns.a_sheet == "S"
    assert ns.b_sheet == "T"
