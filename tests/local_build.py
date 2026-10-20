"""離線跑一整輪：用 probe 的原始回應當作網路資料，輸出 App 資料到指定資料夾。

用法：python3 tests/local_build.py [輸出資料夾] [state 資料夾]
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline import build  # noqa: E402
from pipeline.util import now  # noqa: E402
from tests.fake_http import FakeHttp  # noqa: E402

SP = '/tmp/claude-0/-home-claude/cee980f9-5e38-5530-b4df-135bdea4688a/scratchpad'
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else f'{SP}/public')
state = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else f'{SP}/state')
state.mkdir(parents=True, exist_ok=True)
build_id = build.copy_site(out)
http = FakeHttp()
_sleep = time.sleep
time.sleep = lambda s: None  # 離線測試不用等
t0 = time.time()
outputs = build.execute(http, build.State(state), now(), out / 'data', build_id, {})
time.sleep = _sleep
r = outputs['radar.json']
print('topics', len(r['topics']), 'secs', round(time.time() - t0, 1), 'calls', http.calls)
for t in r['topics'][:25]:
    s = t['scores']
    print(f"{t['score']:5.1f} {t['grade']} [{t['type_label']}] {t['title'][:40]}  D{s['demand']:.0f} T{s['timing']:.0f} F{s['fit']:.0f} E{s['ease']:.0f}  rise{t['rising']}")
print('health errors', {k: v.get('err') for k, v in outputs['meta.json']['health'].items() if v.get('err')})
print('timings', outputs['meta.json']['stats']['timings'])
