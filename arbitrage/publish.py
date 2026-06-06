"""基金套利模块 · 发布到 GitHub Pages

把最新报告写成 index.html（Pages 首页永远是最新），并按日期存一份到 archive/，
然后 git 提交推送。任何设备访问 Pages URL 即看当天最新报告。

环境变量：
  GH_TOKEN      细粒度 PAT（Contents 读写），仅推送时临时注入 URL，不落盘到 remote
  GH_PAGES_REPO 形如 rygh5jm7nd-cpu/fund-arb-report
  GH_PAGES_DIR  本地克隆目录（默认 ../fund-arb-report）
未配置 GH_TOKEN/GH_PAGES_REPO 则跳过，返回 None。
"""
from __future__ import annotations

import os
import sys
import shutil
import subprocess
from datetime import datetime

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "fund-arb-report")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _run(args, cwd) -> tuple[bool, str]:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def _publish_actions(report_path: str) -> str | None:
    """在 GitHub Actions 里：只把文件写进工作区，提交交给 workflow 自己做。"""
    repo = os.getenv("GH_PAGES_REPO", "").strip()      # owner/name
    ws = os.getenv("GITHUB_WORKSPACE", ".")
    if not repo:
        return None
    shutil.copy(report_path, os.path.join(ws, "index.html"))
    archive_dir = os.path.join(ws, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    shutil.copy(report_path, os.path.join(archive_dir, os.path.basename(report_path)))
    owner, name = repo.split("/", 1)
    url = f"https://{owner}.github.io/{name}/"
    log(f"  ✓ 已写入工作区(index.html+archive)，将由 Actions 提交 → {url}")
    return url


def publish(report_path: str) -> str | None:
    """发布报告到 Pages，返回公网 URL；未配置或失败返回 None。"""
    if os.getenv("GITHUB_ACTIONS") == "true":
        return _publish_actions(report_path)

    token = os.getenv("GH_TOKEN", "").strip()
    repo = os.getenv("GH_PAGES_REPO", "").strip()      # owner/name
    pages_dir = os.getenv("GH_PAGES_DIR", DEFAULT_DIR)
    if not token or not repo:
        log("  · 未配置 GH_TOKEN/GH_PAGES_REPO，跳过 Pages 发布")
        return None
    if not os.path.isdir(os.path.join(pages_dir, ".git")):
        log(f"  ⚠ Pages 本地目录不是 git 仓库：{pages_dir}")
        return None

    owner, name = repo.split("/", 1)
    pages_url = f"https://{owner}.github.io/{name}/"

    # 1) 写 index.html（最新）+ archive 存档
    shutil.copy(report_path, os.path.join(pages_dir, "index.html"))
    archive_dir = os.path.join(pages_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    shutil.copy(report_path, os.path.join(archive_dir, os.path.basename(report_path)))

    # 2) git add / commit / push（token 临时注入 URL，不落盘）
    for ok, msg in [
        _run(["git", "add", "-A"], pages_dir),
        _run(["git", "-c", f"user.name={owner}",
              "-c", f"user.email={owner}@users.noreply.github.com",
              "commit", "-m", f"report {datetime.now():%Y-%m-%d %H:%M}"], pages_dir),
    ]:
        if not ok and "nothing to commit" not in msg:
            log(f"  ⚠ git 步骤异常：{msg[:120]}")

    push_url = f"https://{token}@github.com/{repo}.git"
    ok, msg = _run(["git", "push", push_url, "HEAD:main"], pages_dir)
    if ok:
        log(f"  ✓ 已发布到 GitHub Pages：{pages_url}")
        return pages_url
    log(f"  ⚠ Pages 推送失败：{msg[:150]}")
    return None
