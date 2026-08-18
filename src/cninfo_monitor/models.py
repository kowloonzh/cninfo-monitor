from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Report:
    sec_code: str
    sec_name: str
    report_year: int
    report_type: str
    title: str
    disclosure_date: str
    announcement_id: str
    pdf_url: str
    market: str = "mainland"
    total_market_cap: str | None = None
    index_names: tuple[str, ...] = ()

    @property
    def company_report_key(self) -> str:
        if self.market != "mainland":
            return f"{self.market}:{self.report_year}:{self.report_type}:{self.sec_code}"
        return f"{self.report_year}:{self.report_type}:{self.sec_code}"
