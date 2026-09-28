"""Publish audited V2 results and an industry-grouped comparison."""
import json
from collections import defaultdict
from pathlib import Path


def main():
    root = Path('artifacts/pool188-v2-20260928')
    summary = json.loads((root / 'summary.json').read_text())
    rows = json.loads((root / 'selected.json').read_text())
    old = json.loads(Path('artifacts/pool188-20260928/summary.json').read_text())
    rules = json.loads((root / 'rules.json').read_text())
    audit = json.loads((root / 'audit.json').read_text())
    assert len(rows) == 188
    assert sum(summary['rejection_counts'].values()) + 188 == 3196
    manifest = dict(name='188只同行业成熟与成长研究底池V2.1', asof=summary['asof'],
                    version='2.1', status='research_only_not_activated', count=188,
                    rules=rules, allocation=summary['allocation'], audit=audit,
                    rules_hash=summary['rules_hash'], script_hash=summary['script_hash'],
                    dependency_hash=summary['dependency_hash'],
                    stocks=[dict(r, symbol=r['code'].split('.')[1] + '.' + r['code'].split('.')[0].upper()) for r in rows])
    Path('configs/pool188-v2-20260928.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    lines = [
        '# 第二版188只研究底池：按行业展示', '',
        '数据日2026-09-28，与第一版同日比较。最终实现为V2.1；原有24只模拟试验没有切换。', '',
        '## 两版变化', '', '|指标|第一版|第二版|', '|---|---:|---:|',
        '|股票总数|188|188|', '|细分行业数|45|45|',
        '|科技行业代理|7（3.7%）|30（16.0%）|', '|科技相关待核实（单列）|26|44|',
        '|金融股|30|8|', '|其中银行|24|3|', '|成长盈利路径|未单独区分|8|',
        '|最新中报亏损入选|0|0|', '',
        '保留61只，新增127只，移除127只。这是方法调整，不是实际调仓指令，尚未证明收益更高。', '',
        '## 方法与证据边界', '',
        '- 财务指标只在同细分行业、同评价路径内比较；小样本收缩，不跨行业回退。',
        '- 行业名额按基础行情合格候选数量分配，行业内成长/成熟名额按财务合格候选数量分配，再按路径内分数选择；未设科技最低名额。',
        '- 行业弱势最多影响5个百分点，不整行业删除；取消TTM盈利及统一6% ROE硬过滤。',
        '- 成长评价采用营收、毛利率和经营趋势；亏损观察额外检查现金、流动比率、负债和净利率改善。',
        '- 研发投入与产出、现金消耗周期和融资依赖的专项数据仍不完整，明确标记缺失，不代表未来潜力已获验证。', '',
        '详见[规则](../docs/POOL188_V2.md)及[机器可读配置](../configs/pool188-v2-20260928.json)。', '',
        '## 科技口径', '',
        '科技行业代理为C39电子制造、C40仪器仪表、I63电信广播卫星传输、I64互联网、I65软件信息服务。设备、汽车、电气、其他运输设备和研究试验行业共44只仅标记为科技相关待核实，不并入30只。两版同口径；仍不含创业板、科创板或北交所。', '',
        '## 行业数量对照', '', '|行业|第一版|第二版|变化|', '|---|---:|---:|---:|']
    industries = set(old['industry_counts']) | set(summary['industry_counts'])
    for industry in sorted(industries, key=lambda k: (-summary['industry_counts'].get(k, 0), k)):
        a = old['industry_counts'].get(industry, 0)
        b = summary['industry_counts'].get(industry, 0)
        lines.append('|%s|%d|%d|%+d|' % (industry, a, b, b-a))
    lines += ['', '## 按行业列出全部188只', '', '分数不是日频买入排名或上涨概率。']
    groups = defaultdict(list)
    for row in rows:
        groups[row['industry']].append(row)
    for industry, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lines += ['', '### %s（%d只）' % (industry, len(items)), '',
                  '|代码|名称|评价路径|综合分|中期营收同比|', '|---|---|---|---:|---:|']
        for row in items:
            revenue = '缺失' if row['revenue_growth_h1'] is None else '%.1f%%' % (100*row['revenue_growth_h1'])
            lines.append('|%s|%s|%s|%.2f|%s|' % (row['code'], row['name'], row['lane'], row['score']*100, revenue))
    for title, key in [('新增', 'added'), ('移除', 'removed')]:
        items = summary[key]
        lines += ['', '## %s%d只' % (title, len(items)), '', '|代码|名称|行业|', '|---|---|---|']
        lines += ['|%s|%s|%s|' % (r['code'], r['name'], r['industry']) for r in items]
    lines += ['', '## 复核与限制', '',
              '- 财务检查1413只，780只通过至少一条路径；没有接口错误。',
              '- 已核验188个主板代码唯一、财报时点、同行评分范围、分数分项及集中度上限。',
              '- 缓存重跑后188条记录完全一致，第一版数据哈希未改变。',
              '- 移除仅表示未入本版名单，不代表公司变差。供应方可能修订财报，当前名单不能回填历史冒充无偏回测。',
              '- 研发、融资依赖、财务治理及金融专门监管指标仍需补证据，不从排名推断公司安全或必然上涨。']
    Path('research/pool188-v2-20260928.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
