from pathlib import Path

import pytest

from reconcile.cli import engine_from_args, parse_keys
from reconcile.delimited import decode_bytes, load_delimited, parse_delimiter, parse_records
from reconcile.errors import HardFail
from reconcile.load import load_side


def test_parse_keys_strips_and_keeps_internal_spaces():
    assert parse_keys("id, year") == ["id", "year"]
    assert parse_keys("id,  year name") == ["id", "year name"]


def test_parse_keys_rejects_empty_and_dup():
    with pytest.raises(HardFail, match="Empty key"):
        parse_keys("id,,year")
    with pytest.raises(HardFail, match="Duplicate"):
        parse_keys("id,id")


def test_mix_session_and_identity_hard_fails():
    from argparse import Namespace

    ns = Namespace(
        session="x.recon.zip",
        a="a.csv",
        b=None,
        a_sheet=None,
        b_sheet=None,
        keys=None,
        a_delim=None,
        b_delim=None,
    )
    with pytest.raises(HardFail, match="Mixing --session"):
        engine_from_args(ns)


def test_quoted_newline_and_doubled_quote():
    text = 'id,note\n1,"hello ""x""\nworld"\n'
    recs = parse_records(text, ",")
    assert recs[0][1] == ["id", "note"]
    assert recs[1][1][0] == "1"
    assert recs[1][1][1] == 'hello "x"\nworld'


def test_unclosed_quote_hard_fail():
    with pytest.raises(HardFail, match="Unclosed quote"):
        parse_records('id,x\n1,"oops', ",")


def test_encoding_utf8_bom(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"\xef\xbb\xbfid,name\n1,a\n")
    table = load_delimited(p)
    assert table.detection.encoding == "utf-8"
    assert table.headers == ["id", "name"]
    assert table.rows == [["1", "a"]]


def test_encoding_windows_1252(tmp_path: Path):
    p = tmp_path / "a.csv"
    # 0x80 is euro in cp1252; not valid UTF-8
    p.write_bytes("id,name\n1,\u20ac\n".encode("cp1252"))
    table = load_delimited(p)
    assert table.detection.encoding == "windows-1252"
    assert table.rows[0][1] == "\u20ac"


def test_invalid_utf8_after_bom(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"\xef\xbb\xbfid,name\n1,\xff\n")
    with pytest.raises(HardFail, match="Invalid UTF-8 after UTF-8 BOM"):
        load_delimited(p)


def test_csv_extension_defaults_to_comma_no_sniff(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter == ","
    assert table.detection.delimiter_name == "comma"


def test_csv_extension_uses_comma_even_when_pipe_also_splits(tmp_path: Path):
    p = tmp_path / "A.CSV"
    p.write_text("a,b|c\n1,2|3\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter == ","
    assert table.headers == ["a", "b|c"]
    assert table.rows == [["1", "2|3"]]


def test_txt_extension_defaults_to_tilde_no_sniff(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("id~name\n1~a\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter_name == "tilde"
    assert table.headers == ["id", "name"]


def test_txt_extension_uses_tilde_even_when_comma_also_splits(tmp_path: Path):
    p = tmp_path / "a.TXT"
    p.write_text("a~b,c\n1~2,3\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter == "~"
    assert table.headers == ["a", "b,c"]
    assert table.rows == [["1", "2,3"]]


def test_cli_delimiter_overrides_csv_extension(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id~name\n1~a\n", encoding="utf-8")
    table = load_delimited(p, delimiter="~")
    assert table.detection.delimiter_name == "tilde"
    assert table.headers == ["id", "name"]


def test_sniff_pipe_and_tab_for_non_csv_txt(tmp_path: Path):
    p = tmp_path / "a.dat"
    p.write_text("id|name\n1|a\n", encoding="utf-8")
    assert load_delimited(p).detection.delimiter_name == "pipe"
    q = tmp_path / "datafile"
    q.write_text("id\tname\n1\ta\n", encoding="utf-8")
    assert load_delimited(q).detection.delimiter_name == "tab"


def test_sniff_comma_dat(tmp_path: Path):
    p = tmp_path / "a.dat"
    p.write_text("id,name\n1,a\n", encoding="utf-8")
    assert load_delimited(p).detection.delimiter == ","


def test_sniff_failure_hard_fail(tmp_path: Path):
    p = tmp_path / "a.dat"
    p.write_text("hello world\nno delimiters here\n", encoding="utf-8")
    with pytest.raises(HardFail, match="sniff delimiter") as ei:
        load_delimited(p)
    assert str(p.resolve()) in ei.value.message


def test_unknown_delimiter_flag_hard_fail():
    with pytest.raises(HardFail, match="Unknown delimiter for --a-delim"):
        parse_delimiter("semicolon", "--a-delim")
    with pytest.raises(HardFail, match=r"Unknown delimiter for --b-delim: ';"):
        parse_delimiter(";", "--b-delim")


def test_parse_delimiter_names_and_literals():
    assert parse_delimiter("comma", "--a-delim") == ","
    assert parse_delimiter("COMMA", "--a-delim") == ","
    assert parse_delimiter(",", "--a-delim") == ","
    assert parse_delimiter("tilde", "--a-delim") == "~"
    assert parse_delimiter("~", "--a-delim") == "~"
    assert parse_delimiter("pipe", "--a-delim") == "|"
    assert parse_delimiter("|", "--a-delim") == "|"
    assert parse_delimiter("tab", "--a-delim") == "\t"
    assert parse_delimiter("\\t", "--a-delim") == "\t"
    assert parse_delimiter("\t", "--a-delim") == "\t"


def test_ragged_error_has_path_and_row(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name,x\n1,a\n", encoding="utf-8")
    with pytest.raises(HardFail) as ei:
        load_delimited(p)
    msg = ei.value.message
    assert str(p.resolve()) in msg
    assert "Ragged" in msg or "ragged" in msg
    assert "row 2" in msg
    assert "header has 3" in msg


def test_drop_all_empty_rows(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name\n1,a\n,\n2,b\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.rows == [["1", "a"], ["2", "b"]]


def test_missing_path():
    with pytest.raises(HardFail, match="Missing path"):
        load_side("/no/such/file.csv", None, "A")


def test_decode_never_latin1():
    text, enc = decode_bytes("id,name\n1,x\n".encode("utf-8"), "x")
    assert enc == "utf-8"


def test_relative_paths_stored_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "a.csv").write_text("id,name\n1,a\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,name\n1,b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from reconcile.engine import Engine

    eng = Engine.from_paths("a.csv", "b.csv", ["id"])
    assert Path(eng.a.path).is_absolute()
    assert Path(eng.b.path).is_absolute()
    assert Path(eng.a.path) == (tmp_path / "a.csv").resolve()
    assert Path(eng.b.path) == (tmp_path / "b.csv").resolve()
    lines = "\n".join(eng.identity_lines())
    assert str((tmp_path / "a.csv").resolve()) in lines
    assert "a.csv" in Path(eng.a.path).name


def test_missing_relative_path_error_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HardFail, match="Missing path") as ei:
        load_side("nope.csv", None, "A")
    assert str((tmp_path / "nope.csv").resolve()) in ei.value.message


def test_zip_roundtrip_stores_and_reopens_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import json
    import zipfile

    from reconcile.engine import Engine

    (tmp_path / "a.csv").write_text("id,val\n1,Y\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,val\n1,Yes\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    eng = Engine.from_paths("a.csv", "b.csv", ["id"])
    abs_a = (tmp_path / "a.csv").resolve()
    abs_b = (tmp_path / "b.csv").resolve()
    assert Path(eng.a.path) == abs_a
    eng.export_zip("job.recon.zip")
    zpath = tmp_path / "job.recon.zip"
    with zipfile.ZipFile(zpath) as zf:
        man = json.loads(zf.read("manifest.json"))
    assert Path(man["a_path"]).is_absolute()
    assert Path(man["b_path"]).is_absolute()
    assert man["a_path"] == str(abs_a)
    assert man["b_path"] == str(abs_b)
    assert not man["a_path"].startswith(".")
    loaded = Engine.from_session("job.recon.zip")
    assert loaded.a.path == str(abs_a)
    assert loaded.b.path == str(abs_b)
    assert loaded.keys == ["id"]
