"""读 logs/*.json，合成单文件静态 HTML 报告，浏览 BM25 检索结果。

用法：
    python scripts/generate_report.py
    python scripts/generate_report.py --log-dir logs --output output/retrieval_report.html
"""
import argparse
import json
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT / "logs"
DEFAULT_OUTPUT = ROOT / "output" / "retrieval_report.html"


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>BM25 检索报告</title>
<style>
:root {
  --green: #4caf50;
  --yellow: #ffa726;
  --red: #f44336;
  --blue: #3578e5;
  --blue-bg: #e3f2fd;
  --orange-bg: #fff3e0;
  --border: #e0e0e0;
  --bg: #f7f7f8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  color: #222;
}
#app { display: flex; height: 100vh; }
.sidebar {
  width: 260px;
  background: var(--bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sidebar-search {
  padding: 8px;
  border-bottom: 1px solid var(--border);
}
.sidebar-search input {
  width: 100%;
  padding: 6px 8px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
}
.domain-group {
  padding: 4px 0;
}
.domain-label {
  padding: 6px 12px 2px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
}
.qid-row {
  padding: 5px 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  border-left: 3px solid transparent;
}
.qid-row:hover { background: #ececec; }
.qid-row.active {
  background: var(--blue);
  color: white;
}
.qid-row .score-chip {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 8px;
  background: rgba(0,0,0,0.08);
}
.qid-row.active .score-chip { background: rgba(255,255,255,0.2); }
.detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.detail-header {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: #fafafa;
}
.detail-header h2 {
  margin: 0 0 4px;
  font-size: 16px;
}
.detail-header .meta-line {
  font-size: 11px;
  color: #666;
  margin-bottom: 6px;
}
.detail-header .question-text {
  font-size: 12px;
  margin: 6px 0;
  line-height: 1.5;
}
.query-chips, .option-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #eee;
}
.chip.domain { background: var(--blue-bg); }
.chip.option { background: var(--orange-bg); }
.chip.failed { background: #fee; color: var(--red); }
.chunks-area {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
}
.chunks-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
  margin-bottom: 8px;
}
.chunk-card {
  background: white;
  padding: 8px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  border-left: 4px solid var(--border);
}
.chunk-card.score-high { border-left-color: var(--green); }
.chunk-card.score-mid { border-left-color: var(--yellow); }
.chunk-card.score-low { border-left-color: var(--red); }
.chunk-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 2px;
}
.chunk-head .score { font-weight: bold; }
.chunk-head .score.high { color: var(--green); }
.chunk-head .score.mid { color: var(--yellow); }
.chunk-head .score.low { color: var(--red); }
.chunk-meta {
  font-size: 10px;
  color: #888;
  margin-bottom: 4px;
}
.chunk-text {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  padding: 6px;
  border-radius: 3px;
  margin: 4px 0 0;
  line-height: 1.5;
}
.empty {
  padding: 40px;
  text-align: center;
  color: #888;
}
.warn-bar {
  background: #fee;
  color: var(--red);
  padding: 6px 14px;
  font-size: 11px;
  border-bottom: 1px solid #fcc;
}
</style>
</head>
<body>
<div id="app"></div>
<script type="application/json" id="data">__DATA_PLACEHOLDER__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

function scoreClass(score, maxScore) {
  if (!maxScore) return 'low';
  const ratio = score / maxScore;
  if (ratio >= 0.7) return 'high';
  if (ratio >= 0.4) return 'mid';
  return 'low';
}
function scoreCardClass(score, maxScore) {
  const c = scoreClass(score, maxScore);
  return 'chunk-card score-' + c;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
}
function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function renderSidebar(records, activeQid, filter) {
  const grouped = {};
  for (const r of records) {
    const d = r.domain || '(no domain)';
    if (!grouped[d]) grouped[d] = [];
    grouped[d].push(r);
  }
  const domains = Object.keys(grouped).sort();
  let html = '';
  for (const d of domains) {
    const items = grouped[d].filter(r =>
      !filter || (r.qid && r.qid.toLowerCase().includes(filter.toLowerCase()))
    );
    if (!items.length) continue;
    html += `<div class="domain-group">`;
    html += `<div class="domain-label">${escapeHtml(d)} (${items.length})</div>`;
    for (const r of items) {
      const maxScore = r.stats && r.stats.max_bm25_score || 0;
      const cls = scoreClass(maxScore, maxScore) || 'low';
      const colorMap = { high: 'var(--green)', mid: 'var(--yellow)', low: 'var(--red)' };
      const active = r.qid === activeQid ? ' active' : '';
      html += `<div class="qid-row${active}" onclick="selectQid('${escapeHtml(r.qid)}')">
        <span>${escapeHtml(r.qid)}</span>
        <span class="score-chip" style="color:${colorMap[cls]}">
          ■ ${maxScore.toFixed(1)} · ${(r.chunks || []).length}c
        </span>
      </div>`;
    }
    html += `</div>`;
  }
  return html;
}

function renderDetail(r) {
  if (!r) return '<div class="empty">选择左侧 qid 查看详情</div>';
  const stats = r.stats || {};
  const maxScore = stats.max_bm25_score || 0;
  const chunks = r.chunks || [];
  const queries = r.queries || [];
  const options = r.options || [];

  let html = `<div class="detail-header">
    <h2>${escapeHtml(r.qid)}</h2>
    <div class="meta-line">
      ${escapeHtml(r.domain || '')} · ${escapeHtml(r.answer_format || '')} ·
      queries: ${queries.length} · chunks: ${stats.chunk_count || 0} ·
      windows: ${stats.retrieved_windows || 0} ·
      <span style="color:var(--green)">max ${maxScore.toFixed(2)}</span> ·
      avg ${(stats.avg_bm25_score || 0).toFixed(2)}
    </div>
    <div class="question-text"><strong>题目：</strong>${escapeHtml(r.question_text || '')}</div>
    <div class="query-chips">
      ${queries.map(q => `<span class="chip">${escapeHtml(q)}</span>`).join('')}
    </div>
    ${options.length ? `<div class="option-chips">
      ${options.map(o => `<span class="chip option">${escapeHtml(o)}</span>`).join('')}
    </div>` : ''}
  </div>`;

  html += `<div class="chunks-area">
    <div class="chunks-label">chunks · 按 score 降序 (${chunks.length})</div>`;
  const sorted = [...chunks].sort((a, b) => (b.score || 0) - (a.score || 0));
  for (let i = 0; i < sorted.length; i++) {
    const c = sorted[i];
    const cls = scoreClass(c.score || 0, maxScore);
    html += `<div class="${scoreCardClass(c.score || 0, maxScore)}">
      <div class="chunk-head">
        <strong>chunk ${i + 1}</strong>
        <span class="score ${cls}">★ ${(c.score || 0).toFixed(2)}</span>
      </div>
      <div class="chunk-meta">
        ${escapeHtml(truncate(c.doc_id, 60))} · ${c.start || 0}-${c.end || 0}
        ${(c.query_types || []).map(q => `<span class="chip ${q.startsWith('option') ? 'option' : 'domain'}">${escapeHtml(q)}</span>`).join(' ')}
      </div>
      <pre class="chunk-text">${escapeHtml(c.text || '')}</pre>
    </div>`;
  }
  if (!sorted.length) {
    html += `<div class="empty">无 chunks（检索为空）</div>`;
  }
  html += `</div>`;
  return html;
}

function renderFailedBar(failed) {
  if (!failed || !failed.length) return '';
  const items = failed.map(f => `${escapeHtml(f.qid)}: ${escapeHtml(f.error)}`).join('; ');
  return `<div class="warn-bar">⚠ ${failed.length} 个 log 解析失败：${items}</div>`;
}

let activeQid = DATA.records[0] && DATA.records[0].qid;
let filter = '';

function render() {
  const app = document.getElementById('app');
  const active = DATA.records.find(r => r.qid === activeQid);
  const sidebarHtml = renderSidebar(DATA.records, activeQid, filter);
  const detailHtml = renderDetail(active);
  const failedBar = renderFailedBar(DATA.failed);
  app.innerHTML = `
    <div class="sidebar">
      <div class="sidebar-search">
        <input placeholder="搜 qid..." value="${escapeHtml(filter)}" oninput="setFilter(this.value)">
      </div>
      <div class="sidebar-list">${sidebarHtml}</div>
    </div>
    <div class="detail">
      ${failedBar}
      ${detailHtml}
    </div>
  `;
}

function selectQid(qid) {
  activeQid = qid;
  render();
}

function setFilter(value) {
  filter = value;
  render();
}

if (!DATA.records.length && !DATA.failed.length) {
  document.getElementById('app').innerHTML =
    '<div class="empty" style="flex:1;display:flex;align-items:center;justify-content:center">未找到任何 log</div>';
} else if (!DATA.records.length) {
  document.getElementById('app').innerHTML =
    `<div style="flex:1">${renderFailedBar(DATA.failed)}</div>`;
} else {
  render();
}
</script>
</body>
</html>
"""


def load_logs(log_dir: Path):
    """读 log_dir 下所有 *.json，返回 (records, failed)。"""
    records = []
    failed = []
    for path in sorted(log_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                records.append(json.load(f))
        except Exception as e:
            failed.append({"qid": path.stem, "error": str(e)})
    records.sort(key=lambda r: r.get("qid", ""))
    return records, failed


def render_html(records, failed):
    data = {"records": records, "failed": failed}
    data_json = json.dumps(data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", escape(data_json))


def main():
    parser = argparse.ArgumentParser(description="生成 BM25 检索 HTML 报告")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR),
                        help=f"log 目录（默认 {DEFAULT_LOG_DIR}）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help=f"输出 HTML 路径（默认 {DEFAULT_OUTPUT}）")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(
            f"错误: log 目录 {log_dir} 不存在。"
            f"请先跑 python -m src.agent.run --split A",
            file=sys.stderr,
        )
        sys.exit(1)

    records, failed = load_logs(log_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(records, failed)
    output.write_text(html, encoding="utf-8")

    print(f"报告已生成: {output}")
    print(f"  题数: {len(records)}，失败 log: {len(failed)}")
    if not records and not failed:
        print(f"  警告: {log_dir} 下没有 JSON 文件", file=sys.stderr)


if __name__ == "__main__":
    main()
