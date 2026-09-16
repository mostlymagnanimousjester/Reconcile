from pathlib import Path

import pytest

from reconcile.cli import build_parser, main
from tests.xlsxutil import write_xlsx


def test_cli_help_exit_zero():
    assert main(["--help"]) == 0


def test_cli_missing_identity_exit_2():
    assert main([]) == 2


def test_cli_load_and_hard_fail_missing(tmp_path: Path, capsys):
    code = main(
        [
            "--a",
            str(tmp_path / "no.csv"),
            "--b",
            str(tmp_path / "no2.csv"),
            "--keys",
            "id",
            "--a-delim",
            "comma",
            "--b-delim",
            "comma",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Missing path" in err


def test_cli_csv_no_delim_defaults_to_comma(tmp_path: Path):
    from reconcile.cli import engine_from_args

    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,name\n1,a\n", encoding="utf-8")
    b.write_text("id,name\n1,b\n", encoding="utf-8")
    p = build_parser()
    ns = p.parse_args(["--a", str(a), "--b", str(b), "--keys", "id"])
    eng = engine_from_args(ns)[0]
    assert eng.a.detection.delimiter == ","
    assert eng.a.detection.delimiter_name == "comma"
    assert eng.b.detection.delimiter == ","
    assert eng.b.detection.delimiter_name == "comma"
    assert eng.a.frame.to_dicts() == [{"id": "1", "name": "a"}]
    assert eng.b.frame.to_dicts() == [{"id": "1", "name": "b"}]


def test_cli_csv_a_delim_tilde_overrides(tmp_path: Path):
    from reconcile.cli import engine_from_args

    a = tmp_path / "left.csv"
    b = tmp_path / "right.csv"
    a.write_text("id~name\n1~a\n", encoding="utf-8")
    b.write_text("id,name\n1,b\n", encoding="utf-8")
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
        ]
    )
    eng = engine_from_args(ns)[0]
    assert eng.a.detection.delimiter == "~"
    assert eng.a.detection.delimiter_name == "tilde"
    assert eng.b.detection.delimiter == ","
    assert eng.a.headers == ["id", "name"]
    assert Path(eng.a.path).is_absolute()


def test_cli_missing_delim_txt_exit_2(tmp_path: Path, capsys):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("id,name\n1,a\n", encoding="utf-8")
    b.write_text("id,name\n1,a\n", encoding="utf-8")
    code = main(["--a", str(a), "--b", str(b), "--keys", "id"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--a-delim" in err
    assert str(a.resolve()) in err


def test_cli_missing_delim_exit_2(tmp_path: Path, capsys):
    a = tmp_path / "a.dat"
    b = tmp_path / "b.dat"
    a.write_text("id,name\n1,a\n", encoding="utf-8")
    b.write_text("id,name\n1,a\n", encoding="utf-8")
    code = main(["--a", str(a), "--b", str(b), "--keys", "id"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--a-delim" in err
    assert str(a.resolve()) in err


def test_cli_mix_session_exit_2(tmp_path: Path, capsys):
    code = main(["--session", str(tmp_path / "x.recon.zip"), "--a", "a.csv"])
    assert code == 2
    assert "Mixing --session" in capsys.readouterr().err


def test_cli_mix_session_with_delim_exit_2(tmp_path: Path, capsys):
    code = main(["--session", str(tmp_path / "x.recon.zip"), "--a-delim", "comma"])
    assert code == 2
    assert "Mixing --session" in capsys.readouterr().err


def test_cli_mix_session_with_encoding_exit_2(tmp_path: Path, capsys):
    code = main(
        ["--session", str(tmp_path / "x.recon.zip"), "--a-encoding", "windows-1252"]
    )
    assert code == 2
    assert "Mixing --session" in capsys.readouterr().err


def test_parser_has_sheet_flags():
    p = build_parser()
    ns = p.parse_args(["--a", "a.xlsx", "--b", "b.xlsx", "--a-sheet", "S", "--b-sheet", "T", "--keys", "id"])
    assert ns.a_sheet == "S"
    assert ns.b_sheet == "T"


def test_parser_has_delim_and_encoding_flags():
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
            "--a-encoding",
            "utf8",
            "--b-encoding",
            "windows-1252",
        ]
    )
    assert ns.a_delim == "pipe"
    assert ns.b_delim == "~"
    assert ns.a_encoding == "utf8"
    assert ns.b_encoding == "windows-1252"


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


def test_cli_unknown_encoding_exit_2(tmp_path: Path, capsys):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,name\n1,a\n", encoding="utf-8")
    b.write_text("id,name\n1,a\n", encoding="utf-8")
    code = main(
        [
            "--a",
            str(a),
            "--b",
            str(b),
            "--keys",
            "id",
            "--a-delim",
            "comma",
            "--b-delim",
            "comma",
            "--a-encoding",
            "latin1",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Unknown encoding" in err
    assert "--a-encoding" in err
    assert "latin1" in err
    assert "utf8" in err
    assert "windows-1252" in err


def test_cli_encoding_override(tmp_path: Path):
    from reconcile.cli import engine_from_args

    a = tmp_path / "left.csv"
    b = tmp_path / "right.csv"
    a.write_bytes("id,name\n1,\u20ac\n".encode("cp1252"))
    b.write_text("id,name\n1,x\n", encoding="utf-8")
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
            "comma",
            "--b-delim",
            "comma",
            "--a-encoding",
            "windows-1252",
        ]
    )
    eng = engine_from_args(ns)[0]
    assert eng.a.detection.encoding == "windows-1252"
    assert eng.b.detection.encoding == "utf8"
    assert eng.a.frame.to_dicts()[0]["name"] == "\u20ac"


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
    eng = engine_from_args(ns)[0]
    assert eng.a.detection.delimiter == "~"
    assert eng.b.detection.delimiter == "|"
    assert Path(eng.a.path).is_absolute()


def test_cli_encoding_illegal_on_excel(tmp_path: Path, capsys):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.csv"
    write_xlsx(a, "Sheet1", [["id", "val"], ["1", "a"]])
    b.write_text("id,val\n1,a\n", encoding="utf-8")
    code = main(
        [
            "--a",
            str(a),
            "--b",
            str(b),
            "--keys",
            "id",
            "--a-sheet",
            "Sheet1",
            "--a-encoding",
            "utf8",
            "--b-delim",
            "comma",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "--a-encoding" in err
    assert str(a.resolve()) in err


def test_cli_relative_paths_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from reconcile.cli import engine_from_args

    (tmp_path / "a.csv").write_text("id,name\n1,x\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,name\n1,y\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    p = build_parser()
    ns = p.parse_args(
        [
            "--a",
            "a.csv",
            "--b",
            "b.csv",
            "--keys",
            "id",
            "--a-delim",
            "comma",
            "--b-delim",
            "comma",
        ]
    )
    eng = engine_from_args(ns)[0]
    assert eng.a.path == str((tmp_path / "a.csv").resolve())
    assert eng.b.path == str((tmp_path / "b.csv").resolve())


def test_cli_missing_relative_shows_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "--a",
            "missing.csv",
            "--b",
            "also.csv",
            "--keys",
            "id",
            "--a-delim",
            "comma",
            "--b-delim",
            "comma",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert str((tmp_path / "missing.csv").resolve()) in err


def test_cli_textual_cannot_start_only_for_import_or_construct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,val\n1,a\n", encoding="utf-8")
    b.write_text("id,val\n1,b\n", encoding="utf-8")
    args = ["--a", str(a), "--b", str(b), "--keys", "id"]

    class BoomConstruct:
        def __init__(self, *a, **k):
            raise RuntimeError("no tty")

    import reconcile.tui as tui_mod

    monkeypatch.setattr(tui_mod, "ReconcileApp", BoomConstruct)
    assert main(args) == 2
    err = capsys.readouterr().err
    assert "Textual cannot start" in err

    class BoomRun:
        def __init__(self, *a, **k):
            pass

        def run(self):
            raise RuntimeError("run exploded")

    monkeypatch.setattr(tui_mod, "ReconcileApp", BoomRun)
    with pytest.raises(RuntimeError, match="run exploded"):
        main(args)
    err = capsys.readouterr().err
    assert "Textual cannot start" not in err
