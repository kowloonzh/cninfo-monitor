import datetime as dt
from dataclasses import replace

import httpx
import pytest

from cninfo_monitor import sec
from cninfo_monitor.models import Report
from cninfo_monitor.monitor import format_digest, JsonStateStore, run_monitor

NOW = dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc)


def filing(form='10-Q', accession='0000000001-26-000001', **extra):
    return dict(form=form, accessionNumber=accession, filingDate='2026-10-07',
                acceptanceDateTime='2026-10-07T20:00:00Z', reportDate='2026-09-30',
                primaryDocument='report.htm', items='', **extra)


def columns(rows):
    return {key: [r[key] for r in rows] for key in rows[0]}


def test_official_membership_maps_all_symbols_and_merges_share_classes():
    members = [{'symbol': 'GOOG'}, {'symbol': 'GOOGL'}, {'symbol': 'AAPL'}]
    tickers = {'0': {'ticker': 'GOOG', 'cik_str': 1, 'title': 'Alphabet'},
               '1': {'ticker': 'GOOGL', 'cik_str': 1, 'title': 'Alphabet'},
               '2': {'ticker': 'AAPL', 'cik_str': 2, 'title': 'Apple'}}
    mapped = sec.map_companies(members, tickers)
    assert len(mapped) == 2
    assert mapped[0]['symbols'] == ['GOOG', 'GOOGL']
    with pytest.raises(ValueError, match='MISSING'):
        sec.map_companies([{'symbol': 'MISSING'}], tickers)


@pytest.mark.parametrize('form,kind', [('10-Q','quarterly'), ('10-K','annual'), ('20-F','annual'), ('40-F','annual'), ('8-K','earnings')])
def test_classifies_reports_and_keeps_fiscal_period(form, kind):
    row = filing(form)
    if form == '8-K': row['items'] = '2.02,9.01'
    report = sec.filing_report(row, {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, '')
    assert report.report_type == kind
    assert '2026-09-30' in report.title
    assert report.market == 'us'
    assert report.pdf_url == 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/report.htm'


@pytest.mark.parametrize('form,items,text,selected', [
    ('8-K', '5.02,9.01', '', False),
    ('10-Q/A', '', '', False),
    ('6-K', '', '<h1>Company Reports Third Quarter 2026 Financial Results</h1><p>Revenue was $10 million. Net income was $2 million.</p>', True),
    ('6-K', '', 'Company will announce third quarter financial results next month.', False),
    ('6-K', '', 'Notice of annual general meeting', False),
])
def test_filters_unrelated_foreign_filings_and_amendments(form, items, text, selected):
    row = filing(form); row['items'] = items
    result = sec.filing_report(row, {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, text)
    assert (result is not None) is selected


def test_us_filings_are_deduplicated_by_accession_and_baselined(tmp_path):
    a = sec.filing_report(filing(), {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, '')
    b = replace(a, announcement_id='second', title='Next quarter')
    assert a.company_report_key != b.company_report_key
    state = JsonStateStore(tmp_path / 'state.json')
    sent = []
    run_monitor([a], state, sender=lambda s: sent.append(s) or True, bootstrap_silently=True, markets=['us'])
    assert not sent
    run_monitor([a,b], state, sender=lambda s: sent.append(s) or True, bootstrap_silently=True, markets=['us'])
    assert len(sent) == 1
    assert '美股' in sent[0] and '纳斯达克100' in sent[0]
    assert '总市值' not in sent[0]


def test_sec_client_identifies_limits_retries_and_rejects_403(monkeypatch):
    waits = []
    monkeypatch.setattr(sec.time, 'sleep', waits.append)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={'ok': True})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = sec.SecReader(client, 'monitor test@example.com')
        assert reader.get('https://data.sec.gov/test').json() == {'ok': True}
    assert all(r.headers['User-Agent'] == 'monitor test@example.com' for r in calls)
    assert len(waits) >= 2 and waits[0] >= .2
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            sec.SecReader(client, 'monitor test@example.com').get('https://data.sec.gov/test')


def test_query_reads_historical_files_and_filters_time(monkeypatch):
    monkeypatch.setattr(sec.time, 'sleep', lambda _: None)
    paths = []
    recent = filing('8-K'); recent['items'] = '5.02'
    old = filing(); old['acceptanceDateTime'] = '2026-10-06T20:00:00Z'
    future = filing(accession='0000000001-26-000003'); future['acceptanceDateTime'] = '2026-10-09T20:00:00Z'
    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith('CIK0000000001.json'):
            return httpx.Response(200, json={'filings': {'recent': columns([recent, future]), 'files': [
                {'name': 'CIK0000000001-submissions-001.json', 'filingFrom': '2026-10-01', 'filingTo': '2026-10-06'}]}})
        return httpx.Response(200, json=columns([old]))
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = sec.query_filings(sec.SecReader(client, 'monitor test@example.com'),
            [{'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}], NOW-dt.timedelta(days=3), NOW)
    assert len(result) == 1
    assert result[0]['report']['report_type'] == 'quarterly'
    assert len(paths) == 2


def test_membership_refresh_is_daily_and_incomplete_refresh_preserves_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sec.time, 'sleep', lambda _: None)
    members = [{'symbol': f'S{i}'} for i in range(100)]
    tickers = {str(i): {'ticker': f'S{i}', 'cik_str': i+1, 'title': f'Company {i}'} for i in range(100)}
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.host == 'api.nasdaq.com':
            return httpx.Response(200, json={'data': {'date': 'Oct 7, 2026', 'totalrecords':100, 'data': {'rows':members}}})
        return httpx.Response(200, json=tickers)
    path = tmp_path / 'members.json'
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = sec.SecReader(client, 'test test@example.com')
        assert len(sec.load_companies(reader, path, NOW)) == 100
        assert len(sec.load_companies(reader, path, NOW)) == 100
        assert len(calls) == 2
        original = path.read_bytes()
        members.pop()
        with pytest.raises(ValueError, match='Incomplete'):
            sec.load_companies(reader, path, NOW+dt.timedelta(days=1))
        assert path.read_bytes() == original


def test_us_digest_counts_disclosures_not_companies():
    a = sec.filing_report(filing(), {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, '')
    b = replace(a, announcement_id='b', report_type='earnings')
    assert format_digest([a,b]).startswith('巨潮财报监控：新披露 2 份')


def test_6k_does_not_match_planned_results_with_past_revenue():
    text = 'Company will announce third quarter financial results next month. Last year revenue was $10 million.'
    assert sec.filing_report(filing('6-K'), {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, text) is None


def test_6k_recognizes_numeric_results_headline():
    text = '<h1>Example reports €9 billion total net sales and €2 billion net income in Q2 2026</h1>'
    report = sec.filing_report(filing('6-K'), {'cik': 1, 'symbols': ['TEST'], 'name': 'Test'}, text)
    assert report is not None and report.report_type == 'earnings'


def test_fetch_rechecks_seven_days_but_preserves_older_outage_window(tmp_path, monkeypatch):
    from cninfo_monitor.config import MonitorConfig
    windows = []
    monkeypatch.setattr(sec, 'load_companies', lambda *args: [])
    monkeypatch.setattr(sec, 'query_filings', lambda reader, companies, start, end: windows.append((start,end)) or [])
    class Config:
        request_timeout = 20
        sec_user_agent = 'monitor test@example.com'
        us_membership_path = tmp_path / 'members.json'
    sec.fetch_us_announcements(Config(), NOW-dt.timedelta(hours=1), NOW)
    sec.fetch_us_announcements(Config(), NOW-dt.timedelta(days=30), NOW)
    assert windows == [(NOW-dt.timedelta(days=7),NOW), (NOW-dt.timedelta(days=30),NOW)]
