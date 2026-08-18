from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from cninfo_monitor.models import Report


QUOTE_URL = "https://qt.gtimg.cn/q="
QUOTE_BATCH_SIZE = 50
TOTAL_MARKET_CAP_FIELD = 45
_QUOTE_ROW = re.compile(r'v_(?P<symbol>[a-z0-9]+)="(?P<fields>[^"]*)";')


def quote_symbol(report: Report) -> str:
    if report.market == "hong_kong":
        return f"hk{report.sec_code}"
    if report.sec_code.startswith(("43", "83", "87", "92")):
        return f"bj{report.sec_code}"
    if report.sec_code.startswith(("5", "6", "9")):
        return f"sh{report.sec_code}"
    return f"sz{report.sec_code}"


def enrich_reports_with_market_caps(
    client: Any,
    reports: Iterable[Report],
) -> tuple[Report, ...]:
    rows = tuple(reports)
    symbols = list(dict.fromkeys(quote_symbol(report) for report in rows))
    market_caps: dict[str, str] = {}

    for offset in range(0, len(symbols), QUOTE_BATCH_SIZE):
        batch = symbols[offset : offset + QUOTE_BATCH_SIZE]
        try:
            response = client.get(QUOTE_URL + ",".join(batch))
            response.raise_for_status()
            text = response.content.decode("gbk")
            market_caps.update(_parse_market_caps(text))
        except Exception:
            continue

    return tuple(
        replace(
            report,
            total_market_cap=market_caps.get(quote_symbol(report)),
        )
        for report in rows
    )


def _parse_market_caps(text: str) -> dict[str, str]:
    market_caps: dict[str, str] = {}
    for match in _QUOTE_ROW.finditer(text):
        symbol = match.group("symbol")
        fields = match.group("fields").split("~")
        if len(fields) <= TOTAL_MARKET_CAP_FIELD:
            continue
        try:
            value = Decimal(fields[TOTAL_MARKET_CAP_FIELD])
        except InvalidOperation:
            continue
        if value <= 0:
            continue
        unit = "亿港元" if symbol.startswith("hk") or "HKD" in fields else "亿元"
        market_caps[symbol] = f"{value:.2f}{unit}"
    return market_caps
