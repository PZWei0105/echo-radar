"""離線跑一整輪：用 tests/fixtures 錄好的原始回應當作網路資料，輸出 App 資料到指定資料夾（不連網）。

用法：python3 tests/local_build.py <輸出資料夾> [state 資料夾]
- 沒給 state 資料夾：複製 tests/fixtures/state（2026-10-20 的真實狀態）到 <輸出資料夾>-state 再跑。
- 時間固定在 FIXED_T0（種子狀態的時間點），每次跑出來的題目都一樣。
"""
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import build  # noqa: E402
from pipeline.util import parse_iso  # noqa: E402
from tests.fake_http import FakeHttp  # noqa: E402

FIXED_T0 = '2026-10-20T10:47:00Z'
SEED = ROOT / 'tests' / 'fixtures' / 'state'


def run(out: pathlib.Path, state: pathlib.Path | None = None, t0: str = FIXED_T0, quiet=False):
    if state is None:
        state = out.parent / (out.name + '-state')
        if state.exists():
            shutil.rmtree(state)
        shutil.copytree(SEED, state)
    state.mkdir(parents=True, exist_ok=True)
    build_id = build.copy_site(out)
    http = FakeHttp()
    _sleep = time.sleep
    time.sleep = lambda s: None  # 離線測試不用等
    t_start = time.time()
    try:
        outputs = build.execute(http, build.State(state), parse_iso(t0), out / 'data', build_id, {})
    finally:
        time.sleep = _sleep
    if not quiet:
        r = outputs['radar.json']
        print('topics', len(r['topics']), 'secs', round(time.time() - t_start, 1), 'calls', http.calls)
        for t in r['topics'][:25]:
            s = t['scores']
            print(f"{t['score']:5.1f} {t['grade']} [{t['type_label']}] {t['title'][:40]}  "
                  f"D{s['demand']:.0f} T{s['timing']:.0f} F{s['fit']:.0f} E{s['ease']:.0f}  rise{t['rising']}")
        print('health errors', {k: v.get('err') for k, v in outputs['meta.json']['health'].items() if v.get('err')})
        print('timings', outputs['meta.json']['stats']['timings'])
    return outputs, http


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None)
