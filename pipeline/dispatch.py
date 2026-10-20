"""GitHub Actions 進入點：依觸發方式決定這一輪要做什麼。"""
import datetime as dt
import json
import os
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
TW = dt.timezone(dt.timedelta(hours=8))


def decide_mode() -> str:
    event = os.environ.get('EVENT', '')
    wanted = (os.environ.get('INPUT_MODE') or '').strip()
    if event == 'workflow_dispatch' and wanted:
        return wanted
    if event == 'push':
        job = json.loads((ROOT / 'control' / 'job.json').read_text(encoding='utf-8'))
        return job.get('job', 'build')
    return 'build'


def set_output(key: str, value: str):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f'{key}={value}\n')
    print(f'[output] {key}={value}', flush=True)


def main():
    mode = decide_mode()
    if '--print-mode' in sys.argv:
        set_output('mode', mode)
        return
    print(f'[echo] mode={mode} at {dt.datetime.now(TW).isoformat()}', flush=True)
    deploy = False
    try:
        if mode == 'probe':
            from . import probe
            probe.run()
        else:
            try:
                from . import build
            except ImportError:
                print('[echo] build 模組尚未就緒，這輪略過', flush=True)
            else:
                deploy = bool(build.run(mode))
    except Exception:  # noqa: BLE001 — 任何錯誤都要留下紀錄，不讓排程整個中斷
        traceback.print_exc()
    set_output('deploy', 'true' if deploy else 'false')


if __name__ == '__main__':
    main()
