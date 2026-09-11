import argparse
from pathlib import Path

import pytest

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


def test_cli_mix_session_with_delim_exit_2(tmp_path: Path, capsys):
    code = main(["--session", str(tmp_path / "x.recon.zip"), "--a-delim", "comma"])
    assert code == 2
    assert "Mixing --session" in capsys.readouterr().err


def test_parser_has_sheet_flags():
    p = build_parser()
    ns = p.parse_args(["--a", "a.xlsx", "--b", "b.xlsx", "--a-sheet", "S", "--b-sheet", "T", "--keys", "id"])
    assert ns.a_sheet == "S"
    assert ns.b_sheet == "T"


def test_parser_has_delim_flags():
    p = build_parser()
    ns = p.parse_args(
        [
            "--a",
            "a.csv",
            "--b",
            "b.txt",
            "--keys",
            "id",
            "--a-delim",
            "pipe",
            "--b-delim",
            "~",
        ]
    )
    assert ns.a_delim == "pipe"
    assert ns.b_delim == "~"


def test_cli_unknown_delim_exit_2(tmp_path: Path, capsys):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,name\n1,a\n", encoding="utf-8")
    b.write_text("id,name\n1,a\n", encoding="utf-8")
    code = main(
        ["--a", str(a), "--b", str(b), "--keys", "id", "--a-delim", "semicolon"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Unknown delimiter" in err
    assert "--a-delim" in err
    assert "semicolon" in err


def test_cli_delim_override_mixed_sides(tmp_path: Path):
    from reconcile.cli import engine_from_args

    a = tmp_path / "left.csv"
    b = tmp_path / "right.dat"
    a.write_text("id~name\n1~a\n", encoding="utf-8")
    b.write_text("id|name\n1|b\n", encoding="utf-8")
    p = build_parser()
    ns = p.parse_args(
        [
            "--a",
            str(a),
            "--b",
            str(b),
            "--keys",
            "id",
            "--a-delim",
            "tilde",
            "--b-delim",
            "pipe",
        ]
    )
    eng = engine_from_args(ns)
    assert eng.a.detection.delimiter == "~"
    assert eng.b.detection.delimiter == "|"
    assert Path(eng.a.path).is_absolute()


def test_cli_relative_paths_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from reconcile.cli import engine_from_args

    (tmp_path / "a.csv").write_text("id,name\n1,x\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,name\n1,y\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    p = build_parser()
    ns = p.parse_args(["--a", "a.csv", "--b", "b.csv", "--keys", "id"])
    eng = engine_from_args(ns)
    assert eng.a.path == str((tmp_path / "a.csv").resolve())
    assert eng.b.path == str((tmp_path / "b.csv").resolve())


def test_cli_missing_relative_shows_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.chdir(tmp_path)
    code = main(["--a", "missing.csv", "--b", "also.csv", "--keys", "id"])
    assert code == 2
    err = capsys.readouterr().err
    assert str((tmp_path / "missing.csv").resolve()) in err
