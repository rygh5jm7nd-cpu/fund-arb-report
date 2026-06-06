#!/usr/bin/env python3
"""基金套利机会日报 · 入口

每天扫描全市场 ETF 的折溢价，列出套利标的、方向与扣成本后的预期净收益，
生成 HTML 报告存到 reports/。

用法：
  ./venv/bin/python fund_arb.py            # 扫描并生成报告（非交易日自动跳过）
  ./venv/bin/python fund_arb.py --open     # 生成后自动用浏览器打开
  ./venv/bin/python fund_arb.py --top 20   # 控制台同时打印前 N 条
  ./venv/bin/python fund_arb.py --force    # 无视交易日历强制运行（周末测试用）

成本/阈值在 arbitrage/config.py 调整。
"""
from __future__ import annotations

import sys
import argparse
import datetime as dt

from arbitrage.scanner import scan
from arbitrage.convertible import scan as scan_cb
from arbitrage.reverse_repo import scan as scan_repo
from arbitrage.report import generate
from arbitrage.publish import publish as publish_pages
from arbitrage.notify import push as push_wechat


def is_trading_day() -> bool:
    """今天是否为 A 股交易日（用 akshare 交易日历，失败时按周一~周五粗判）。"""
    today = dt.date.today().isoformat()
    try:
        import akshare as ak
        days = {str(x) for x in ak.tool_trade_date_hist_sina()["trade_date"]}
        return today in days
    except Exception:  # noqa: BLE001 日历拉取失败时退化为工作日判断
        return dt.date.today().weekday() < 5


def main():
    parser = argparse.ArgumentParser(description="基金套利机会日报")
    parser.add_argument("--open", action="store_true", help="生成后自动打开报告")
    parser.add_argument("--top", type=int, default=15, help="控制台打印前 N 条（默认15）")
    parser.add_argument("--force", action="store_true", help="无视交易日历强制运行")
    parser.add_argument("--no-notify", action="store_true", help="不推送微信（仅生成报告）")
    args = parser.parse_args()

    if not args.force and not is_trading_day():
        print(f"· {dt.date.today()} 非交易日，跳过（加 --force 可强制运行）", file=sys.stderr)
        return

    opportunities, meta = scan()
    cb_list = scan_cb()
    repo_list = scan_repo()
    path = generate(opportunities, meta, cb_list, repo_list)
    print(f"\n✓ 报告已生成：{path}", file=sys.stderr)

    # 发布到 GitHub Pages（任何设备可点开）；失败则推送里退回本地路径
    pages_url = publish_pages(path)
    link = pages_url or path

    if not args.no_notify:
        push_wechat(opportunities, cb_list, repo_list, meta, link)

    # 控制台速览
    top = [o for o in opportunities if o.executable][: args.top]
    print(f"\n【基金折溢价】{'代码':<8}{'名称':<16}{'方向':<8}{'账面':>7}{'净收益':>8}  类别", file=sys.stderr)
    print("-" * 70, file=sys.stderr)
    for o in top:
        print(f"          {o.code:<8}{o.name:<16}{o.direction:<8}"
              f"{o.gross:>6.2f}%{o.net:>+7.2f}%  {o.category}", file=sys.stderr)
    if cb_list:
        print(f"\n【转股套利】前{min(5,len(cb_list))}：" + " ｜ ".join(
            f"{o.name}({o.code}) 溢价{o.premium:+.2f}% 净{o.net:+.2f}%" for o in cb_list[:5]), file=sys.stderr)
    if repo_list:
        hot = [o for o in repo_list if o.highlight] or repo_list[:3]
        print("【逆回购】" + " ｜ ".join(
            f"{o.name} {o.rate:.2f}%" for o in hot[:5]), file=sys.stderr)

    if args.open:
        import webbrowser
        webbrowser.open(f"file://{path}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
