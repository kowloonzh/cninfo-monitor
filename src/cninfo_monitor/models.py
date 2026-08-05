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

    @property
    def company_report_key(self) -> str:
        return f"{self.report_year}:{self.report_type}:{self.sec_code}"
