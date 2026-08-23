from __future__ import annotations

import datetime as dt

from cninfo_monitor.cache import AnnouncementCache
from cninfo_monitor.cninfo import CNINFO_TIMEZONE


def announcement(announcement_id: str, title: str, timestamp: int) -> dict:
    return {
        "secCode": "000001",
        "secName": "平安银行",
        "announcementTitle": title,
        "announcementId": announcement_id,
        "announcementTime": timestamp,
        "adjunctUrl": f"finalpage/{announcement_id}.PDF",
    }


def moment(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 23, hour, minute, tzinfo=CNINFO_TIMEZONE)


def test_store_scan_upserts_announcements_and_tracks_fetch_cursor(tmp_path):
    cache = AnnouncementCache(tmp_path / "announcements.db")
    first = announcement("1", "旧标题", int(moment(8).timestamp() * 1000))
    updated = announcement("1", "2026年半年度报告", int(moment(8).timestamp() * 1000))

    cache.store_scans(
        {"mainland": [first]},
        scan_starts={"mainland": moment(7)},
        scanned_through=moment(9),
    )
    cache.store_scans(
        {"mainland": [updated]},
        scan_starts={"mainland": moment(8, 55)},
        scanned_through=moment(10),
    )

    assert cache.fetched_through("mainland") == moment(10)
    assert cache.processed_through("mainland") == moment(7)
    assert cache.load_unprocessed("mainland") == [updated]


def test_mark_processed_advances_processing_cursor_without_deleting_raw_data(tmp_path):
    cache = AnnouncementCache(tmp_path / "announcements.db")
    row = announcement("1", "2026年半年度报告", int(moment(8).timestamp() * 1000))
    cache.store_scans(
        {"mainland": [row]},
        scan_starts={"mainland": moment(7)},
        scanned_through=moment(9),
    )

    cache.mark_processed({"mainland"}, moment(9))

    assert cache.processed_through("mainland") == moment(9)
    assert cache.load_unprocessed("mainland", overlap_seconds=300) == []
    assert cache.count_announcements() == 1


def test_date_only_announcement_is_processed_when_discovered_later(tmp_path):
    cache = AnnouncementCache(tmp_path / "announcements.db")
    midnight = moment(0)
    row = announcement(
        "late",
        "2026年半年度报告",
        int(midnight.timestamp() * 1000),
    )

    cache.store_scans(
        {"mainland": []},
        scan_starts={"mainland": moment(8)},
        scanned_through=moment(9),
        snapshot_dates={"mainland": [midnight.date()]},
    )
    cache.mark_processed({"mainland"}, moment(9))
    cache.store_scans(
        {"mainland": [row]},
        scan_starts={"mainland": moment(8, 55)},
        scanned_through=moment(10),
        snapshot_dates={"mainland": [midnight.date()]},
    )

    assert cache.load_unprocessed("mainland") == [row]
    assert cache.announcement_ids_for_date("mainland", midnight.date()) == {"late"}
    assert cache.has_date_snapshot("mainland", midnight.date()) is True


def test_daily_activity_is_recorded_once(tmp_path):
    cache = AnnouncementCache(tmp_path / "announcements.db")
    day = dt.date(2026, 8, 23)

    assert cache.has_daily_activity(day) is False

    cache.record_daily_activity(day, moment(21))

    assert cache.has_daily_activity(day) is True
