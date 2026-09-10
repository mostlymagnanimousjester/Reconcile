from pathlib import Path

import pytest

from reconcile.cli import engine_from_args, parse_keys
from reconcile.delimited import decode_bytes, detect_delimiter, load_delimited, parse_records
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

    ns = Namespace(session="x.recon.zip", a="a.csv", b=None, a_sheet=None, b_sheet=None, keys=None)
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


def test_delimiter_comma(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter == ","
    assert table.detection.delimiter_name == "comma"


def test_delimiter_tilde(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("id~name\n1~a\n", encoding="utf-8")
    table = load_delimited(p)
    assert table.detection.delimiter_name == "tilde"


def test_delimiter_pipe_and_tab(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("id|name\n1|a\n", encoding="utf-8")
    assert load_delimited(p).detection.delimiter_name == "pipe"
    p.write_text("id\tname\n1\ta\n", encoding="utf-8")
    assert load_delimited(p).detection.delimiter_name == "tab"


def test_ambiguous_delimiter_hard_fail(tmp_path: Path):
    # both comma and pipe yield 2 consistent fields
    p = tmp_path / "a.txt"
    p.write_text("a,b|c\n1,2|3\n", encoding="utf-8")
    # header "a,b|c" with comma → ["a","b|c"] (2 fields); pipe → ["a,b","c"] (2)
    # data "1,2|3" comma → ["1","2|3"]; pipe → ["1,2","3"]
    with pytest.raises(HardFail, match="Ambiguous delimiter"):
        load_delimited(p)


def test_ragged_error_has_path_and_row(tmp_path: Path):
    p = tmp_path / "a.csv"
    # comma is the only candidate with ≥2 header fields, but row 2 is ragged
    p.write_text("id,name,x\n1,a\n", encoding="utf-8")
    with pytest.raises(HardFail) as ei:
        load_delimited(p)
    msg = ei.value.message
    assert str(p.resolve()) in msg or str(p) in msg
    assert "plausible" in msg or "Ragged" in msg


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
