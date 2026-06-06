"""基金套利模块 · 扫描引擎

流程：
  1. 拉取全市场 ETF（含实时 IOPV）+ LOF（仅 T-1 折价率）行情
  2. 计算溢价率、按名称分类
  3. 套利方向与预期净收益（扣成本）
  4. 按预期收益排序，输出候选清单

折溢价口径（与东财一致）：
  折价率 = (净值 - 市价) / 净值 * 100
  溢价率 = -折价率           # 正=溢价(市价高于净值)，负=折价
  · ETF：净值取实时 IOPV（盘中估值），可视为实时折溢价
  · LOF：东财不提供盘中 IOPV，折价率基于「昨收净值(T-1)」，已含当日标的涨跌，
         不是实时无风险套利信号，套利需自行估算当日净值变动（与集思录 T-1 溢价率同口径）
套利方向：
  溢价 → 场外按净值申购份额，场内高价卖出，赚溢价
  折价 → 场内低价买入份额，按净值赎回，赚折价
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from . import config

# 东财 clist 接口镜像。akshare 自带的 LOF 接口写死走 88.push2，常被本地代理拦截；
# push2delay 这个镜像同参数可通（ETF 接口用的也是它）。
EM_CLIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass
class ArbOpportunity:
    code: str
    name: str
    kind: str                   # 品种：ETF / LOF
    category: str
    price: float                # 场内最新价
    iopv: Optional[float]       # 实时净值估算（LOF 无，为 None）
    basis: str                  # 折溢价基准：实时IOPV / 昨收净值T-1
    premium: float              # 溢价率 %（正=溢价，负=折价）
    turnover: float             # 成交额(元)
    direction: str              # 溢价套利 / 折价套利
    gross: float                # 账面毛收益率 %（=|折溢价|）
    net: float                  # 扣成本后预期净收益 %
    executable: bool            # 是否属于可场外申赎套利（散户可操作）
    warnings: List[str] = field(default_factory=list)


def _classify(name: str) -> str:
    for cat, pattern in config.CATEGORY_RULES:
        if re.search(pattern, name):
            return cat
    return "境内"


def _fetch_with_retry(fn, label: str, retries: int = 4, required: bool = False):
    """带重试地调用行情接口（东财 push 主机经代理偶发抽风）。

    required=False 时失败返回 None（优雅降级），True 时抛错。
    """
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 网络层异常，重试
            last = e
            log(f"  · {label}第{i+1}次失败，重试… ({type(e).__name__})")
            time.sleep(2)
    if required:
        raise RuntimeError(f"{label}拉取失败：{last}")
    log(f"  ⚠ {label}多次失败，本次跳过（代理/网络问题）")
    return None


def _fetch_etf_spot() -> pd.DataFrame:
    """ETF 实时行情，含实时 IOPV 与折价率（akshare 自带接口走可通主机）。"""
    import akshare as ak
    return _fetch_with_retry(ak.fund_etf_spot_em, "ETF行情", required=True)


def _fetch_lof_spot() -> Optional[pd.DataFrame]:
    """LOF 行情：自定义请求可通镜像，并补取 akshare 未映射的折价率字段 f402。

    字段：f12 代码 / f14 名称 / f2 最新价 / f402 折价率(T-1) / f6 成交额。
    LOF 无盘中 IOPV，IOPV 列填空。
    """
    import requests

    def _do() -> pd.DataFrame:
        rows: list = []
        pn = 1
        while True:
            params = {
                "pn": str(pn), "pz": "100", "po": "1", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "wbp2u": "|0|0|0|web", "fid": "f3",
                "fs": "b:MK0404,b:MK0405,b:MK0406,b:MK0407",
                "fields": "f12,f14,f2,f402,f6",
            }
            d = requests.get(EM_CLIST_URL, params=params, timeout=15).json().get("data")
            if not d:
                break
            rows += d.get("diff", [])
            if len(rows) >= d.get("total", 0) or not d.get("diff"):
                break
            pn += 1
        df = pd.DataFrame(rows).rename(columns={
            "f12": "代码", "f14": "名称", "f2": "最新价",
            "f402": "基金折价率", "f6": "成交额",
        })
        df["IOPV实时估值"] = pd.NA      # LOF 无盘中 IOPV
        return df

    return _fetch_with_retry(_do, "LOF行情", required=False)


def _net_premium_return(premium: float) -> float:
    """溢价套利：申购份额→卖出。净收益 = 溢价 - 申购费 - 卖出佣金 - 冲击。"""
    c = config.COST
    return premium - c["申购费率"] - c["卖出佣金"] - c["冲击成本"]


def _net_discount_return(discount: float) -> float:
    """折价套利：买入份额→赎回。净收益 = 折价 - 买入佣金 - 赎回费 - 冲击。"""
    c = config.COST
    return discount - c["买入佣金"] - c["赎回费率"] - c["冲击成本"]


def _eval_row(r, kind: str) -> Optional[ArbOpportunity]:
    """对单条行情判定是否构成套利机会。kind: ETF / LOF。"""
    prem = float(r["溢价率"])
    turnover = float(r["成交额"] or 0)
    threshold = config.MIN_TURNOVER_LOF if kind == "LOF" else config.MIN_TURNOVER
    if turnover < threshold:                    # 流动性门槛
        return None

    cat = r["类别"]
    # 境内主动权益/定开/封闭 LOF：场内价与净值长期背离、净值非实时或封闭期不可赎回，
    # T-1 折价率不是套利信号，直接剔除（否则会冒出 50% 的假"溢价"）
    if kind == "LOF" and cat not in config.ARBITRAGEABLE:
        return None
    # 可执行性：可申赎套利的 LOF（商品/QDII/跨境）全部可做；ETF 仅这些类别可执行，
    # 境内股票 ETF 申赎需一篮子股票，散户门槛高 → 标为不可执行
    executable = (kind == "LOF") or (cat in config.ARBITRAGEABLE)
    basis = "实时IOPV" if kind == "ETF" else "昨收净值T-1"

    warns: List[str] = []
    if prem >= config.PREMIUM_THRESHOLD:
        direction, gross = "溢价套利", prem
        net = _net_premium_return(prem)
        if prem >= config.HIGH_PREMIUM_WARN:
            warns.append(f"溢价已达{prem:.1f}%，透支严重、回落风险高")
        if executable:
            warns.append("需场外可申购且未限购；份额T+N到账，期间承担净值波动")
        else:
            warns.append("境内ETF溢价套利需一篮子股票(T+0申赎)，散户门槛高")
    elif -prem >= config.DISCOUNT_THRESHOLD:
        direction, gross = "折价套利", -prem
        net = _net_discount_return(-prem)
        if executable:
            warns.append("场内买入后赎回，赎回费较高(默认0.5%)；到账T+N有净值波动")
        else:
            warns.append("境内ETF折价套利需赎回换一篮子股票卖出，散户门槛高")
    else:
        return None      # 折溢价太小，非机会

    if kind == "LOF":
        warns.insert(0, "折溢价基于昨收净值(T-1)，已含今日标的涨跌，非实时无风险套利——需自行估算当日净值")

    iopv_val = r.get("IOPV实时估值")
    iopv = float(iopv_val) if pd.notna(iopv_val) else None

    return ArbOpportunity(
        code=str(r["代码"]), name=str(r["名称"]), kind=kind, category=cat,
        price=float(r["最新价"]), iopv=iopv, basis=basis,
        premium=prem, turnover=turnover, direction=direction,
        gross=round(gross, 3), net=round(net, 3),
        executable=executable, warnings=warns,
    )


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("最新价", "基金折价率", "成交额", "IOPV实时估值"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["最新价"].notna() & df["基金折价率"].notna()].copy()
    df["溢价率"] = -df["基金折价率"]          # 正=溢价
    df["类别"] = df["名称"].map(_classify)
    return df


def scan() -> tuple[List[ArbOpportunity], dict]:
    """扫描全市场 ETF + LOF，返回（套利机会列表, 元信息）。"""
    log("▶ 拉取全市场 ETF 实时行情 …")
    etf = _prep(_fetch_etf_spot())

    log("▶ 拉取全市场 LOF 行情（自定义镜像，补折价率字段）…")
    lof_raw = _fetch_lof_spot()
    lof = _prep(lof_raw) if lof_raw is not None else None

    meta = {
        "数据日期": str(etf["数据日期"].iloc[0]) if "数据日期" in etf else "",
        "更新时间": str(etf["更新时间"].iloc[0]) if "更新时间" in etf else "",
        "ETF总数": len(etf),
        "LOF总数": len(lof) if lof is not None else 0,
        "LOF可用": lof is not None,
    }

    opportunities: List[ArbOpportunity] = []
    for _, r in etf.iterrows():
        op = _eval_row(r, "ETF")
        if op:
            opportunities.append(op)
    if lof is not None:
        for _, r in lof.iterrows():
            op = _eval_row(r, "LOF")
            if op:
                opportunities.append(op)

    # 排序：先可执行、再按预期净收益降序
    opportunities.sort(key=lambda o: (o.executable, o.net), reverse=True)
    meta["候选数"] = len(opportunities)
    lof_note = "" if meta["LOF可用"] else "（LOF本次拉取失败，仅含ETF）"
    log(f"  ✓ 命中套利候选 {len(opportunities)} 个{lof_note}（数据时间 {meta['更新时间']}）")
    return opportunities, meta
