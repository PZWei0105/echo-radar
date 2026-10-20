"""共用小工具：連線、時間、文字清理。"""
import datetime as dt
import hashlib
import html
import json
import math
import re
import time

import requests

UTC = dt.timezone.utc
TW = dt.timezone(dt.timedelta(hours=8))
UA_BROWSER = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/141.0 Safari/537.36')
UA_BOT = 'EchoRadar/1.0 (personal topic radar for a game channel; https://github.com/PZWei0105/echo-radar)'


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    if not s:
        return None
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    try:
        return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    except ValueError:
        try:
            t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
            return t if t.tzinfo else t.replace(tzinfo=UTC)
        except ValueError:
            return None


def hours_since(t, ref: dt.datetime | None = None) -> float:
    t = parse_iso(t)
    if t is None:
        return 1e9
    return ((ref or now()) - t).total_seconds() / 3600


def tw_date(t: dt.datetime) -> dt.date:
    return t.astimezone(TW).date()


def parse_day(s):
    """'2026-10-22' → date；只有年月或其他格式回傳 None。"""
    if not s or not isinstance(s, str):
        return None
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', s.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


class Http:
    """所有對外連線都走這裡：固定 User-Agent、逾時、遇到 5xx/429 重試一次。"""

    def __init__(self):
        self.s = requests.Session()
        self.calls = 0
        self.by_host = {}

    def get(self, url, *, bot=False, headers=None, timeout=30, retries=1):
        h = {'User-Agent': UA_BOT if bot else UA_BROWSER, 'Accept': '*/*',
             'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8'}
        h.update(headers or {})
        host = re.sub(r'^https?://([^/]+).*$', r'\1', url)
        last = None
        for attempt in range(retries + 1):
            self.calls += 1
            self.by_host[host] = self.by_host.get(host, 0) + 1
            try:
                r = self.s.get(url, headers=h, timeout=timeout)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    last = RuntimeError(f'HTTP {r.status_code}')
                    time.sleep(3 + attempt * 4)
                    continue
                return r
            except requests.RequestException as e:
                last = e
                if attempt < retries:
                    time.sleep(3 + attempt * 4)
        raise last

    def json(self, url, **kw):
        r = self.get(url, **kw)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}')
        return r.json()


# ---------- 文字 ----------
_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')


def strip_html(s: str) -> str:
    s = _TAG.sub(' ', s or '')
    return _WS.sub(' ', html.unescape(s)).strip()


def clip(s: str, n: int) -> str:
    s = (s or '').strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + '…'


def sid(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode('utf-8')).hexdigest()[:n]


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def log_scale(v, lo, hi):
    """把 v 依對數刻度放到 0–100：v<=lo → 0，v>=hi → 100。"""
    if v is None or v <= 0:
        return 0.0
    if v <= lo:
        return 0.0
    return clamp(100 * math.log(v / lo) / math.log(hi / lo))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
