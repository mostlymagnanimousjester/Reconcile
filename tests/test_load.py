from argparse import Namespace
from pathlib import Path

import polars as pl
import pytest

from reconcile.cli import engine_from_args, parse_keys
from reconcile.delimited import load_delimited, parse_delimiter, parse_encoding
from reconcile.errors import HardFail
from reconcile.excel import load_excel
from reconcile.load import load_side
from tests.xlsxutil import write_xlsx


def test_parse_keys_strips_and_keeps_internal_spaces():
    assert parse_keys("id, year") == ["id", "year"]
    assert parse_keys("id,  year name") == ["id", "year name"]


def test_parse_keys_rejects_empty_and_dup():
    with pytest.raises(HardFail, match="Empty key"):
        parse_keys("id,,year")
    with pytest.raises(HardFail, match="Duplicate"):
        parse_keys("id,id")


def test_mix_session_and_identity_hard_fails():
    ns = Namespace(
        session="x.recon.zip",
        a="a.csv",
        b=None,
        a_sheet=None,
        b_sheet=None,
        keys=None,
        a_delim=None,
        b_delim=None,
        a_encoding=None,
        b_encoding=None,
    )
    with pytest.raises(HardFail, match="Mixing --session"):
        engine_from_args(ns)


def test_mix_session_and_encoding_hard_fails():
    ns = Namespace(
        session="x.recon.zip",
        a=None,
        b=None,
        a_sheet=None,
        b_sheet=None,
        keys=None,
        a_delim=None,
        b_delim=None,
        a_encoding="windows-1252",
        b_encoding=None,
    )
    with pytest.raises(HardFail, match="Mixing --session"):
        engine_from_args(ns)


def test_quoted_newline_and_doubled_quote(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text('id,note\n1,"hello ""x""\nworld"\n', encoding="utf-8")
    table = load_delimited(p, ",")
    assert table.headers == ["id", "note"]
    assert table.frame.to_dicts() == [{"id": "1", "note": 'hello "x"\nworld'}]


def test_unclosed_quote_surfaces_polars_and_path(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text('id,x\n1,"oops', encoding="utf-8")
    with pytest.raises(HardFail) as ei:
        load_delimited(p, ",", side="A")
    msg = ei.value.message
    assert str(p.resolve()) in msg
    assert "side A" in msg
    assert "row 2" not in msg
    assert "Ragged" not in msg
    assert "not properly escaped" in msg or "invalid csv" in msg.lower() or "parse" in msg.lower()


def test_encoding_utf8_bom(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"\xef\xbb\xbfid,name\n1,a\n")
    table = load_delimited(p, ",")
    assert table.detection.encoding == "utf8"
    assert table.headers == ["id", "name"]
    assert table.frame.to_dicts() == [{"id": "1", "name": "a"}]


def test_utf8_default_rejects_cp1252(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes("id,name\n1,\u20ac\n".encode("cp1252"))
    with pytest.raises(HardFail) as ei:
        load_delimited(p, ",", encoding="utf8", side="B")
    msg = ei.value.message
    assert str(p.resolve()) in msg
    assert "side B" in msg
    assert "utf-8" in msg.lower() or "utf8" in msg.lower()


def test_encoding_override_windows_1252(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes("id,name\n1,\u20ac\n".encode("cp1252"))
    table = load_delimited(p, ",", encoding="windows-1252")
    assert table.detection.encoding == "windows-1252"
    assert table.frame.to_dicts()[0]["name"] == "\u20ac"


def test_utf8_lossy_is_opt_in(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"id,name\n1,\xff\n")
    with pytest.raises(HardFail, match="Failed to parse"):
        load_delimited(p, ",", encoding="utf8")
    table = load_delimited(p, ",", encoding="utf8-lossy")
    assert table.detection.encoding == "utf8-lossy"
    assert table.frame.height == 1


def test_unknown_encoding_hard_fail():
    with pytest.raises(HardFail, match="Unknown encoding for --a-encoding") as ei:
        parse_encoding("latin1", "--a-encoding")
    assert "utf8" in ei.value.message
    assert "windows-1252" in ei.value.message
    with pytest.raises(HardFail, match="Unknown encoding for --b-encoding"):
        parse_encoding("cp1252", "--b-encoding")


def test_missing_delim_hard_fail_non_csv(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("id,name\n1,a\n", encoding="utf-8")
    with pytest.raises(HardFail) as ei:
        load_side(str(p), None, "A")
    msg = ei.value.message
    assert "--a-delim" in msg
    assert str(p.resolve()) in msg
    with pytest.raises(HardFail, match="--b-delim") as ei2:
        load_side(str(p), None, "B")
    assert str(p.resolve()) in ei2.value.message


def test_csv_defaults_to_comma_without_delim_flag(tmp_path: Path):
    from reconcile.engine import Engine

    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.CSV"
    pa.write_text("id,name\n1,a\n", encoding="utf-8")
    pb.write_text("id,name\n1,b\n", encoding="utf-8")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    assert eng.a.detection.delimiter == ","
    assert eng.a.detection.delimiter_name == "comma"
    assert eng.b.detection.delimiter == ","
    assert eng.b.detection.delimiter_name == "comma"
    assert eng.a.frame.to_dicts() == [{"id": "1", "name": "a"}]
    assert eng.b.frame.to_dicts() == [{"id": "1", "name": "b"}]


def test_csv_delim_flag_overrides_comma_default(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id~name\n1~a\n", encoding="utf-8")
    table = load_side(str(p), None, "A", delimiter="~")
    assert table.detection.delimiter == "~"
    assert table.detection.delimiter_name == "tilde"
    assert table.headers == ["id", "name"]
    assert table.frame.to_dicts() == [{"id": "1", "name": "a"}]


def test_cli_delimiter_used_not_extension(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id~name\n1~a\n", encoding="utf-8")
    table = load_delimited(p, "~")
    assert table.detection.delimiter_name == "tilde"
    assert table.headers == ["id", "name"]


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


def test_polars_short_row_padded(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name,x\n1,a\n", encoding="utf-8")
    table = load_delimited(p, ",")
    assert table.frame.to_dicts() == [{"id": "1", "name": "a", "x": ""}]


def test_long_ragged_is_polars_error_with_path(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name\n1,a,extra\n", encoding="utf-8")
    with pytest.raises(HardFail) as ei:
        load_delimited(p, ",", side="A")
    msg = ei.value.message
    assert str(p.resolve()) in msg
    assert "side A" in msg
    assert "row 2" not in msg
    assert "header has" not in msg
    assert "more fields" in msg.lower() or "ragged" in msg.lower() or "Schema" in msg


def test_drop_all_empty_rows(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id,name\n1,a\n,\n2,b\n", encoding="utf-8")
    table = load_delimited(p, ",")
    assert table.frame.to_dicts() == [
        {"id": "1", "name": "a"},
        {"id": "2", "name": "b"},
    ]


def test_quoted_header_accepted_as_polars_reads_it(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text('id,"na""me"\n1,a\n', encoding="utf-8")
    table = load_delimited(p, ",")
    assert table.frame.height == 1
    assert "id" in table.headers
    assert len(table.headers) == 2


def test_delimited_duplicate_header_renamed_not_hard_fail(tmp_path: Path):
    from reconcile.engine import Engine

    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    pa.write_text("id,val,val\n1,a,b\n", encoding="utf-8")
    pb.write_text("id,val\n1,a\n", encoding="utf-8")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert "val" in eng.a.headers
    assert any("duplicated" in h for h in eng.a.headers)
    extras = {n for side, n in eng.pending_extras if side == "A"}
    assert any("duplicated" in n for n in extras)


def test_one_column_header_is_allowed(tmp_path: Path):
    p = tmp_path / "a.csv"
    p.write_text("id\n1\n", encoding="utf-8")
    table = load_delimited(p, ",")
    assert table.headers == ["id"]
    assert table.frame.to_dicts() == [{"id": "1"}]


def test_missing_path():
    with pytest.raises(HardFail, match="Missing path"):
        load_side("/no/such/file.csv", None, "A", delimiter=",")


def test_relative_paths_stored_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "a.csv").write_text("id,name\n1,a\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,name\n1,b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from reconcile.engine import Engine

    eng = Engine.from_paths("a.csv", "b.csv", ["id"], a_delim=",", b_delim=",")
    assert Path(eng.a.path).is_absolute()
    assert Path(eng.b.path).is_absolute()
    assert Path(eng.a.path) == (tmp_path / "a.csv").resolve()
    assert Path(eng.b.path) == (tmp_path / "b.csv").resolve()
    lines = "\n".join(eng.identity_lines())
    assert str((tmp_path / "a.csv").resolve()) in lines
    assert "a.csv" in Path(eng.a.path).name
    assert "utf8" in lines
    assert "comma" in lines


def test_missing_relative_path_error_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HardFail, match="Missing path") as ei:
        load_side("nope.csv", None, "A", delimiter=",")
    assert str((tmp_path / "nope.csv").resolve()) in ei.value.message


def test_zip_stores_delim_encoding_and_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import json
    import zipfile

    from reconcile.engine import Engine

    (tmp_path / "a.csv").write_text("id,val\n1,Y\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,val\n1,Yes\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    eng = Engine.from_paths(
        "a.csv",
        "b.csv",
        ["id"],
        a_encoding="utf8",
        b_encoding="utf8",
    )
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
    assert man["a_detection"]["delimiter"] == ","
    assert man["a_detection"]["delimiter_name"] == "comma"
    assert man["a_detection"]["encoding"] == "utf8"
    assert man["b_detection"]["delimiter"] == ","
    assert man["b_detection"]["encoding"] == "utf8"
    loaded = Engine.from_session("job.recon.zip")
    assert loaded.a.path == str(abs_a)
    assert loaded.b.path == str(abs_b)
    assert loaded.keys == ["id"]
    assert loaded.a.detection.encoding == "utf8"
    assert loaded.a.detection.delimiter == ","
    assert loaded.b.detection.encoding == "utf8"


def test_excel_stays_polars_without_list_dump(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "Y"], ["2", ""]])
    df = load_excel(p, "Sheet1")
    assert isinstance(df, pl.DataFrame)
    assert df.columns == ["id", "val"]
    assert all(dt in (pl.Utf8, pl.String) for dt in df.dtypes)
    recs = df.to_dicts()
    assert {"id": "1", "val": "Y"} in recs
    assert {"id": "2", "val": ""} in recs
    assert not any(isinstance(v, list) for v in recs)
