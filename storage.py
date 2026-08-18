#Brady McGonagle
#Version 1.0
#8/17/2026

#storage.py - session log for speech_test_V1.


import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/.speech_coach.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT    NOT NULL,   -- ISO8601 UTC
    duration_sec  REAL    NOT NULL,
    source        TEXT    NOT NULL,   -- 'mic' or 'file'
    source_path   TEXT,               -- original upload, or saved wav
    audio_path    TEXT,               -- the wav we kept, if any
    engine        TEXT    NOT NULL,   -- 'google', 'whisper', ...
    verbatim      INTEGER NOT NULL,   -- 1 if engine preserves um/uh
    transcript    TEXT    NOT NULL,
    word_count    INTEGER NOT NULL,
    filler_count  INTEGER NOT NULL,
    marker_count  INTEGER NOT NULL,
    detail_json   TEXT    NOT NULL,   -- {"um": 3, "like": 2}
    label         TEXT                -- optional user tag: "standup", "demo"
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""


def connect(db_path=DB_PATH):
    """Open the database, creating it and the schema if needed."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log_session(conn, *, duration_sec, source, engine, verbatim, transcript,
                word_count, filler_count, marker_count, detail,
                source_path=None, audio_path=None, label=None,
                started_at=None):
    """Insert one session. Returns the new row id."""
    started_at = started_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO sessions (started_at, duration_sec, source, source_path,
                                 audio_path, engine, verbatim, transcript,
                                 word_count, filler_count, marker_count,
                                 detail_json, label)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (started_at, float(duration_sec), source, source_path, audio_path,
         engine, int(bool(verbatim)), transcript, int(word_count),
         int(filler_count), int(marker_count), json.dumps(detail), label),
    )
    conn.commit()
    return cur.lastrowid


def get_session(conn, session_id):
    """One session by id, or None."""
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def update_counts(conn, session_id, word_count, filler_count, marker_count, detail):
    """
    Overwrite the derived counts for one session.

    Used when FILLERS/MARKERS change: the transcript is the source of truth,
    so old rows can be brought forward instead of being thrown away.
    """
    conn.execute(
        """UPDATE sessions SET word_count = ?, filler_count = ?,
           marker_count = ?, detail_json = ? WHERE id = ?""",
        (int(word_count), int(filler_count), int(marker_count),
         json.dumps(detail), session_id),
    )
    conn.commit()


def set_label(conn, session_id, label):
    """Rename a session. Empty string clears the label."""
    conn.execute("UPDATE sessions SET label = ? WHERE id = ?",
                 (label or None, session_id))
    conn.commit()


def delete_session(conn, session_id):
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def wpm(row):
    """Words per minute for a row. Derived, never stored."""
    return row["word_count"] / (row["duration_sec"] / 60) if row["duration_sec"] else 0.0


def recent(conn, limit=10, engine=None):
    """Most recent sessions, newest first."""
    q = "SELECT * FROM sessions"
    args = []
    if engine:
        q += " WHERE engine = ?"
        args.append(engine)
    q += " ORDER BY started_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(q, args).fetchall()


def summary(conn, engine=None):

    q = """SELECT engine,
                  COUNT(*)                      AS sessions,
                  SUM(duration_sec) / 60.0      AS minutes,
                  SUM(word_count)               AS words,
                  SUM(filler_count)             AS fillers,
                  SUM(marker_count)             AS markers
           FROM sessions"""
    
    args = []
    if engine:
        q += " WHERE engine = ?"
        args.append(engine)
    q += " GROUP BY engine ORDER BY sessions DESC"

    out = []
    for r in conn.execute(q, args):
        minutes = r["minutes"] or 0.0
        out.append({
            "engine": r["engine"],
            "sessions": r["sessions"],
            "minutes": minutes,
            "wpm": (r["words"] / minutes) if minutes else 0.0,
            "fillers_per_min": (r["fillers"] / minutes) if minutes else 0.0,
            "markers_per_min": (r["markers"] / minutes) if minutes else 0.0,
        })
    return out


def top_words(conn, limit=10, engine=None):
    """Most frequent filler/marker words across all logged sessions."""
    q = "SELECT detail_json FROM sessions"
    args = []
    if engine:
        q += " WHERE engine = ?"
        args.append(engine)

    totals = {}
    for (blob,) in conn.execute(q, args):
        for k, v in json.loads(blob).items():
            totals[k] = totals.get(k, 0) + v
    return sorted(totals.items(), key=lambda kv: -kv[1])[:limit]


def trend(conn, engine=None, window=5):

    rows = recent(conn, limit=window * 2, engine=engine)
    if len(rows) < window * 2:
        return None

    def agg(chunk):
        mins = sum(r["duration_sec"] for r in chunk) / 60.0
        if not mins:
            return 0.0, 0.0
        return (sum(r["word_count"] for r in chunk) / mins,
                sum(r["filler_count"] for r in chunk) / mins)

    now_wpm, now_fill = agg(rows[:window])
    then_wpm, then_fill = agg(rows[window:window * 2])
    return {
        
        "wpm": now_wpm, "wpm_delta": now_wpm - then_wpm,
        "fillers_per_min": now_fill, "fillers_delta": now_fill - then_fill,
    
    }


def print_history(conn, limit=10, engine=None):
    
    rows = recent(conn, limit=limit, engine=engine)
    if not rows:
        print("No sessions logged yet.")
        return

    print(f"\n{'id':>4}  {'when':<17} {'len':>6} {'wpm':>6} {'fill':>5} {'src':<5} engine")
    print("-" * 62)
    
    for r in rows:
        
        when = r["started_at"].replace("T", " ")[:16]
        print(f"{r['id']:>4}  {when:<17} {r['duration_sec']:>5.0f}s "
              f"{wpm(r):>6.0f} {r['filler_count']:>5} {r['source']:<5} "
              f"{r['engine']}{'' if r['verbatim'] else ' (stripped)'}")

    for s in summary(conn, engine=engine):
        
        print(f"\n{s['engine']}: {s['sessions']} sessions, {s['minutes']:.1f} min")
        print(f"  avg pace     {s['wpm']:6.0f} wpm")
        print(f"  fillers      {s['fillers_per_min']:6.1f}/min")
        print(f"  markers      {s['markers_per_min']:6.1f}/min")

    t = trend(conn, engine=engine)
    
    if t:
        
        print(f"\nLast 5 vs previous 5:")
        print(f"  pace         {t['wpm']:6.0f} wpm  ({t['wpm_delta']:+.0f})")
        print(f"  fillers      {t['fillers_per_min']:6.1f}/min ({t['fillers_delta']:+.1f})")

    words = top_words(conn, limit=5, engine=engine)
    
    if words:
        
        print("\nMost common:")
        for w, c in words:
            print(f"  {w:<12} {c}")