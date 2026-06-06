"""基金套利模块 · 消息推送（飞书 / 微信 Server酱）

按环境变量自动选择渠道（都配则都发）：
  · 飞书群自定义机器人： FEISHU_WEBHOOK（必填）   FEISHU_SECRET（可选，开了"加签"才需要）
  · 微信 Server酱：       SC_SENDKEY
都没配置则静默跳过（本地手动跑不受影响）。

飞书 webhook 获取：飞书群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 地址。
（想私聊只发给自己：建一个只有你自己的群，在里面加机器人即可。）
"""
from __future__ import annotations

import os
import sys
import json
import time
import hmac
import base64
import hashlib
from datetime import datetime
from typing import List

from .scanner import ArbOpportunity
from .convertible import CBArb
from .reverse_repo import RepoRate


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# 汇总当日 Top 机会（各渠道复用）
# ---------------------------------------------------------------------------
def _collect(opportunities: List[ArbOpportunity], cb_list: List[CBArb],
             repo_list: List[RepoRate]):
    main = [o for o in opportunities if o.executable][:8]
    cb = cb_list[:5]
    repo_hot = [o for o in repo_list if o.highlight][:5]
    return main, cb, repo_hot


def _title(main) -> str:
    top = main[0] if main else None
    return (f"📈 基金套利日报 {datetime.now():%m-%d}"
            + (f"｜{top.name} {top.net:+.1f}%" if top else "｜今日无显著机会"))


# ---------------------------------------------------------------------------
# 飞书交互卡片
# ---------------------------------------------------------------------------
def _feishu_card(main, cb, repo_hot, meta, report_path) -> dict:
    def section(title, lines):
        if not lines:
            return []
        body = "\n".join(lines)
        return [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}**\n{body}"}},
                {"tag": "hr"}]

    elements = [{"tag": "div", "text": {"tag": "lark_md",
                 "content": f"数据时间 {meta.get('更新时间','')}"}}, {"tag": "hr"}]
    elements += section("① 基金折溢价（主战场）", [
        f"{o.name}（{o.code}）· {o.direction} · <font color='red'>**{o.net:+.2f}%**</font>" for o in main])
    elements += section("③ 可转债转股套利", [
        f"{o.name}（{o.code}）· 溢价{o.premium:+.2f}% · **{o.net:+.2f}%**" for o in cb])
    if repo_hot:
        elements += section("④ 逆回购利率尖峰 ⚡", [
            f"{o.name} · 年化 **{o.rate:.2f}%**" for o in repo_hot])
    elements.append({"tag": "note", "elements": [{"tag": "lark_md",
        "content": "⚠️ 研究参考非投资建议；溢价需确认限购，LOF为T-1口径，转股套利含隔夜敞口"}]})
    link_md = (f"[📄 点开完整报告（任意设备）]({report_path})"
               if report_path.startswith("http") else f"完整报告：{report_path}")
    elements.append({"tag": "note", "elements": [{"tag": "lark_md", "content": link_md}]})

    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": _title(main)},
                       "template": "blue"},
            "elements": elements,
        },
    }


def _push_feishu(main, cb, repo_hot, meta, report_path) -> bool:
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        return False
    import requests
    payload = _feishu_card(main, cb, repo_hot, meta, report_path)

    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:                               # 开了"加签"校验
        ts = str(int(time.time()))
        sign = base64.b64encode(hmac.new(
            f"{ts}\n{secret}".encode("utf-8"), digestmod=hashlib.sha256).digest()).decode()
        payload = {**payload, "timestamp": ts, "sign": sign}

    try:
        r = requests.post(webhook, json=payload, timeout=15)
        j = r.json()
        # 飞书成功：新版 {"code":0,...} 或旧版 {"StatusCode":0,...}
        ok = j.get("code", j.get("StatusCode", -1)) == 0
        log("  ✓ 飞书推送成功" if ok else f"  ⚠ 飞书推送返回异常：{r.text[:150]}")
        return ok
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ 飞书推送失败：{type(e).__name__} {e}")
        return False


# ---------------------------------------------------------------------------
# 微信 Server酱
# ---------------------------------------------------------------------------
def _push_serverchan(main, cb, repo_hot, meta, report_path) -> bool:
    sendkey = os.getenv("SC_SENDKEY", "").strip()
    if not sendkey:
        return False
    import requests
    lines = [f"**数据时间** {meta.get('更新时间','')}", ""]
    if main:
        lines += ["### ① 基金折溢价", "", "| 标的 | 方向 | 净收益 |", "|---|---|---|"]
        lines += [f"| {o.name}({o.code}) | {o.direction} | **{o.net:+.2f}%** |" for o in main]
    if cb:
        lines += ["", "### ③ 可转债转股套利", "", "| 转债 | 溢价 | 净收益 |", "|---|---|---|"]
        lines += [f"| {o.name}({o.code}) | {o.premium:+.2f}% | **{o.net:+.2f}%** |" for o in cb]
    if repo_hot:
        lines += ["", "### ④ 逆回购尖峰", "", "| 品种 | 年化 |", "|---|---|"]
        lines += [f"| {o.name} | **{o.rate:.2f}%** |" for o in repo_hot]
    link_md = f"[点开完整报告]({report_path})" if report_path.startswith("http") else f"报告：`{report_path}`"
    lines += ["", "---", "⚠️ 研究参考非投资建议。", link_md]
    try:
        r = requests.post(f"https://sctapi.ftqq.com/{sendkey}.send",
                          data={"title": _title(main)[:100], "desp": "\n".join(lines)}, timeout=15)
        ok = r.json().get("code") == 0
        log("  ✓ 微信推送成功" if ok else f"  ⚠ 微信推送返回异常：{r.text[:120]}")
        return ok
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ 微信推送失败：{type(e).__name__} {e}")
        return False


def push(opportunities: List[ArbOpportunity], cb_list: List[CBArb],
         repo_list: List[RepoRate], meta: dict, report_path: str) -> bool:
    """按已配置渠道推送。任一成功返回 True；都未配置则跳过。"""
    main, cb, repo_hot = _collect(opportunities, cb_list, repo_list)
    sent = False
    if os.getenv("FEISHU_WEBHOOK", "").strip():
        sent = _push_feishu(main, cb, repo_hot, meta, report_path) or sent
    if os.getenv("SC_SENDKEY", "").strip():
        sent = _push_serverchan(main, cb, repo_hot, meta, report_path) or sent
    if not sent:
        log("  · 未配置推送渠道（FEISHU_WEBHOOK / SC_SENDKEY），跳过")
    return sent
