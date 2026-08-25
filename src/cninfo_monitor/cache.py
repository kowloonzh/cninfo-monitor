from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class AnnouncementCache:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                market TEXT NOT NULL,
                announcement_id TEXT NOT NULL,
                announcement_time INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (market, announcement_id)
            );
            CREATE INDEX IF NOT EXISTS announcements_market_time
                ON announcements (market, announcement_time);
            CREATE TABLE IF NOT EXISTS scan_cursors (
                market TEXT PRIMARY KEY,
                fetched_through TEXT NOT NULL,
                processed_through TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_activity (
                activity_date TEXT PRIMARY KEY,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS date_snapshots (
                market TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (market, snapshot_date)
            );
            CREATE TABLE IF NOT EXISTS market_cap_pending (
                market TEXT NOT NULL,
                announcement_id TEXT NOT NULL,
                first_deferred_at TEXT NOT NULL,
                PRIMARY KEY (market, announcement_id)
            );
            """
        )
        self._migrate_first_seen_at()

    def close(self) -> None:
        self._connection.close()

    def fetched_through(self, market: str) -> dt.datetime | None:
        return self._cursor_value(market, "fetched_through")

    def processed_through(self, market: str) -> dt.datetime | None:
        return self._cursor_value(market, "processed_through")

    def store_scans(
        self,
        rows_by_market: Mapping[str, Iterable[dict[str, Any]]],
        *,
        scan_starts: Mapping[str, dt.datetime],
        scanned_through: dt.datetime,
        snapshot_dates: Mapping[str, Iterable[dt.date]] | None = None,
    ) -> None:
        end_value = _format_datetime(scanned_through)
        with self._connection:
            for market, rows in rows_by_market.items():
                for row in rows:
                    announcement_id = row.get("announcementId")
                    announcement_time = row.get("announcementTime")
                    if announcement_id is None or announcement_time is None:
                        continue
                    try:
                        timestamp = int(announcement_time)
                    except (TypeError, ValueError):
                        continue
                    self._connection.execute(
                        """
                        INSERT INTO announcements (
                            market, announcement_id, announcement_time,
                            first_seen_at, payload_json
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(market, announcement_id) DO UPDATE SET
                            announcement_time = excluded.announcement_time,
                            payload_json = excluded.payload_json
                        """,
                        (
                            market,
                            str(announcement_id),
                            timestamp,
                            end_value,
                            json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                        ),
                    )
                start_value = _format_datetime(scan_starts[market])
                self._connection.execute(
                    """
                    INSERT INTO scan_cursors (
                        market, fetched_through, processed_through
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(market) DO UPDATE SET
                        fetched_through = excluded.fetched_through
                    """,
                    (market, end_value, start_value),
                )
                for snapshot_date in (snapshot_dates or {}).get(market, ()):
                    self._connection.execute(
                        """
                        INSERT INTO date_snapshots (
                            market, snapshot_date, completed_at
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(market, snapshot_date) DO UPDATE SET
                            completed_at = excluded.completed_at
                        """,
                        (market, snapshot_date.isoformat(), end_value),
                    )

    def load_unprocessed(
        self,
        market: str,
        *,
        overlap_seconds: int = 0,
    ) -> list[dict[str, Any]]:
        fetched = self.fetched_through(market)
        processed = self.processed_through(market)
        if fetched is None or processed is None:
            return []
        rows = self._connection.execute(
            """
            SELECT payload_json
            FROM announcements
            WHERE market = ? AND first_seen_at > ? AND first_seen_at <= ?
            ORDER BY first_seen_at, announcement_id
            """,
            (
                market,
                _format_datetime(processed),
                _format_datetime(fetched),
            ),
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def announcement_ids_for_date(
        self,
        market: str,
        snapshot_date: dt.date,
    ) -> set[str]:
        timezone = dt.timezone(dt.timedelta(hours=8))
        start = dt.datetime.combine(snapshot_date, dt.time(), timezone)
        end = start + dt.timedelta(days=1)
        rows = self._connection.execute(
            """
            SELECT announcement_id
            FROM announcements
            WHERE market = ? AND announcement_time >= ? AND announcement_time < ?
            """,
            (
                market,
                int(start.timestamp() * 1000),
                int(end.timestamp() * 1000),
            ),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def has_date_snapshot(self, market: str, snapshot_date: dt.date) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM date_snapshots
            WHERE market = ? AND snapshot_date = ?
            """,
            (market, snapshot_date.isoformat()),
        ).fetchone()
        return row is not None

    def defer_market_cap_reports(
        self,
        references: Iterable[tuple[str, str]],
        *,
        deferred_at: dt.datetime,
    ) -> None:
        rows = [
            (market, announcement_id, _format_datetime(deferred_at))
            for market, announcement_id in references
        ]
        if not rows:
            return
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO market_cap_pending (
                    market, announcement_id, first_deferred_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(market, announcement_id) DO NOTHING
                """,
                rows,
            )

    def load_market_cap_pending(self, market: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT announcements.payload_json
            FROM market_cap_pending
            JOIN announcements USING (market, announcement_id)
            WHERE market_cap_pending.market = ?
            ORDER BY market_cap_pending.first_deferred_at, announcement_id
            """,
            (market,),
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def clear_market_cap_pending(
        self,
        references: Iterable[tuple[str, str]],
    ) -> None:
        rows = list(references)
        if not rows:
            return
        with self._connection:
            self._connection.executemany(
                """
                DELETE FROM market_cap_pending
                WHERE market = ? AND announcement_id = ?
                """,
                rows,
            )

    def expire_market_cap_pending(self, *, before: dt.datetime) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM market_cap_pending WHERE first_deferred_at < ?",
                (_format_datetime(before),),
            )

    def mark_processed(
        self,
        markets: Iterable[str],
        processed_through: dt.datetime,
    ) -> None:
        value = _format_datetime(processed_through)
        with self._connection:
            for market in markets:
                self._connection.execute(
                    """
                    UPDATE scan_cursors
                    SET processed_through = ?
                    WHERE market = ? AND fetched_through >= ?
                    """,
                    (value, market, value),
                )

    def has_daily_activity(self, day: dt.date) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM daily_activity WHERE activity_date = ?",
            (day.isoformat(),),
        ).fetchone()
        return row is not None

    def record_daily_activity(self, day: dt.date, recorded_at: dt.datetime) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO daily_activity (activity_date, recorded_at)
                VALUES (?, ?)
                ON CONFLICT(activity_date) DO UPDATE SET recorded_at = excluded.recorded_at
                """,
                (day.isoformat(), _format_datetime(recorded_at)),
            )

    def count_announcements(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM announcements").fetchone()
        return int(row[0]) if row else 0

    def _cursor_value(self, market: str, column: str) -> dt.datetime | None:
        row = self._connection.execute(
            f"SELECT {column} FROM scan_cursors WHERE market = ?",
            (market,),
        ).fetchone()
        if row is None:
            return None
        return dt.datetime.fromisoformat(str(row[0]))

    def _migrate_first_seen_at(self) -> None:
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(announcements)"
            ).fetchall()
        }
        if "first_seen_at" not in columns:
            with self._connection:
                self._connection.execute(
                    "ALTER TABLE announcements ADD COLUMN first_seen_at TEXT"
                )
        with self._connection:
            self._connection.execute(
                """
                UPDATE announcements
                SET first_seen_at = COALESCE(
                    (
                        SELECT fetched_through
                        FROM scan_cursors
                        WHERE scan_cursors.market = announcements.market
                    ),
                    '1970-01-01T00:00:00+00:00'
                )
                WHERE first_seen_at IS NULL
                """
            )


def _format_datetime(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("cache cursor datetimes must be timezone-aware")
    return value.isoformat(timespec="seconds")
