"""Cell / unmatched / extra snapshots: pending splits, accept, undo."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from reconcile.compare import _empty_df, _empty_mismatch_schema
from reconcile.engine import ExtraSnap, InTuiError
from reconcile.roster import refresh_derived

if TYPE_CHECKING:
    from reconcile.engine import Engine


def _empty_cell_snaps(keys: list[str]) -> pl.DataFrame:
    return _empty_df(_empty_mismatch_schema(keys))


def _key_eq_expr(keys: list[str], key: tuple[str, ...]) -> pl.Expr:
    expr: pl.Expr = pl.lit(True)
    for k, v in zip(keys, key):
        expr = expr & (pl.col(k) == v)
    return expr


def _ensure_unmatched_snap_frames(eng: Engine) -> None:
    if eng.unmatched_snaps_a is None or (
        eng.unmatched_snaps_a.is_empty()
        and list(eng.unmatched_snaps_a.columns) != list(eng.a_only.columns)
    ):
        eng.unmatched_snaps_a = eng.a_only.head(0)
    if eng.unmatched_snaps_b is None or (
        eng.unmatched_snaps_b.is_empty()
        and list(eng.unmatched_snaps_b.columns) != list(eng.b_only.columns)
    ):
        eng.unmatched_snaps_b = eng.b_only.head(0)


def _split_on_unique(
    left: pl.DataFrame, snaps: pl.DataFrame, on: list[str]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Left-join ``left`` to unique snaps. Pending order is left-frame order."""
    marked = snaps.select(on).unique().with_columns(pl.lit(True).alias("_snap"))
    joined = left.join(marked, on=on, how="left")
    pending = joined.filter(pl.col("_snap").is_null()).drop("_snap")
    accepted = joined.filter(pl.col("_snap").is_not_null()).drop("_snap")
    return pending, accepted


def _split_cells(eng: Engine) -> tuple[pl.DataFrame, pl.DataFrame]:
    if eng.mismatches.is_empty() or eng.cell_snaps.is_empty():
        return eng.mismatches, eng.mismatches.head(0)
    return _split_on_unique(
        eng.mismatches, eng.cell_snaps, [*eng.keys, "column", "val_a", "val_b"]
    )


def _split_unmatched(eng: Engine, side: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    frame = eng.a_only if side == "A" else eng.b_only
    snaps = eng.unmatched_snaps_a if side == "A" else eng.unmatched_snaps_b
    if frame.is_empty():
        return frame, frame
    if snaps is None or snaps.is_empty():
        return frame, frame.head(0)
    if set(snaps.columns) != set(frame.columns):
        # Schema drift: full-row identity cannot match (not a key-only ignore).
        return frame, frame.head(0)
    cols = list(frame.columns)
    return _split_on_unique(frame, snaps.select(cols), cols)


def _split_extras(eng: Engine) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    current = [("A", n) for n in eng.extras_a] + [("B", n) for n in eng.extras_b]
    accepted_set = {(s.side, s.name) for s in eng.extra_snaps}
    pending = [x for x in current if x not in accepted_set]
    accepted = [x for x in current if x in accepted_set]
    return pending, accepted


def apply_snapshots(eng: Engine) -> None:
    _ensure_unmatched_snap_frames(eng)
    eng.pending_cells, eng.accepted_cells = _split_cells(eng)
    eng.pending_a_only, eng.accepted_a_only = _split_unmatched(eng, "A")
    eng.pending_b_only, eng.accepted_b_only = _split_unmatched(eng, "B")
    eng.pending_extras, eng.accepted_extras = _split_extras(eng)
    refresh_derived(eng)


def _cell_snap_cols(eng: Engine) -> list[str]:
    return [*eng.keys, "column", "val_a", "val_b"]


def _vstack_cell_snaps(eng: Engine, frame: pl.DataFrame) -> int:
    cols = _cell_snap_cols(eng)
    if frame.is_empty():
        apply_snapshots(eng)
        return 0
    frame = frame.select(cols)
    if eng.cell_snaps.is_empty():
        new = frame.unique()
    else:
        new = frame.join(eng.cell_snaps, on=cols, how="anti").unique()
    n = new.height
    if n:
        # Unique the delta only — do not re-unique the growing snap store.
        eng.cell_snaps = (
            new
            if eng.cell_snaps.is_empty()
            else pl.concat([eng.cell_snaps, new], how="vertical")
        )
    apply_snapshots(eng)
    return n


def accept_column(eng: Engine, column: str) -> int:
    if eng.pair_draft_col is not None:
        raise InTuiError("ERROR: confirm or cancel the pair draft first")
    n = _vstack_cell_snaps(eng, eng.pending_cells.filter(pl.col("column") == column))
    eng.column_draft.discard(column)
    return n


def accept_pair(eng: Engine, column: str, val_a: str, val_b: str) -> int:
    frame = eng.pending_cells.filter(
        (pl.col("column") == column)
        & (pl.col("val_a") == val_a)
        & (pl.col("val_b") == val_b)
    )
    return _vstack_cell_snaps(eng, frame)


def accept_pair_across_columns(
    eng: Engine, columns: list[str], val_a: str, val_b: str
) -> int:
    """Accept the exact grain pair on every selected column that still has it."""
    if not columns:
        return 0
    frame = eng.pending_cells.filter(
        pl.col("column").is_in(list(columns))
        & (pl.col("val_a") == val_a)
        & (pl.col("val_b") == val_b)
    )
    return _vstack_cell_snaps(eng, frame)


def accept_cell(
    eng: Engine, key: tuple[str, ...], column: str, val_a: str, val_b: str
) -> int:
    data: dict[str, list[str]] = {k: [v] for k, v in zip(eng.keys, key)}
    data["column"] = [column]
    data["val_a"] = [val_a]
    data["val_b"] = [val_b]
    return _vstack_cell_snaps(eng, pl.DataFrame(data))


def confirm_column_draft(eng: Engine) -> int:
    if eng.pair_draft_col is not None:
        raise InTuiError("ERROR: confirm or cancel the pair draft first")
    names = list(eng.column_draft)
    eng.column_draft = set()
    if not names:
        return 0
    return _vstack_cell_snaps(
        eng, eng.pending_cells.filter(pl.col("column").is_in(names))
    )


def confirm_pair_draft(
    eng: Engine, unchecked: set[tuple[str, ...]] | None = None
) -> int:
    if eng.pair_draft_col is None:
        return 0
    frame = eng._pair_draft_cells
    if frame is None:
        col, va, vb = eng.pair_draft_col, eng.pair_draft_va, eng.pair_draft_vb
        frame = eng.pending_cells.filter(
            (pl.col("column") == col)
            & (pl.col("val_a") == va)
            & (pl.col("val_b") == vb)
        )
    if unchecked:
        exc = pl.DataFrame(
            {k: [key[i] for key in unchecked] for i, k in enumerate(eng.keys)}
        )
        frame = frame.join(exc, on=eng.keys, how="anti")
    if frame.is_empty():
        raise InTuiError("ERROR: nothing to confirm (all unchecked)")
    n = _vstack_cell_snaps(eng, frame)
    eng.clear_pair_draft()
    return n


def _unmatched_attr(side: str) -> str:
    return "unmatched_snaps_a" if side == "A" else "unmatched_snaps_b"


def _vstack_unmatched(eng: Engine, side: str, frame: pl.DataFrame) -> int:
    attr = _unmatched_attr(side)
    snaps: pl.DataFrame | None = getattr(eng, attr)
    if frame.is_empty():
        apply_snapshots(eng)
        return 0
    if snaps is None or snaps.is_empty():
        setattr(eng, attr, frame.unique())
        n = frame.height
    elif set(snaps.columns) == set(frame.columns):
        snaps = snaps.select(list(frame.columns))
        new = frame.join(snaps, on=list(frame.columns), how="anti")
        n = new.height
        if n:
            setattr(eng, attr, pl.concat([snaps, new], how="vertical").unique())
    else:
        n = frame.height
        setattr(eng, attr, pl.concat([snaps, frame], how="diagonal").unique())
    apply_snapshots(eng)
    return n


def accept_unmatched(eng: Engine, side: str, key: tuple[str, ...]) -> int:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    row = pending.filter(_key_eq_expr(eng.keys, key))
    if row.is_empty():
        return 0
    return 1 if _vstack_unmatched(eng, side, row) else 0


def accept_all_unmatched(eng: Engine, side: str) -> int:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    return _vstack_unmatched(eng, side, pending)


def accept_extra(eng: Engine, side: str, name: str) -> int:
    snap = ExtraSnap(side, name)
    if (side, name) not in eng.pending_extras:
        return 0
    if snap not in eng.extra_snaps:
        eng.extra_snaps.append(snap)
    apply_snapshots(eng)
    return 1


def undo_column(eng: Engine, column: str) -> int:
    before = eng.cell_snaps.height
    eng.cell_snaps = eng.cell_snaps.filter(pl.col("column") != column)
    apply_snapshots(eng)
    return before - eng.cell_snaps.height


def undo_cell(eng: Engine, key: tuple[str, ...], column: str) -> int:
    before = eng.cell_snaps.height
    expr = (pl.col("column") == column) & _key_eq_expr(eng.keys, key)
    eng.cell_snaps = eng.cell_snaps.filter(~expr)
    apply_snapshots(eng)
    return before - eng.cell_snaps.height


def undo_pair(eng: Engine, column: str, val_a: str, val_b: str) -> int:
    before = eng.cell_snaps.height
    eng.cell_snaps = eng.cell_snaps.filter(
        ~(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        )
    )
    apply_snapshots(eng)
    return before - eng.cell_snaps.height


def undo_unmatched(eng: Engine, side: str, key: tuple[str, ...] | None = None) -> int:
    attr = _unmatched_attr(side)
    snaps: pl.DataFrame | None = getattr(eng, attr)
    if snaps is None or snaps.is_empty():
        return 0
    before = snaps.height
    if key is None:
        setattr(eng, attr, snaps.head(0))
    else:
        setattr(eng, attr, snaps.filter(~_key_eq_expr(eng.keys, key)))
    apply_snapshots(eng)
    return before - getattr(eng, attr).height


def undo_extra(eng: Engine, side: str, name: str) -> int:
    before = len(eng.extra_snaps)
    eng.extra_snaps = [
        s for s in eng.extra_snaps if not (s.side == side and s.name == name)
    ]
    apply_snapshots(eng)
    return before - len(eng.extra_snaps)


def column_has_returned(eng: Engine, column: str) -> bool:
    if eng.returned_cells_df.is_empty():
        return False
    return eng.returned_cells_df.filter(pl.col("column") == column).height > 0


def cell_is_returned(eng: Engine, key: tuple[str, ...], column: str) -> bool:
    if eng.returned_cells_df.is_empty():
        return False
    return (
        eng.returned_cells_df.filter(
            (pl.col("column") == column) & _key_eq_expr(eng.keys, key)
        ).height
        > 0
    )


def side_has_returned(eng: Engine, side: str) -> bool:
    if eng.returned_keys_df.is_empty():
        return False
    return eng.returned_keys_df.filter(pl.col("side") == side).height > 0


def key_is_returned(eng: Engine, side: str, key: tuple[str, ...]) -> bool:
    if eng.returned_keys_df.is_empty():
        return False
    return (
        eng.returned_keys_df.filter(
            (pl.col("side") == side) & _key_eq_expr(eng.keys, key)
        ).height
        > 0
    )


def pair_has_returned(eng: Engine, column: str, val_a: str, val_b: str) -> bool:
    if eng.returned_cells_df.is_empty():
        return False
    pending = eng.pending_cells.filter(
        (pl.col("column") == column)
        & (pl.col("val_a") == val_a)
        & (pl.col("val_b") == val_b)
    )
    if pending.is_empty():
        return False
    ret = eng.returned_cells_df.filter(pl.col("column") == column)
    if ret.is_empty():
        return False
    return pending.join(ret, on=eng.keys, how="inner").height > 0
