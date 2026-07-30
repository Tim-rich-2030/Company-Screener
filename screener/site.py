# -*- coding: utf-8 -*-
"""
정적 사이트 생성 — GitHub Pages 에 그대로 올라간다.

왼쪽에서 종목을 검색해 고르면, 오른쪽에 그 종목의 분기별 숫자와 변화가 표와
그래프로 한꺼번에 나온다. 외부 요청을 하나도 하지 않으므로 GitHub Pages, 사내
정적 호스팅, 로컬 파일 열기 어디서든 똑같이 동작한다.
"""
from __future__ import annotations

import os
import json
import html

import quarterly_dashboard as qd

from . import config
from .metrics import compute_timeseries
from .store import load_all, load_state, sort_quarters

CSS = """
:root{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;--head:#f7f8fa;
--pos:#0a7c3f;--neg:#c02626;--accent:#1a56db;--chip:#eef2ff;--sel:#dbe4ff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ac;
--line:#262a31;--head:#171a20;--pos:#3ddc84;--neg:#ff6b6b;--accent:#7aa2ff;
--chip:#1c2333;--sel:#233056}}
:root[data-theme=dark]{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ac;--line:#262a31;
--head:#171a20;--pos:#3ddc84;--neg:#ff6b6b;--accent:#7aa2ff;--chip:#1c2333;--sel:#233056}
:root[data-theme=light]{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;
--head:#f7f8fa;--pos:#0a7c3f;--neg:#c02626;--accent:#1a56db;--chip:#eef2ff;--sel:#dbe4ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line)}
h1{font-size:17px;margin:0}
.meta{color:var(--muted);font-size:12px;margin-top:3px}
.layout{display:grid;grid-template-columns:270px 1fr;min-height:calc(100vh - 62px)}
@media (max-width:820px){.layout{grid-template-columns:1fr}
  .side{max-height:240px;border-right:0;border-bottom:1px solid var(--line)}}
.side{border-right:1px solid var(--line);overflow-y:auto;padding:10px}
.side input{width:100%;font:inherit;padding:7px 10px;border:1px solid var(--line);
border-radius:6px;background:var(--bg);color:var(--fg);margin-bottom:8px}
.item{padding:7px 9px;border-radius:6px;cursor:pointer;display:flex;
justify-content:space-between;gap:8px;align-items:baseline}
.item:hover{background:var(--head)}
.item.on{background:var(--sel)}
.item b{font-weight:600}
.item span{color:var(--muted);font-size:11px;white-space:nowrap}
main{padding:16px 18px 50px;overflow-x:auto}
.title{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:2px}
.title h2{margin:0;font-size:19px}
.title .code{color:var(--muted);font-size:13px}
.tag{background:var(--chip);color:var(--muted);border-radius:999px;
padding:2px 9px;font-size:11px}
.cards{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 16px}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:104px}
.card .k{color:var(--muted);font-size:11px}
.card .v{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:right;white-space:nowrap}
thead th{background:var(--head);position:sticky;top:0;font-size:12px}
tbody th{text-align:left;background:var(--bg);position:sticky;left:0;font-weight:500;
border-right:1px solid var(--line)}
.pos{color:var(--pos)}.neg{color:var(--neg)}.na{color:var(--muted)}
.chart{margin:6px 0 18px}
.cscroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.bval{font-size:9.5px;fill:var(--fg);font-variant-numeric:tabular-nums}
.bqlab{font-size:9px;fill:var(--muted)}
.chart h3{font-size:13px;margin:0 0 6px;color:var(--muted);font-weight:600}
.legend{font-size:11px;color:var(--muted);margin-bottom:4px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.foot{color:var(--muted);font-size:12px;margin-top:22px;line-height:1.7;max-width:70ch}
.empty{color:var(--muted);padding:40px 0}
.prog{display:inline-block;margin-top:4px;background:var(--chip);border-radius:999px;
padding:2px 10px;font-size:12px}
.prog.done{color:var(--pos)}
"""

JS = r"""
var DB = __DATA__;
var list = document.getElementById('list');
var search = document.getElementById('search');
var pane = document.getElementById('pane');
var current = null;

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmt(v, spec){
  if (v === null || v === undefined) return null;
  var plus = spec.indexOf('+') >= 0, m = spec.match(/\.(\d)f/), d = m ? +m[1] : 2;
  return (v < 0 ? '-' : (plus ? '+' : '')) +
    Math.abs(v).toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d});
}
function renderList(){
  var t = search.value.trim().toLowerCase();
  var rows = DB.companies.filter(function(c){
    return !t || c.name.toLowerCase().indexOf(t) >= 0 || c.code.indexOf(t) >= 0; });
  list.innerHTML = rows.map(function(c){
    return '<div class="item' + (current === c.code ? ' on' : '') + '" data-code="' + c.code +
      '"><b>' + esc(c.name) + '</b><span>' + esc(c.latest || '') + '</span></div>';
  }).join('') || '<div class="empty">검색 결과 없음</div>';
  list.querySelectorAll('.item').forEach(function(el){
    el.addEventListener('click', function(){ select(el.dataset.code); });
  });
}
function compact(v){
  // 막대 위에 얹는 짧은 숫자. 억 단위가 커지면 '조'로 접는다.
  var a = Math.abs(v);
  if (a >= 10000) return (v / 10000).toFixed(a >= 100000 ? 0 : 1) + '조';
  if (a >= 1000) return Math.round(v).toLocaleString('ko-KR');
  return (Math.round(v * 10) / 10).toLocaleString('ko-KR');
}
function bars(labels, vals, title){
  // 분기 막대. 음수는 0선 아래로 내려 적자 분기가 한눈에 보이게 한다.
  // 값은 막대 위(음수는 아래)에 직접 찍는다 — 눈금축을 읽는 것보다 빠르다.
  var W = Math.max(340, labels.length * 52), H = 132, padT = 20, padB = 26;
  var ok = vals.filter(function(v){return v !== null;});
  if (!ok.length) return '';
  var hi = Math.max.apply(null, ok), lo = Math.min.apply(null, ok);
  if (hi < 0) hi = 0; if (lo > 0) lo = 0;
  var span = (hi - lo) || 1, plot = H - padT - padB;
  var zero = padT + (hi / span) * plot;
  var step = (W - 8) / labels.length, bw = step * 0.6;
  var svg = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H +
            '" role="img">';
  svg += '<line x1="0" y1="' + zero.toFixed(1) + '" x2="' + W + '" y2="' + zero.toFixed(1) +
         '" stroke="var(--line)"/>';
  for (var i = 0; i < labels.length; i++){
    var v = vals[i]; if (v === null) continue;
    var x = 4 + i * step + (step - bw) / 2;
    var h = Math.abs(v) / span * plot;
    var y = v >= 0 ? zero - h : zero;
    var cx = x + bw / 2;
    svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
      '" height="' + Math.max(1, h).toFixed(1) + '" rx="1.5" fill="' +
      (v >= 0 ? 'var(--pos)' : 'var(--neg)') + '"><title>' + labels[i] + ': ' +
      v.toLocaleString('ko-KR') + '</title></rect>';
    // 값 라벨: 양수는 막대 위, 음수는 막대 아래
    svg += '<text class="bval" x="' + cx.toFixed(1) + '" y="' +
      (v >= 0 ? (y - 4).toFixed(1) : (y + h + 11).toFixed(1)) +
      '" text-anchor="middle">' + esc(compact(v)) + '</text>';
    // 분기 라벨은 2줄로 (2025Q3 -> 25 / Q3) 가로 공간을 아낀다
    svg += '<text class="bqlab" x="' + cx.toFixed(1) + '" y="' + (H - 4).toFixed(1) +
      '" text-anchor="middle">' + labels[i].slice(2) + '</text>';
  }
  svg += '</svg>';
  return '<div class="chart"><h3>' + title + '</h3><div class="cscroll">' + svg + '</div></div>';
}
function line(labels, vals, title, digits){
  var W = Math.max(340, labels.length * 52), H = 124, padT = 20, padB = 26, padX = 16;
  var ok = vals.filter(function(v){return v !== null;});
  if (ok.length < 2) return '';
  var hi = Math.max.apply(null, ok), lo = Math.min.apply(null, ok), span = (hi - lo) || 1;
  var plot = H - padT - padB;
  var step = (W - padX * 2) / Math.max(1, labels.length - 1);
  var d = '', started = false, marks = '';
  for (var i = 0; i < labels.length; i++){
    var v = vals[i];
    var x = padX + i * step;
    if (v === null){ started = false; }
    else {
      var y = padT + (hi - v) / span * plot;
      d += (started ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
      marks += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) +
        '" r="2.6" fill="var(--accent)"><title>' + labels[i] + ': ' +
        v.toLocaleString('ko-KR') + '</title></circle>';
      marks += '<text class="bval" x="' + x.toFixed(1) + '" y="' + (y - 6).toFixed(1) +
        '" text-anchor="middle">' + v.toFixed(digits == null ? 2 : digits) + '</text>';
      started = true;
    }
    marks += '<text class="bqlab" x="' + x.toFixed(1) + '" y="' + (H - 4).toFixed(1) +
      '" text-anchor="middle">' + labels[i].slice(2) + '</text>';
  }
  return '<div class="chart"><h3>' + title + '</h3><div class="cscroll"><svg width="' + W +
    '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '"><path d="' + d +
    '" fill="none" stroke="var(--accent)" stroke-width="1.8"/>' + marks +
    '</svg></div></div>';
}
function select(code){
  current = code;
  renderList();
  var c = DB.data[code];
  if (!c){ pane.innerHTML = '<div class="empty">데이터 없음</div>'; return; }
  var qs = c.quarters, oldFirst = qs.slice().reverse();
  var M = DB.metrics;

  // 최신 분기 요약 카드
  var cards = '';
  ['PBR','PER','ROE(%)','매출 YoY(%)','분기 영업이익률(%)'].forEach(function(k){
    if (!c.metrics[k]) return;
    var v = c.metrics[k][0], spec = (M.filter(function(m){return m.key===k;})[0]||{}).fmt || '{:.2f}';
    var s = fmt(v, spec);
    cards += '<div class="card"><div class="k">' + esc(k) + '</div><div class="v' +
      (s === null ? ' na' : '') + '">' + (s === null ? '–' : s) + '</div></div>';
  });

  // 분기 × 지표 표 (오래된 분기가 왼쪽 -> 변화가 왼쪽에서 오른쪽으로 읽힌다)
  var head = '<tr><th></th>' + oldFirst.map(function(q){
    return '<th>' + q + '</th>'; }).join('') + '</tr>';
  var body = M.map(function(m){
    var vals = c.metrics[m.key]; if (!vals) return '';
    var cells = oldFirst.map(function(q, i){
      var v = vals[qs.length - 1 - i], s = fmt(v, m.fmt), cls = '';
      if (s === null) cls = 'na';
      else if (m.better === 'high') cls = v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
      else if (m.better === 'low' && v < 0) cls = 'neg';
      return '<td class="' + cls + '">' + (s === null ? '–' : s) + '</td>';
    }).join('');
    return '<tr><th title="' + esc(m.desc) + '">' + esc(m.key) + '</th>' + cells + '</tr>';
  }).join('');

  function seriesOld(key){
    var v = c.metrics[key]; return v ? v.slice().reverse() : [];
  }
  var charts = bars(oldFirst, seriesOld('매출액(억)'), '분기 매출액 (억원)') +
               bars(oldFirst, seriesOld('영업이익(억)'), '분기 영업이익 (억원)') +
               line(oldFirst, seriesOld('PBR'), 'PBR 추이 (각 분기말 주가 기준)', 2) +
               line(oldFirst, seriesOld('PER'), 'PER 추이 (각 분기말 주가 기준)', 1);

  pane.innerHTML =
    '<div class="title"><h2>' + esc(c.name) + '</h2><span class="code">' + code + '</span>' +
    (c.fs_div ? '<span class="tag">' + esc(c.fs_div) + '</span>' : '') +
    '<span class="tag">' + qs.length + '개 분기</span>' +
    (c.report ? '<span class="tag">최근 ' + esc(c.report) + '</span>' : '') + '</div>' +
    '<div class="cards">' + cards + '</div>' + charts +
    '<div style="overflow-x:auto"><table><thead>' + head + '</thead><tbody>' + body +
    '</tbody></table></div>' +
    '<div class="foot">PBR·PER·ROE는 <b>각 분기말 종가와 그 시점 주식수</b>로 계산합니다. ' +
    '오늘 주가로 과거를 계산하면 시계열이 아니라 착시가 됩니다.<br>' +
    '분기말 시점에는 아직 그 분기 실적이 공시되기 전이므로, ' +
    '"그때 이 PER로 살 수 있었다"는 뜻은 아닙니다.<br>' +
    'PER은 최근 4분기 순이익이 적자면 계산하지 않습니다(–). ' +
    '금융지주·은행·보험은 자본 구조가 달라 제조업과 같은 기준으로 비교하면 안 됩니다.</div>';
}
// 마지막 실행 시각을 보는 사람의 시간대로, '몇 시간 전'까지 함께 보여준다
document.querySelectorAll('time[datetime]').forEach(function(el){
  var t = new Date(el.getAttribute('datetime'));
  if (isNaN(t)) return;
  var mins = Math.round((Date.now() - t.getTime()) / 60000);
  var ago = mins < 60 ? mins + '분 전'
          : mins < 1440 ? Math.round(mins / 60) + '시간 전'
          : Math.round(mins / 1440) + '일 전';
  el.textContent = t.toLocaleString('ko-KR', {month:'numeric', day:'numeric',
    hour:'2-digit', minute:'2-digit'}) + ' (' + ago + ')';
});
search.addEventListener('input', renderList);
renderList();
if (DB.companies.length) select(DB.companies[0].code);
"""


def build(out_dir: str = None) -> str:
    out_dir = out_dir or config.SITE_DIR
    os.makedirs(out_dir, exist_ok=True)

    qd.load_custom_metrics()
    metrics = list(qd.METRICS.values())

    companies, data = [], {}
    for rec in load_all():
        ts = compute_timeseries(rec, qd.METRICS)
        if not ts["quarters"]:
            continue
        latest_slot = rec["quarters"].get(ts["quarters"][0], {})
        data[rec["code"]] = {
            "name": rec.get("name") or rec["code"],
            "quarters": ts["quarters"],
            "metrics": ts["metrics"],
            "fs_div": {"CFS": "연결", "OFS": "별도"}.get(latest_slot.get("fs_div"), ""),
            "report": latest_slot.get("report_nm", ""),
        }
        companies.append({"code": rec["code"],
                          "name": rec.get("name") or rec["code"],
                          "latest": ts["quarters"][0]})
    companies.sort(key=lambda c: c["name"])

    state = load_state()
    payload = {
        "built_at": state.get("backfill_last_run") or state.get("last_run") or "",
        "companies": companies,
        "data": data,
        "metrics": [{"key": m.key, "fmt": m.fmt, "better": m.better, "desc": m.desc}
                    for m in metrics],
    }

    all_q = sort_quarters({q for c in data.values() for q in c["quarters"]})
    span = f"{all_q[-1]} ~ {all_q[0]}" if all_q else "데이터 없음"

    # 상태를 한 화면에서 다 보이게 한다. 안 그러면 실행 기록·state.json·사이트
    # 세 군데를 돌아다녀야 "돌고 있나, 어디까지 됐나"를 알 수 있다.
    target = state.get("backfill_total")
    chips = []
    if target and len(companies) < target:
        pct = round(len(companies) / target * 100)
        chips.append(f'<span class="prog">과거 수집 {len(companies)} / {target}종목 '
                     f'({pct}%)</span>')
    elif target:
        chips.append('<span class="prog done">과거 수집 완료</span>')
    for label, key in (("소급", "backfill_last_run"), ("공시 감지", "last_run")):
        when = state.get(key)
        if when:
            chips.append(f'<span class="prog">{label} 마지막 실행 '
                         f'<time datetime="{html.escape(when)}">{html.escape(when)}</time>'
                         f'</span>')
    progress = " ".join(chips)

    body = f"""<header>
<h1>코스피 분기 실적 시계열</h1>
<div class="meta">{len(companies)}종목 · {html.escape(span)} ·
공시가 뜨면 자동으로 수집·갱신됩니다</div>
<div class="meta">{progress}</div>
<div class="meta" id="built"></div>
</header>
<div class="layout">
  <aside class="side">
    <input id="search" type="search" placeholder="종목명 · 종목코드 검색" aria-label="검색">
    <div id="list"></div>
  </aside>
  <main id="pane"></main>
</div>"""

    script = JS.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    doc = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>코스피 분기 실적 시계열</title>'
           f'<style>{CSS}</style></head><body>{body}'
           f'<script>{script}</script></body></html>')

    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    # Pages가 _로 시작하는 경로를 Jekyll로 처리하지 않게 한다
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return path
