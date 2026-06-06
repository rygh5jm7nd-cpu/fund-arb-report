"""基金套利模块 · 可转债转股套利扫描

转股套利逻辑：
  转股溢价率 = (转债价格 - 转股价值) / 转股价值 * 100
  当转股溢价率 < 0（转债价 < 转股价值）：
    买入转债 → 申请转股（T+1 到股）→ 卖出正股，赚取负溢价空间。
  毛收益 ≈ -转股溢价率；扣除转债佣金 + 正股佣金 + 正股印花税 + 冲击。

风险（必须知道）：
  · 转股 T+1 才到股，承担一夜正股波动；无融券对冲则是裸露敞口，不是无风险套利。
  · 须已进入转股期（开始转股日已过）；临近强赎/流动性差的券风险更高。

数据：东财可转债比价板块 b:MK0354（经可通镜像 push2delay）。
  字段：f12 转债代码 / f14 转债名 / f2 转债价 / f236 转股价值 /
        f237 转股溢价率 / f232 正股代码 / f234 正股名 / f242 开始转股日
"""
from __future__ import annotations

import sys
import time
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from . import config

EM_CLIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass
class CBArb:
    code: str               # 转债代码
    name: str               # 转债名
    price: float            # 转债最新价
    conv_value: float       # 转股价值
    premium: float          # 转股溢价率 %（负=套利机会）
    stock_code: str         # 正股代码
    stock_name: str         # 正股名
    net: float              # 扣成本预期净收益 %
    warnings: List[str] = field(default_factory=list)


def _fetch_cb() -> Optional[pd.DataFrame]:
    import requests

    def _do() -> pd.DataFrame:
        rows: list = []
        pn = 1
        while True:
            params = {
                "pn": str(pn), "pz": "100", "po": "1", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f243", "fs": "b:MK0354",
                "fields": "f12,f14,f2,f236,f237,f232,f234,f242",
            }
            d = requests.get(EM_CLIST_URL, params=params, timeout=15).json().get("data")
            if not d:
                break
            rows += d.get("diff", [])
            if len(rows) >= d.get("total", 0) or not d.get("diff"):
                break
            pn += 1
        return pd.DataFrame(rows)

    last = None
    for i in range(4):
        try:
            return _do()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  · 可转债比价第{i+1}次失败，重试… ({type(e).__name__})")
            time.sleep(2)
    log(f"  ⚠ 可转债比价多次失败，本次跳过（{last}）")
    return None


def _net_return(premium: float) -> float:
    c = config.CB_COST
    cost = c["转债佣金"] + c["正股佣金"] + c["正股印花税"] + c["冲击成本"]
    return round(-premium - cost, 3)      # premium 为负，-premium 为正空间


def scan() -> List[CBArb]:
    log("▶ 拉取可转债比价（转股溢价率）…")
    df = _fetch_cb()
    if df is None or df.empty:
        return []

    today = int(dt.date.today().strftime("%Y%m%d"))
    out: List[CBArb] = []
    for _, r in df.iterrows():
        price = pd.to_numeric(r.get("f2"), errors="coerce")
        prem = pd.to_numeric(r.get("f237"), errors="coerce")
        cv = pd.to_numeric(r.get("f236"), errors="coerce")
        start = pd.to_numeric(r.get("f242"), errors="coerce")
        if pd.isna(price) or pd.isna(prem):
            continue                       # 未上市/停牌（价格为 '-'）
        if pd.notna(start) and start > today:
            continue                       # 未进入转股期
        if prem >= config.CB_PREMIUM_THRESHOLD:
            continue                       # 溢价率不够负，无套利空间

        warns = ["转股T+1到股，承担隔夜正股波动；无融券对冲非无风险套利"]
        out.append(CBArb(
            code=str(r["f12"]), name=str(r["f14"]),
            price=float(price), conv_value=float(cv) if pd.notna(cv) else 0.0,
            premium=round(float(prem), 3),
            stock_code=str(r.get("f232", "")), stock_name=str(r.get("f234", "")),
            net=_net_return(float(prem)), warnings=warns,
        ))

    out.sort(key=lambda o: o.premium)      # 溢价率最负（机会最大）在前
    log(f"  ✓ 转股套利候选 {len(out)} 个（共扫描 {len(df)} 只转债）")
    return out
