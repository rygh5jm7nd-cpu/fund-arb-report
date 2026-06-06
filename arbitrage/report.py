"""基金套利模块 · HTML 报告生成"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List

from . import config
from .scanner import ArbOpportunity
from .convertible import CBArb
from .reverse_repo import RepoRate

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#0f1115;color:#e6e6e6;padding:24px;font-size:14px}
h1{font-size:22px;margin-bottom:4px}
.sub{color:#8a8f98;font-size:13px;margin-bottom:18px}
.warn-box{background:#2a1f12;border:1px solid #6b4a1f;color:#f0c674;padding:12px 14px;border-radius:8px;margin-bottom:18px;font-size:13px;line-height:1.7}
h2{font-size:16px;margin:22px 0 10px;border-left:3px solid #4a9eff;padding-left:8px}
table{width:100%;border-collapse:collapse;background:#171a21;border-radius:8px;overflow:hidden}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid #232733;white-space:nowrap}
th{background:#1d2129;color:#9aa0aa;font-weight:600;font-size:12px;text-align:right}
th:nth-child(-n+4),td:nth-child(-n+4){text-align:left}
tr:hover td{background:#1c2029}
.code{color:#6ab0ff;font-family:monospace}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px}
.t-prem{background:#3a1a1a;color:#ff7676}
.t-disc{background:#143020;color:#5fd28a}
.cat{color:#9aa0aa;font-size:12px}
.net-pos{color:#5fd28a;font-weight:600}
.net-neg{color:#777}
.net-hi{color:#ffd166;font-weight:700}
.note{color:#c98b3a;font-size:11px;line-height:1.5;max-width:340px;white-space:normal;text-align:left}
.muted{opacity:.55}
footer{margin-top:24px;color:#6a6f78;font-size:12px;line-height:1.7}
"""


def _fmt_turnover(v: float) -> str:
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    return f"{v/1e4:.0f}万"


def _row(o: ArbOpportunity) -> str:
    tag = "t-prem" if o.direction == "溢价套利" else "t-disc"
    if o.net <= 0:
        net_cls = "net-neg"
    elif o.net >= 2:
        net_cls = "net-hi"
    else:
        net_cls = "net-pos"
    muted = "" if o.executable else " muted"
    notes = "；".join(o.warnings)
    # ETF 显示实时 IOPV，LOF 无盘中估值显示基准说明
    nav_cell = f"{o.iopv:.3f}" if o.iopv is not None else "—<br><span style='font-size:10px;opacity:.6'>T-1净值</span>"
    return f"""<tr class="{muted.strip()}">
<td class="code">{o.code}</td>
<td>{o.name}</td>
<td class="cat">{o.kind}</td>
<td class="cat">{o.category}</td>
<td><span class="tag {tag}">{o.direction}</span></td>
<td>{o.price:.3f}</td>
<td>{nav_cell}</td>
<td>{o.gross:.2f}%</td>
<td class="{net_cls}">{o.net:+.2f}%</td>
<td>{_fmt_turnover(o.turnover)}</td>
<td class="note">{notes}</td>
</tr>"""


def _table(title: str, rows: List[ArbOpportunity]) -> str:
    if not rows:
        return f"<h2>{title}</h2><p class='sub'>无</p>"
    body = "\n".join(_row(o) for o in rows)
    return f"""<h2>{title}（{len(rows)}）</h2>
<table>
<thead><tr>
<th>代码</th><th>名称</th><th>品种</th><th>类别</th><th>方向</th><th>市价</th><th>净值/IOPV</th>
<th>账面折溢价</th><th>预期净收益</th><th>成交额</th><th>提示</th>
</tr></thead>
<tbody>{body}</tbody>
</table>"""


def _cb_table(rows: List[CBArb]) -> str:
    title = "③ 可转债转股套利（转股溢价率为负）"
    if not rows:
        return (f"<h2>{title}（0）</h2>"
                "<p class='sub'>当前无转股溢价率为负的转债——转债市场普遍正溢价时属正常，"
                "机会多出现在个别临近强赎/到期的券。</p>")
    body = "\n".join(f"""<tr>
<td class="code">{o.code}</td><td>{o.name}</td>
<td>{o.price:.2f}</td><td>{o.conv_value:.2f}</td>
<td class="net-pos">{o.premium:+.2f}%</td>
<td class="code">{o.stock_code}</td><td>{o.stock_name}</td>
<td class="{'net-hi' if o.net>=1 else ('net-pos' if o.net>0 else 'net-neg')}">{o.net:+.2f}%</td>
<td class="note">{'；'.join(o.warnings)}</td>
</tr>""" for o in rows)
    return f"""<h2>{title}（{len(rows)}）</h2>
<table><thead><tr>
<th>转债代码</th><th>转债名</th><th>转债价</th><th>转股价值</th><th>转股溢价率</th>
<th>正股代码</th><th>正股名</th><th>预期净收益</th><th>提示</th>
</tr></thead><tbody>{body}</tbody></table>"""


def _repo_table(rows: List[RepoRate]) -> str:
    title = "④ 国债逆回购（闲置资金现金管理）"
    if not rows:
        return f"<h2>{title}（0）</h2><p class='sub'>无数据</p>"
    body = "\n".join(f"""<tr class="{'' if o.highlight else 'muted'}">
<td class="code">{o.code}</td><td>{o.name}</td><td class="cat">{o.market}</td>
<td>{o.days}天</td>
<td class="{'net-hi' if o.highlight else 'net-pos'}">{o.rate:.3f}%</td>
<td>{o.net_annualized:.3f}%</td><td>{o.net_per_10k:.2f}元</td>
<td class="note">{'⚡利率尖峰，闲钱优先' if o.highlight else ''}</td>
</tr>""" for o in rows)
    return f"""<h2>{title}（{len(rows)}）</h2>
<table><thead><tr>
<th>代码</th><th>名称</th><th>市场</th><th>期限</th><th>年化利率</th>
<th>扣费净年化</th><th>每万元净收益</th><th>提示</th>
</tr></thead><tbody>{body}</tbody></table>"""


def generate(opportunities: List[ArbOpportunity], meta: dict,
             cb_list: List[CBArb] = None, repo_list: List[RepoRate] = None) -> str:
    cb_list = cb_list or []
    repo_list = repo_list or []
    now = datetime.now()
    # 拆两组：可场外申赎套利（主战场） / 境内 ETF（门槛高，仅参考）
    main = [o for o in opportunities if o.executable]
    domestic = [o for o in opportunities if not o.executable]
    c = config.COST

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>基金套利机会日报 {now:%Y-%m-%d}</title>
<style>{CSS}</style></head>
<body>
<h1>基金套利机会日报</h1>
<div class="sub">数据时间：{meta.get('更新时间','')} ｜ 扫描 {meta.get('ETF总数',0)} 只 ETF + {meta.get('LOF总数',0)} 只 LOF{'' if meta.get('LOF可用') else '（LOF本次拉取失败，仅ETF）'}，命中候选 {meta.get('候选数',0)} 个 ｜ 生成于 {now:%Y-%m-%d %H:%M}</div>

<div class="warn-box">
⚠️ <b>重要说明</b>：本报告为研究参考，非投资建议。<br>
· <b>预期净收益</b>已扣减成本假设（申购费 {c['申购费率']}% / 赎回费 {c['赎回费率']}% / 佣金 {c['卖出佣金']}% / 冲击 {c['冲击成本']}%），请按自己平台费率在 <code>arbitrage/config.py</code> 调整。<br>
· <b>溢价套利</b>能否吃到，取决于该基金<b>当日是否开放申购、限购额度</b>——本表无法判断，需到基金公司/天天基金人工确认。<br>
· 跨境/QDII 份额申购到账多为 <b>T+2 及以上</b>，期间承担净值波动，账面溢价≠到手收益。<br>
· <b>LOF（净值/IOPV列显示"T-1净值"者）</b>：东财不提供盘中实时估值，折溢价是<b>市价 vs 昨日净值</b>，已含今日标的（白银/原油/海外等）涨跌，<b>不是实时无风险套利</b>——真实空间需自行按当日标的涨跌估算当日净值后再算。<br>
· 溢价＞{config.HIGH_PREMIUM_WARN}% 的标的往往已被爆炒，<b>回落风险极高</b>，追入可能巨亏。
</div>

{_table("① 可申赎套利（跨境/QDII/商品，主战场）", main)}
{_table("② 境内 ETF 折溢价（需一篮子股票，门槛高，仅参考）", domestic)}
{_cb_table(cb_list)}
{_repo_table(repo_list)}

<footer>
说明：基金折溢价口径 = (净值 − 市价)/净值；溢价=市价高于净值。<br>
溢价套利＝场外按净值申购→场内高价卖出；折价套利＝场内低价买入→按净值赎回。<br>
可转债转股套利＝买转债→转股(T+1)→卖正股，吃负的转股溢价率（非无风险，含隔夜敞口）。<br>
逆回购＝质押式国债逆回购，借出资金收固定利息，适合闲钱；节前月末短期利率常飙升。<br>
注：债券/可转债 ETF 折溢价经核查不可行（免费源无实时 IOPV、折价率读数为 0），未纳入。<br>
数据源：东方财富。本报告由基金套利模块自动生成。
</footer>
</body></html>"""

    fname = f"基金套利日报_{now:%Y%m%d_%H%M}.html"
    path = os.path.join(config.REPORT_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
