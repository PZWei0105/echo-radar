"""開發用：實際抓一輪候選資料來源，把原始回應存到 probe-results 分支供檢查。"""
import datetime as dt
import json
import pathlib
import platform
import shutil
import time

import requests

from . import gitstate
from .util import UA_BOT, UA_BROWSER

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_MAX = 3_000_000


def run():
    targets = json.loads((ROOT / 'control' / 'probe.json').read_text(encoding='utf-8'))
    out = gitstate.open_branch('probe-results', '.probe')
    for p in list(out.iterdir()):
        if p.name != '.git':
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (out / 'raw').mkdir(exist_ok=True)

    session = requests.Session()
    summary = {'started': dt.datetime.now(dt.timezone.utc).isoformat(),
               'python': platform.python_version(), 'results': []}

    for t in targets:
        name = t['name']
        meta = {'name': name, 'url': t['url']}
        headers = {'User-Agent': UA_BOT if t.get('bot_ua') else UA_BROWSER,
                   'Accept': '*/*', 'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8'}
        headers.update(t.get('headers') or {})
        t0 = time.time()
        try:
            r = session.get(t['url'], headers=headers, timeout=t.get('timeout', 40))
            raw = r.content
            meta.update(status=r.status_code, final_url=r.url, size=len(raw),
                        content_type=r.headers.get('content-type'),
                        retry_after=r.headers.get('retry-after'))
            (out / 'raw' / f'{name}.txt').write_bytes(raw[:t.get('max', DEFAULT_MAX)])
        except Exception as e:  # noqa: BLE001 — 開發探測，記錄所有錯誤
            meta['error'] = repr(e)[:500]
        meta['elapsed'] = round(time.time() - t0, 2)
        summary['results'].append(meta)
        print(json.dumps(meta, ensure_ascii=False)[:300], flush=True)
        time.sleep(t.get('sleep', 1.2))

    summary['finished'] = dt.datetime.now(dt.timezone.utc).isoformat()
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                      encoding='utf-8')
    gitstate.save_branch('probe-results', '.probe', 'probe ' + summary['finished'])
