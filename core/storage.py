"""SQLite storage for runs, raw responses and citations (one DB per project).

Tables:
  runs       one row per `run` command, with cost totals
  responses  one row per (engine, query) answer, including the full raw JSON
  citations  one row per source URL attached to a response

The `cache_key` on responses is what lets re-runs skip queries we already
paid for. See Engine.cache_key().
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from engines import EngineResponse

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    engines         TEXT,
    calls_made      INTEGER DEFAULT 0,
    cached          INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    total_cost_usd  REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS responses (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER REFERENCES runs(id),
    cache_key       TEXT NOT NULL,
    engine          TEXT NOT NULL,
    model           TEXT,
    query           TEXT NOT NULL,
    answer_text     TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    search_calls    INTEGER,
    cost_usd        REAL,
    cost_source     TEXT,
    latency_ms      INTEGER,
    created_at      TEXT NOT NULL,
    raw_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_responses_cache ON responses(cache_key);
CREATE TABLE IF NOT EXISTS citations (
    id              INTEGER PRIMARY KEY,
    response_id     INTEGER NOT NULL REFERENCES responses(id),
    position        INTEGER,
    url             TEXT,
    domain          TEXT,
    title           TEXT,
    cited_in_text   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_citations_response ON citations(response_id);
CREATE TABLE IF NOT EXISTS sentiments (
    response_id     INTEGER NOT NULL REFERENCES responses(id),
    input_hash      TEXT NOT NULL,      -- hash of brand + excerpts; changes if aliases change
    sentiment       TEXT,               -- positive | neutral | negative | unknown
    reason          TEXT,
    model           TEXT,
    cost_usd        REAL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (response_id, input_hash)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- runs ------------------------------------------------------------

    def start_run(self, engines: list[str]) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, engines) VALUES (?, ?)", (utc_now(), ",".join(engines))
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, calls_made: int, cached: int, failed: int, cost: float) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, calls_made=?, cached=?, failed=?, total_cost_usd=? WHERE id=?",
            (utc_now(), calls_made, cached, failed, cost, run_id),
        )
        self.conn.commit()

    # ---- responses -------------------------------------------------------

    def is_cached(self, cache_key: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM responses WHERE cache_key=? LIMIT 1", (cache_key,)).fetchone()
        return row is not None

    def save_response(self, run_id: int, cache_key: str, resp: EngineResponse) -> int:
        cur = self.conn.execute(
            """INSERT INTO responses (run_id, cache_key, engine, model, query, answer_text,
                   input_tokens, output_tokens, search_calls, cost_usd, cost_source,
                   latency_ms, created_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, cache_key, resp.engine, resp.model, resp.query, resp.answer_text,
                resp.input_tokens, resp.output_tokens, resp.search_calls, resp.cost_usd,
                resp.cost_source, resp.latency_ms, utc_now(), json.dumps(resp.raw, ensure_ascii=False),
            ),
        )
        response_id = cur.lastrowid
        self.conn.executemany(
            """INSERT INTO citations (response_id, position, url, domain, title, cited_in_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (response_id, i, c.url, c.domain, c.title, int(c.cited_in_text))
                for i, c in enumerate(resp.citations, start=1)
            ],
        )
        self.conn.commit()
        return response_id

    def latest_responses(self) -> list[sqlite3.Row]:
        """Newest answer for each (engine, query) pair. Analysis uses this."""
        return self.conn.execute(
            """SELECT r.* FROM responses r
               JOIN (SELECT engine, query, MAX(id) AS id FROM responses GROUP BY engine, query) latest
                 ON r.id = latest.id
               ORDER BY r.engine, r.id"""
        ).fetchall()

    def citations_for(self, response_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
        """Citations grouped by response id, in the order the engine returned them."""
        grouped: dict[int, list[sqlite3.Row]] = {rid: [] for rid in response_ids}
        if not response_ids:
            return grouped
        marks = ",".join("?" * len(response_ids))
        rows = self.conn.execute(
            f"SELECT * FROM citations WHERE response_id IN ({marks}) ORDER BY response_id, position",
            response_ids,
        ).fetchall()
        for row in rows:
            grouped[row["response_id"]].append(row)
        return grouped

    # ---- sentiment cache ---------------------------------------------------

    def get_sentiment(self, response_id: int, input_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sentiments WHERE response_id=? AND input_hash=?", (response_id, input_hash)
        ).fetchone()

    def save_sentiment(
        self, response_id: int, input_hash: str, sentiment: str, reason: str, model: str, cost: float
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO sentiments
               (response_id, input_hash, sentiment, reason, model, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (response_id, input_hash, sentiment, reason, model, cost, utc_now()),
        )
        self.conn.commit()

    # ---- reporting helpers -----------------------------------------------

    def total_analysis_cost(self) -> float:
        row = self.conn.execute("SELECT SUM(cost_usd) FROM sentiments").fetchone()
        return row[0] or 0.0

    def spend_by_engine(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT engine, COUNT(*) AS calls, SUM(cost_usd) AS cost,
                      SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens
               FROM responses GROUP BY engine ORDER BY engine"""
        ).fetchall()

    def export_csv(self, out_dir: Path, prefix: str) -> list[Path]:
        """Write responses and citations to CSV (UTF-8 with BOM so Excel reads it correctly)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        responses_path = out_dir / f"{prefix}_responses.csv"
        citations_path = out_dir / f"{prefix}_citations.csv"

        rows = self.conn.execute(
            """SELECT r.id, r.run_id, r.created_at, r.engine, r.model, r.query, r.answer_text,
                      r.input_tokens, r.output_tokens, r.search_calls, r.cost_usd, r.cost_source,
                      r.latency_ms,
                      (SELECT GROUP_CONCAT(url, ' | ') FROM citations c
                        WHERE c.response_id = r.id AND c.cited_in_text = 1) AS cited_urls
               FROM responses r ORDER BY r.id"""
        ).fetchall()
        _write_csv(responses_path, rows)

        rows = self.conn.execute(
            """SELECT c.response_id, r.engine, r.query, c.position, c.domain, c.url, c.title, c.cited_in_text
               FROM citations c JOIN responses r ON r.id = c.response_id
               ORDER BY c.response_id, c.position"""
        ).fetchall()
        _write_csv(citations_path, rows)
        return [responses_path, citations_path]

    def close(self) -> None:
        self.conn.close()


def _write_csv(path: Path, rows: list[sqlite3.Row]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows([tuple(r) for r in rows])
