"""Polars owns the reconcile kernel. Tall-frame Python dumps are defects.

Hot-path `.to_dicts()` / `.to_list()` on frames taller than PAGE_SIZE are
forbidden except the explicit allowlist (schema-sized roster
handoff, sentinel column names, and page helpers after
`.slice`).

This test greps reconcile kernel modules via AST rather than patching Polars
internals. Page helpers are wrapped so a slice-then-materialize chokepoint
cannot grow past PAGE_SIZE.
"""

from __future__ import annotations

import ast
from pathlib import Path

import polars as pl
import pytest

from reconcile.engine import PAGE_SIZE, Engine
from tests.xlsxutil import write_csv

ROOT = Path(__file__).resolve().parents[1]

KERNEL_FILES = (
    "reconcile/engine.py",
    "reconcile/compare.py",
    "reconcile/snaps.py",
    "reconcile/pages.py",
    "reconcile/roster.py",
    "reconcile/suggest.py",
)

MATERIALIZE_ATTRS = frozenset(
    {"to_dicts", "to_list", "iter_rows", "rows", "to_pandas", "to_numpy"}
)

# (function name) allowed to call materializers. File-agnostic so the WS9 split
# can move helpers without rewriting the allowlist. Anything else is a defect.
ALLOWLIST_FUNCS = frozenset(
    {
        # schema-sized roster handoff after cache build
        "roster",
        "_build_roster_cache",
        "_roster_agg_maps",
        "_roster_fast_maps",
        "_roster_insight_stats",
        # sentinel column names (schema-sized)
        "start_sentinel_draft",
        "sentinel_hits",
        "_sentinel_map",
        # page helper: callers must slice first; helper itself may to_dicts
        "_page_dicts",
        "_page",
        # page-sized pair list → bool mask (not a tall cell dump)
        "pairs_returned_mask",
        # HardFail duplicate-key .row(0) lives next to these; to_list of
        # schema-sized header lists is not a frame dump.
        "_dup_headers_already_checked",
    }
)

# Methods that must not exist once their workstream lands. Listed from the
# start so later WS make this pass rather than deleting the test.
DELETED_ENGINE_ATTRS = (
    "pending_snaps_for_column",
    "_row_matches",
    "is_unmatched_pending",
    "pair_matrix",
    "from_session",
    "export_zip",
    "to_manifest",
)


def _kernel_paths() -> list[Path]:
    return [ROOT / rel for rel in KERNEL_FILES if (ROOT / rel).is_file()]


def _enclosing_function(stack: list[ast.AST]) -> str | None:
    for node in reversed(stack):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return None


def _materialize_sites() -> list[tuple[str, str, int, str]]:
    """Return (file, func_or_<module>, lineno, attr) for each materializer call."""
    sites: list[tuple[str, str, int, str]] = []
    for path in _kernel_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(ROOT))
        stack: list[ast.AST] = []

        class Visitor(ast.NodeVisitor):
            def generic_visit(self, node: ast.AST) -> None:
                stack.append(node)
                try:
                    if isinstance(node, ast.Attribute) and node.attr in MATERIALIZE_ATTRS:
                        func = _enclosing_function(stack) or "<module>"
                        sites.append((rel, func, node.lineno, node.attr))
                    super().generic_visit(node)
                finally:
                    stack.pop()

        Visitor().visit(tree)
    return sites


def test_kernel_materialize_only_in_allowlisted_functions():
    sites = _materialize_sites()
    offenders = [
        f"{rel}:{lineno} {func}().{attr}()"
        for rel, func, lineno, attr in sites
        if func not in ALLOWLIST_FUNCS
    ]
    assert offenders == [], (
        "Engine hot paths must not materialize frames; "
        f"allowlist={sorted(ALLOWLIST_FUNCS)}; found:\n  " + "\n  ".join(offenders)
    )


def test_deleted_python_row_kernels_are_gone():
    for name in DELETED_ENGINE_ATTRS:
        assert not hasattr(Engine, name), f"Engine.{name} must be deleted"


def test_page_dicts_refuses_taller_than_page_size():
    from reconcile import engine as engine_mod

    helper = getattr(engine_mod, "_page_dicts", None)
    if helper is None:
        pytest.fail("_page_dicts helper is missing (WS5 page chokepoint)")
    tall = pl.DataFrame({"id": [str(i) for i in range(PAGE_SIZE + 1)]})
    with pytest.raises((ValueError, AssertionError, RuntimeError)):
        helper(tall)


def test_cells_for_tab_and_unmatched_page_are_paged(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    a_lines = ["id,val"] + [f"{i},A{i}" for i in range(250)]
    b_lines = ["id,val"] + [f"{i},B{i}" for i in range(250)]
    write_csv(pa, "\n".join(a_lines) + "\n")
    write_csv(pb, "\n".join(b_lines) + "\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    recs, page, pages = eng.cells_for_tab("val", "pending", 0)
    assert page == 0
    assert pages == 3
    assert len(recs) == PAGE_SIZE
    recs1, page1, _ = eng.cells_for_tab("val", "pending", 1)
    assert page1 == 1
    assert len(recs1) == PAGE_SIZE
    recs2, _, _ = eng.cells_for_tab("val", "pending", 2)
    assert len(recs2) == 50

    write_csv(pa, "\n".join(["id,val"] + [f"{i},x" for i in range(250)]) + "\n")
    write_csv(pb, "id,val\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    recs, _, pages = eng.unmatched_page("A", 0)
    assert pages == 3
    assert len(recs) == PAGE_SIZE


def test_start_pair_draft_returns_height_without_key_set(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n" + "".join(f"{i},Y\n" for i in range(120)))
    write_csv(pb, "id,val\n" + "".join(f"{i},Yes\n" for i in range(120)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_pair_draft("val", "Y", "Yes")
    assert n == 120
    assert not hasattr(eng, "pair_draft_keys") or eng.pair_draft_keys is None
