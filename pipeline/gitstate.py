"""把資料存在 repo 的獨立分支（state / probe-results），每次只保留一個 commit，repo 不會越長越大。"""
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _git(*args, cwd=ROOT, check=True):
    print('$ git', ' '.join(args), flush=True)
    return subprocess.run(['git', *args], cwd=cwd, check=check, text=True,
                          capture_output=True)


def open_branch(branch: str, folder: str) -> pathlib.Path:
    """把 branch 的最新內容放到 ROOT/folder；分支不存在就建一個空的。"""
    path = ROOT / folder
    if path.exists():
        _git('worktree', 'remove', '--force', str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
    fetched = _git('fetch', '--depth=1', 'origin', f'{branch}:refs/remotes/origin/{branch}',
                   check=False).returncode == 0
    if fetched:
        _git('worktree', 'add', '--detach', str(path), f'origin/{branch}')
    else:
        _git('worktree', 'add', '--detach', str(path), 'HEAD')
        _git('checkout', '--orphan', f'tmp-{branch}', cwd=path)
        _git('rm', '-rf', '-q', '.', cwd=path, check=False)
        for p in path.iterdir():
            if p.name != '.git':
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    return path


def save_branch(branch: str, folder: str, message: str) -> bool:
    """把資料夾內容存成 branch 唯一的 commit 並強制推上去。"""
    path = ROOT / folder
    _git('config', 'user.name', 'echo-bot', cwd=path)
    _git('config', 'user.email', 'echo-bot@users.noreply.github.com', cwd=path)
    _git('checkout', '--orphan', f'pub-{branch}', cwd=path, check=False)
    _git('add', '-A', cwd=path)
    _git('commit', '-q', '-m', message, cwd=path, check=False)
    res = _git('push', '--force', 'origin', f'HEAD:refs/heads/{branch}', cwd=path, check=False)
    print(res.stdout, res.stderr, flush=True)
    return res.returncode == 0
