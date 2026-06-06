"""基金套利模块 · 国债逆回购（闲置资金现金管理 / 利率尖峰）

逆回购 = 把钱借出去收固定利息，到期资金+利息自动返还，几乎无风险（国债质押）。
"套利/机会"角度：
  · 月末、季末、年末、长假前，短期资金紧张，1~4 天逆回购年化常飙到 3%~10%+，
    远高于货币基金/活期，闲钱顺手做一笔就是确定性收益。
  · 注意"占款天数 vs 计息天数"：逆回购按自然日计息，但资金解冻看交易日。
    周四做 1 天期：占 1 天、计 1 天；周五做 1 天期：要下周一才解冻（占 3 天），
    年化被摊薄——周末前更划算的往往是「周四做1天」或直接做覆盖周末的品种。

净收益（每万元）≈ 本金 × 年化利率/100 × 计息天数/365 − 手续费(本金×费率)。

数据：东财逆回购板块 b:MK0356（沪 GC×××=204×××，深 R-×××=131×××），f2=年化利率%。
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from . import config

EM_CLIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass
class RepoRate:
    code: str
    name: str
    market: str             # 沪 / 深
    days: int               # 期限天数
    rate: float             # 年化利率 %
    fee: float              # 手续费率 %
    net_per_10k: float      # 每万元持有该期限的净收益（元）
    net_annualized: float   # 扣费后净年化 %
    highlight: bool         # 是否利率尖峰


def _fetch_repo() -> Optional[pd.DataFrame]:
    import requests

    def _do() -> pd.DataFrame:
        params = {
            "pn": "1", "pz": "50", "po": "0", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12", "fs": "b:MK0356",
            "fields": "f12,f14,f2,f13",
        }
        d = requests.get(EM_CLIST_URL, params=params, timeout=15).json().get("data")
        return pd.DataFrame(d.get("diff", [])) if d else pd.DataFrame()

    last = None
    for i in range(4):
        try:
            return _do()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  · 逆回购第{i+1}次失败，重试… ({type(e).__name__})")
            time.sleep(2)
    log(f"  ⚠ 逆回购多次失败，本次跳过（{last}）")
    return None


def _term_days(code: str) -> Optional[int]:
    suffix = code[-3:]
    return config.REPO_TERM_DAYS.get(suffix)


def scan() -> List[RepoRate]:
    log("▶ 拉取国债逆回购利率 …")
    df = _fetch_repo()
    if df is None or df.empty:
        return []

    out: List[RepoRate] = []
    for _, r in df.iterrows():
        code = str(r["f12"])
        rate = pd.to_numeric(r.get("f2"), errors="coerce")
        days = _term_days(code)
        if pd.isna(rate) or days is None:
            continue
        market = "沪" if code.startswith("204") else "深"
        fee = config.REPO_FEE.get(days, 0.01)
        gross = 10000 * (rate / 100) * days / 365      # 每万元利息
        net_amt = round(gross - 10000 * fee / 100, 2)  # 扣手续费
        net_ann = round((net_amt / 10000) / days * 365 * 100, 3)
        out.append(RepoRate(
            code=code, name=str(r["f14"]).replace("Ｒ", "R").replace("－", "-"),
            market=market, days=days, rate=round(float(rate), 3), fee=fee,
            net_per_10k=net_amt, net_annualized=net_ann,
            highlight=float(rate) >= config.REPO_HIGHLIGHT_RATE and days <= 4,
        ))

    # 排序：短期限优先、再按净年化降序（短期是现金管理主力）
    out.sort(key=lambda x: (x.days, -x.net_annualized))
    log(f"  ✓ 逆回购品种 {len(out)} 个"
        + ("，含利率尖峰机会" if any(x.highlight for x in out) else ""))
    return out
