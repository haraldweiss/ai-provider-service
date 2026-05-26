#!/usr/bin/env python3
"""
Trim empty/errored assistant turns from OpenWebUI chat history.

Background:
    When the upstream model (Ollama via reverse SSH tunnel, LiteLLM/Anthropic)
    is briefly unreachable, OpenWebUI persists a chat turn whose assistant
    content stays empty (with an error blob attached). Two storage locations
    end up dirty:

    1. chat.chat JSON blob — history.messages tree + currentId. Stale empty
       nodes here corrupt the model context on the next turn.
    2. chat_message table — UI reads error blobs from here. Stale rows here
       surface as red "Cannot connect …" banners in the chat view even after
       the underlying problem is fixed.

What this does:
    - For every chat, walks back from history.currentId, drops trailing leaf
      nodes whose content is empty AND older than --min-age seconds (default
      60s, so we never touch a node that is mid-stream from a live request),
      rewires currentId to the last non-empty ancestor and rebuilds the flat
      messages list.
    - In chat_message, deletes assistant rows with empty content AND a
      non-null/non-empty error blob AND age > --min-age.

Safe to run while the OpenWebUI container is up: SQLite WAL mode permits a
single writer alongside readers; we use BEGIN IMMEDIATE + busy_timeout so a
concurrent UI write either gets serialized or we retry on the next timer.

Usage:
    trim_empty_chats.py [--db PATH] [--min-age SEC] [--dry-run] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

DEFAULT_DB = "/app/backend/data/webui.db"
DEFAULT_MIN_AGE = 60


def is_empty(node: dict) -> bool:
    if not isinstance(node, dict):
        return True
    if (node.get("content") or "").strip():
        return False
    if node.get("files") or node.get("images"):
        return False
    if node.get("toolCalls") or node.get("tool_calls"):
        return False
    return True


def node_age(node: dict, now: float) -> float:
    ts = node.get("timestamp")
    if not isinstance(ts, (int, float)):
        return float("inf")
    # OpenWebUI stores timestamps in milliseconds for messages
    if ts > 1e12:
        ts = ts / 1000.0
    return max(0.0, now - ts)


def linearize(msgs: dict, cur: str | None) -> list:
    out = []
    nid = cur
    seen = set()
    while nid and nid in msgs and nid not in seen:
        seen.add(nid)
        out.append(msgs[nid])
        nid = msgs[nid].get("parentId")
    return list(reversed(out))


def _is_empty_text(value) -> bool:
    """True for SQL NULL, empty string, the JSON literal "null", or a string
    whose stripped form has <= 2 chars (i.e. the JSON-serialized empty string
    "" that OpenWebUI writes into the JSON-typed content column)."""
    if value is None:
        return True
    s = str(value).strip()
    if s in ("", "null", '""'):
        return True
    return len(s) <= 2


def _has_error(value) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    return s not in ("", "null", "{}")


def trim_chat_json(cur: sqlite3.Cursor, min_age: int, dry_run: bool, verbose: bool) -> int:
    """Trim trailing-empty nodes in chat.chat JSON blob (history tree)."""
    rows = cur.execute("SELECT id, title, chat FROM chat").fetchall()
    now = time.time()
    changed = 0
    for cid, title, raw in rows:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        hist = data.get("history") or {}
        msgs = hist.get("messages") or {}
        current = hist.get("currentId")
        if not current or current not in msgs:
            continue
        trim: list[str] = []
        nid = current
        while nid and nid in msgs and is_empty(msgs[nid]):
            if node_age(msgs[nid], now) < min_age:
                # Still possibly mid-stream; stop trimming this chat.
                trim = []
                break
            trim.append(nid)
            nid = msgs[nid].get("parentId")
        if not trim:
            continue
        new_current = nid
        trim_set = set(trim)
        for node in msgs.values():
            ch = node.get("childrenIds")
            if isinstance(ch, list) and any(x in trim_set for x in ch):
                node["childrenIds"] = [x for x in ch if x not in trim_set]
        for tid in trim:
            msgs.pop(tid, None)
        hist["currentId"] = new_current
        data["history"] = hist
        data["messages"] = linearize(msgs, new_current) if new_current else []
        if verbose or dry_run:
            title_s = (title or "")[:50]
            print(f"  json trim {len(trim)} from {cid[:8]} ({title_s})", file=sys.stderr)
        if not dry_run:
            cur.execute(
                "UPDATE chat SET chat=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), int(now), cid),
            )
        changed += 1
    return changed


def trim_chat_message_table(cur: sqlite3.Cursor, min_age: int, dry_run: bool, verbose: bool) -> int:
    """Delete assistant rows in chat_message with empty content + non-null
    error + age > min_age. Filtered in Python to avoid escaping the JSON
    string literals ('null', '{}', etc.) in SQL."""
    now = int(time.time())
    rows = cur.execute(
        "SELECT id, role, content, error, created_at FROM chat_message"
    ).fetchall()
    deleted = 0
    for mid, role, content, err, created in rows:
        if role != "assistant":
            continue
        if not _is_empty_text(content):
            continue
        if not _has_error(err):
            continue
        age = now - int(created or 0)
        if age < min_age:
            continue
        if verbose or dry_run:
            err_preview = str(err)[:100].replace("\n", " ")
            print(f"  msg delete {mid[:20]} age={age}s err={err_preview}", file=sys.stderr)
        if not dry_run:
            cur.execute("DELETE FROM chat_message WHERE id=?", (mid,))
        deleted += 1
    return deleted


def process(db_path: str, min_age: int, dry_run: bool, verbose: bool) -> tuple[int, int]:
    con = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    con.execute("PRAGMA busy_timeout = 5000")
    cur = con.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        n_json = trim_chat_json(cur, min_age, dry_run, verbose)
        n_rows = trim_chat_message_table(cur, min_age, dry_run, verbose)
        if dry_run:
            cur.execute("ROLLBACK")
        else:
            cur.execute("COMMIT")
        return n_json, n_rows
    except Exception:
        cur.execute("ROLLBACK")
        raise
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--min-age", type=int, default=DEFAULT_MIN_AGE,
                    help="Skip nodes younger than this many seconds (avoid races with live streaming)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    n_json, n_rows = process(args.db, args.min_age, args.dry_run, args.verbose)
    if n_json or n_rows or args.verbose:
        suffix = " (dry-run)" if args.dry_run else ""
        print(
            f"trimmed {n_json} chat blob(s), deleted {n_rows} chat_message row(s){suffix}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
