from __future__ import annotations

import json
import io
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import xlrd
from pypdf import PdfReader

from cninfo_monitor.models import Report


@dataclass(frozen=True)
class IndexSpec:
    name: str
    code: str
    market: str
    expected_count: int
    source_url: str


INDEX_SPECS = (
    IndexSpec(
        "沪深300",
        "000300",
        "mainland",
        300,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/000300cons.xls",
    ),
    IndexSpec(
        "中证500",
        "000905",
        "mainland",
        500,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/000905cons.xls",
    ),
    IndexSpec(
        "中证1000",
        "000852",
        "mainland",
        1000,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/000852cons.xls",
    ),
    IndexSpec(
        "中证2000",
        "932000",
        "mainland",
        2000,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/932000cons.xls",
    ),
    IndexSpec(
        "恒生科技",
        "HSTECH",
        "hong_kong",
        30,
        "https://www.hsi.com.hk/static/uploads/contents/zh_cn/dl_centre/"
        "factsheets/hstechc.pdf",
    ),
    IndexSpec(
        "科创50",
        "000688",
        "mainland",
        50,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/000688cons.xls",
    ),
    IndexSpec(
        "科创创业50",
        "931643",
        "mainland",
        50,
        "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
        "file/autofile/cons/931643cons.xls",
    ),
    IndexSpec(
        "机器人产业",
        "980022",
        "mainland",
        50,
        "https://www.cnindex.com.cn/sample-detail/detail",
    ),
)

BUNDLED_INDEX_CACHE_PATH = Path(__file__).with_name("data") / "index_memberships.json"
DEFAULT_INDEX_CACHE_PATH = Path("data/index_memberships.json")
HSTECH_LIVE_URL = (
    "https://www.hsi.com.hk/api/wsit-hsil-hiip-ea-public-proxy/v1/"
    "dataretrieval/e/constituents/v1"
)


def enrich_reports_with_index_memberships(
    reports: Iterable[Report],
    memberships: Mapping[str, tuple[str, ...]],
) -> tuple[Report, ...]:
    return tuple(
        replace(
            report,
            index_names=memberships.get(
                f"{report.market}:{report.sec_code}",
                (),
            ),
        )
        for report in reports
    )


def load_index_memberships(
    runtime_path: str | Path = DEFAULT_INDEX_CACHE_PATH,
    *,
    seed_path: str | Path = BUNDLED_INDEX_CACHE_PATH,
) -> dict[str, tuple[str, ...]]:
    for path in (Path(runtime_path), Path(seed_path)):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("version") != 1 or not isinstance(raw.get("memberships"), dict):
                continue
            return {
                str(key): tuple(str(name) for name in names)
                for key, names in raw["memberships"].items()
                if isinstance(names, list)
            }
        except (AttributeError, json.JSONDecodeError, OSError):
            continue
    return {}


def parse_hsi_constituent_text(text: str) -> set[str]:
    if "成份股" not in text:
        return set()
    section = text.split("成份股", 1)[1].split("合共", 1)[0]
    return {
        match.group(1).zfill(5)
        for match in re.finditer(
            r"(?m)^\s*(\d{4,5})\s+[A-Z0-9]{12}\s+",
            section,
        )
    }


def parse_csi_rows(
    rows: Iterable[list[object]],
    expected_index_code: str,
) -> tuple[set[str], str]:
    data_rows = list(rows)[1:]
    codes: set[str] = set()
    dates: set[str] = set()
    for row in data_rows:
        if len(row) < 5 or not str(row[4]).strip():
            continue
        index_code = str(row[1]).strip()
        if index_code != expected_index_code:
            raise ValueError(
                f"中证成份文件包含指数 {index_code}，预期 {expected_index_code}"
            )
        raw_date = str(row[0]).strip()
        if not re.fullmatch(r"\d{8}", raw_date):
            raise ValueError(f"中证成份文件日期格式无效：{raw_date}")
        dates.add(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
        codes.add(str(row[4]).strip().zfill(6))
    if len(dates) != 1:
        raise ValueError(f"中证成份文件包含 {len(dates)} 个快照日期")
    return codes, dates.pop()


def parse_cni_payload(payload: Mapping[str, object]) -> tuple[set[str], str]:
    data = payload.get("data")
    if payload.get("code") != 200 or not isinstance(data, dict):
        raise ValueError("国证机器人产业样本接口返回无效数据")
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != data.get("total"):
        raise ValueError("国证机器人产业样本接口未返回完整样本")
    codes = {
        str(row["seccode"]).strip().zfill(6)
        for row in rows
        if isinstance(row, dict) and row.get("seccode")
    }
    dates = {
        str(row["dateStr"])
        for row in rows
        if isinstance(row, dict) and row.get("dateStr")
    }
    if len(dates) != 1:
        raise ValueError("国证机器人产业样本日期不一致")
    return codes, dates.pop()


def refresh_official_index_cache(
    output_path: str | Path = DEFAULT_INDEX_CACHE_PATH,
) -> dict[str, object]:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    constituents: dict[str, set[str]] = {}
    as_of: dict[str, str] = {}
    csi_codes = {"000300", "000905", "000852", "932000", "000688", "931643"}
    headers = {"User-Agent": "Mozilla/5.0 cninfo-monitor/0.1"}
    try:
        with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
            for spec in INDEX_SPECS:
                if spec.code in csi_codes:
                    response = client.get(spec.source_url)
                    response.raise_for_status()
                    workbook = xlrd.open_workbook(file_contents=response.content)
                    sheet = workbook.sheet_by_index(0)
                    rows = [sheet.row_values(number) for number in range(sheet.nrows)]
                    constituents[spec.code], as_of[spec.code] = parse_csi_rows(
                        rows,
                        spec.code,
                    )
                elif spec.code == "980022":
                    response = client.get(
                        spec.source_url,
                        params={
                            "indexcode": spec.code,
                            "dateStr": now.strftime("%Y-%m"),
                            "pageNum": 1,
                            "rows": spec.expected_count,
                        },
                    )
                    response.raise_for_status()
                    constituents[spec.code], as_of[spec.code] = parse_cni_payload(
                        response.json()
                    )
                else:
                    response = client.get(spec.source_url)
                    response.raise_for_status()
                    reader = PdfReader(io.BytesIO(response.content))
                    text = "\n".join(page.extract_text() or "" for page in reader.pages)
                    constituents[spec.code] = parse_hsi_constituent_text(text)
                    as_of[spec.code] = _parse_hsi_as_of(text)

                    live = client.get(
                        HSTECH_LIVE_URL,
                        params={"language": "schi", "indexCode": "02083.00"},
                    )
                    live.raise_for_status()
                    live_data = live.json().get("data") or {}
                    live_codes = {
                        str(row["stockCode"]).zfill(5)
                        for row in live_data.get("constituents", [])
                    }
                    if len(live_codes) != 10 or not live_codes.issubset(
                        constituents[spec.code]
                    ):
                        raise ValueError("恒生科技月度成份与官方实时前十成份不一致")
    except (KeyError, TypeError, xlrd.XLRDError) as exc:
        raise ValueError(f"无法解析官方指数成份数据：{exc}") from exc

    snapshot = build_index_snapshot(
        constituents,
        as_of=as_of,
        generated_at=now.isoformat(timespec="seconds"),
    )
    _write_snapshot(Path(output_path), snapshot)
    return snapshot


def _parse_hsi_as_of(text: str) -> str:
    match = re.search(
        r"所有数据截\S*?(\d{4})年(\d{1,2})\S*?(\d{1,2})\S*?",
        text,
    )
    if not match:
        raise ValueError("无法识别恒生科技成份文件日期")
    year, month, day = (int(value) for value in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def _write_snapshot(path: Path, snapshot: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
    except BaseException:
        Path(temporary_path).unlink(missing_ok=True)
        raise


def build_index_snapshot(
    constituents: Mapping[str, set[str]],
    *,
    as_of: Mapping[str, str],
    generated_at: str,
) -> dict[str, object]:
    memberships: dict[str, list[str]] = {}
    sources: list[dict[str, object]] = []
    for spec in INDEX_SPECS:
        codes = constituents.get(spec.code, set())
        if len(codes) != spec.expected_count:
            raise ValueError(
                f"{spec.name}样本数为 {len(codes)}，预期 {spec.expected_count}"
            )
        sources.append(
            {
                "name": spec.name,
                "code": spec.code,
                "market": spec.market,
                "as_of": as_of[spec.code],
                "constituent_count": len(codes),
                "source_url": spec.source_url,
            }
        )
        for code in codes:
            key = f"{spec.market}:{code}"
            memberships.setdefault(key, []).append(spec.name)
    return {
        "version": 1,
        "generated_at": generated_at,
        "sources": sources,
        "memberships": dict(sorted(memberships.items())),
    }
