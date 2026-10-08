"""Nasdaq-100 membership and SEC EDGAR financial disclosures."""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from cninfo_monitor.models import Report

NASDAQ_URL = 'https://api.nasdaq.com/api/quote/list-type/nasdaq100'
TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
SUBMISSIONS_URL = 'https://data.sec.gov/submissions/'
LOGGER = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')


class SecReader:
    def __init__(self, client: httpx.Client, user_agent: str):
        self.client = client
        self.user_agent = user_agent

    def get(self, url: str) -> httpx.Response:
        for attempt in range(4):
            # Sequential requests, including retries, stay below SEC's 10/s limit.
            time.sleep(.25)
            try:
                response = self.client.get(url, headers={'User-Agent': self.user_agent})
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if attempt == 3 or (status is not None and status not in {429, 500, 502, 503, 504}):
                    raise
                delay = (15, 45, 120)[attempt] * random.uniform(1, 1.2)
                LOGGER.warning('SEC request failed url=%s status=%s attempt=%s retry_in=%.1fs', url, status, attempt + 1, delay)
                time.sleep(delay)
        raise AssertionError('unreachable')


def map_companies(members: list[dict], tickers: dict) -> list[dict]:
    by_symbol = {r['ticker'].upper().replace('.', '-'): r for r in tickers.values()}
    companies: dict[int, dict] = {}
    for member in members:
        symbol = member['symbol'].upper()
        row = by_symbol.get(symbol.replace('.', '-'))
        if row is None:
            raise ValueError(f'Nasdaq constituent missing from SEC ticker mapping: {symbol}')
        cik = int(row['cik_str'])
        company = companies.setdefault(cik, {'cik': cik, 'name': row['title'], 'symbols': []})
        if symbol not in company['symbols']:
            company['symbols'].append(symbol)
    return list(companies.values())


def load_companies(reader: SecReader, path: Path, now: dt.datetime) -> list[dict]:
    if path.exists():
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        if snapshot['refreshed_on'] == now.date().isoformat():
            return snapshot['companies']
    response = reader.client.get(NASDAQ_URL, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    response.raise_for_status()
    data = response.json()['data']
    members = data['data']['rows']
    if len(members) < 95 or len(members) > 110 or int(data['totalrecords']) != len(members):
        raise ValueError('Incomplete Nasdaq-100 constituent response')
    source_date = dt.datetime.strptime(data['date'], '%b %d, %Y').date()
    if not 0 <= (now.date() - source_date).days <= 7:
        raise ValueError('Nasdaq-100 constituent source is stale')
    companies = map_companies(members, reader.get(TICKERS_URL).json())
    snapshot = {'refreshed_on': now.date().isoformat(), 'source_date': source_date.isoformat(),
                'source': NASDAQ_URL, 'companies': companies}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.nasdaq100-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return companies


def filing_report(row: dict, company: dict, text: str) -> Report | None:
    form = row['form']
    kind = {'10-Q': 'quarterly', '10-K': 'annual', '20-F': 'annual', '40-F': 'annual'}.get(form)
    if form == '8-K' and '2.02' in re.split(r'[,;\s]+', row.get('items', '')):
        kind = 'earnings'
    if form == '6-K':
        plain = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text)))
        # Require an actual results announcement and financial figures, not a meeting notice.
        released = re.search(r'\b(?:reports|announces|announced|reported)\b.{0,100}\b(?:financial results|quarter.{0,20}results|annual results|full.year results)\b', plain, re.I)
        if not released:
            released = re.search(
                r'\breports\s+[€$£]?\s*\d.{0,100}\b(?:net sales|revenue|net income)\b'
                r'.{0,100}\b(?:Q[1-4]|quarter|full.year)\b', plain, re.I,
            )
        figures = re.search(r'\b(?:revenue|net income|net loss|earnings per share)\b.{0,80}\d', plain, re.I)
        if released and figures:
            kind = 'earnings'
    if kind is None:
        return None
    accession = row['accessionNumber']
    period = row.get('reportDate') or row['filingDate']
    label = {'quarterly': '正式季报', 'annual': '正式年报', 'earnings': '业绩发布'}[kind]
    period_label = '报告期/事件日期' if kind == 'earnings' else '报告期末'
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/"
           f"{accession.replace('-', '')}/{quote(row['primaryDocument'], safe='')}")
    return Report(
        sec_code='/'.join(sorted(company['symbols'])), sec_name=company['name'],
        report_year=int(period[:4]), report_type=kind,
        title=f'{label}（{form}，{period_label} {period}）',
        disclosure_date=row['filingDate'], announcement_id=accession, pdf_url=url,
        market='us', index_names=('纳斯达克100',),
    )


def _rows(columns: dict) -> list[dict]:
    required = ('form', 'accessionNumber', 'filingDate', 'primaryDocument')
    count = len(columns['accessionNumber'])
    if any(len(columns[key]) != count for key in required):
        raise ValueError('Incomplete SEC submissions response')
    return [{key: values[i] for key, values in columns.items()} for i in range(count)]


def query_filings(reader: SecReader, companies: list[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
    results = {}
    for company in companies:
        data = reader.get(f"{SUBMISSIONS_URL}CIK{int(company['cik']):010d}.json").json()
        rows = _rows(data['filings']['recent'])
        for archive in data['filings']['files']:
            if archive['filingTo'] >= start.astimezone(ET).date().isoformat() and archive['filingFrom'] <= end.astimezone(ET).date().isoformat():
                rows.extend(_rows(reader.get(SUBMISSIONS_URL + quote(archive['name'], safe='')).json()))
        for row in rows:
            if row['form'] not in {'10-Q', '10-K', '20-F', '40-F', '8-K', '6-K'}:
                continue
            accepted = row.get('acceptanceDateTime')
            timestamp = dt.datetime.fromisoformat(accepted.replace('Z', '+00:00')) if accepted else dt.datetime.combine(dt.date.fromisoformat(row['filingDate']), dt.time(), ET)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=ET)
            if not start <= timestamp <= end:
                continue
            text = ''
            if row['form'] == '6-K':
                accession = row['accessionNumber']
                text = reader.get(f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/{accession}.txt").text
            report = filing_report(row, company, text)
            if report:
                results[report.announcement_id] = {
                    'announcementId': report.announcement_id,
                    'announcementTime': int(timestamp.timestamp() * 1000),
                    'report': asdict(report),
                }
    return list(results.values())


def fetch_us_announcements(config, start: dt.datetime, end: dt.datetime) -> list[dict]:
    with httpx.Client(timeout=config.request_timeout, follow_redirects=True) as client:
        reader = SecReader(client, config.sec_user_agent)
        companies = load_companies(reader, config.us_membership_path, end)
        # EDGAR may disseminate accepted filings the next business day.
        start = min(start, end - dt.timedelta(days=7))
        return query_filings(reader, companies, start, end)
