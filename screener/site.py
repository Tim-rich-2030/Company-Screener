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
import hashlib

import quarterly_dashboard as qd

from . import config
from . import screen
from .metrics import compute_timeseries
from .store import load_all, load_state, sort_quarters, frozen_price_run

CSS = """
/* CLAUDE.md 디자인 규칙을 따른다. 오래된 회계장부·신문 금융면의 톤.
   폰트·차트는 전부 저장소 안(assets/)에서 불러온다 — 바깥으로 요청이 나가면
   오프라인에서 앱이 반쪽이 되고, 자체 완결 보장도 깨진다. */
@font-face{font-family:'Noto Serif KR';src:url('assets/fonts/noto-serif-kr-korean-400.woff2')
  format('woff2');font-weight:400;font-display:swap;unicode-range:U+1100-11FF,U+3130-318F,U+AC00-D7A3}
@font-face{font-family:'Noto Serif KR';src:url('assets/fonts/noto-serif-kr-latin-400.woff2')
  format('woff2');font-weight:400;font-display:swap;unicode-range:U+0000-00FF,U+2000-206F}
@font-face{font-family:'Pretendard';src:url('assets/fonts/pretendard-400.woff2')
  format('woff2');font-weight:400;font-display:swap}
@font-face{font-family:'Pretendard';src:url('assets/fonts/pretendard-600.woff2')
  format('woff2');font-weight:600;font-display:swap}
@font-face{font-family:'IBM Plex Mono';src:url('assets/fonts/ibm-plex-mono-400.woff2')
  format('woff2');font-weight:400;font-display:swap}
@font-face{font-family:'IBM Plex Mono';src:url('assets/fonts/ibm-plex-mono-500.woff2')
  format('woff2');font-weight:500;font-display:swap}

:root{
  --paper:#F5F2EC; --surface:#FFFDF8; --ink:#1C1A17; --ink-muted:#6B6558;
  --rule:#DED8CC; --accent:#2C4A45; --up:#C4443D; --down:#3D6099;
}
/* 규칙에 다크는 네 가지(paper·surface·ink·rule)만 정해져 있다. 나머지 넷은
   어두운 종이 위에서 읽히도록 같은 계열에서 밝기만 올린 임시값이다. */
@media (prefers-color-scheme:dark){:root{
  --paper:#14130F; --surface:#1C1A16; --ink:#E8E3D8; --rule:#2E2B24;
  --ink-muted:#9A9384; --accent:#7FA89F; --up:#D9645C; --down:#6E8FC4;
}}
:root[data-theme=dark]{--paper:#14130F;--surface:#1C1A16;--ink:#E8E3D8;--rule:#2E2B24;
  --ink-muted:#9A9384;--accent:#7FA89F;--up:#D9645C;--down:#6E8FC4}
:root[data-theme=light]{--paper:#F5F2EC;--surface:#FFFDF8;--ink:#1C1A17;
  --ink-muted:#6B6558;--rule:#DED8CC;--accent:#2C4A45;--up:#C4443D;--down:#3D6099}

*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.65 'Pretendard',system-ui,sans-serif;
  padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}
/* 숫자는 예외 없이 등폭. 자릿수가 흔들리면 표가 아니라 낙서가 된다.
   IBM Plex Mono 에는 한글이 없다. Pretendard 를 뒤에 둬야 '7. 31. 오전 08:27'
   같은 문자열에서 한글만 시스템 모노로 떨어지지 않는다. */
.num,.card .v,.mkt b,table td,table th,.item span,time{
  font-family:'IBM Plex Mono','Pretendard',ui-monospace,monospace;
  font-variant-numeric:tabular-nums}
h1,h2,.item b,.title h2{font-family:'Noto Serif KR',serif;font-weight:400}

header{padding:22px 20px 18px;border-bottom:1px solid var(--rule)}
h1{font-size:22px;margin:0;letter-spacing:-.2px}
.hrow{display:flex;align-items:baseline;gap:12px}
.navlink{margin-left:auto;flex:none;font-size:13px;text-decoration:none;
  color:var(--accent);border-bottom:1px solid var(--rule);padding-bottom:1px}
.meta{color:var(--ink-muted);font-size:12.5px;margin-top:6px}
.prog{display:inline-block;margin-right:14px;font-size:12.5px;color:var(--ink-muted)}
.prog.done{color:var(--accent)}

/* 지수 요약. 박스가 아니라 위아래 괘선으로만 구분한다. */
.mkt{display:flex;gap:22px;flex-wrap:wrap;align-items:baseline;
  margin-top:16px;padding:12px 0 11px;border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule);text-decoration:none;color:inherit;font-size:13.5px}
.mkt i{font-style:normal;color:var(--ink-muted);margin-right:6px;font-size:12.5px}
.mkt .more{margin-left:auto;color:var(--accent)}
/* 주가는 오르면 빨강 내리면 파랑 (실적 표와 방향이 반대다) */
.mkt .u{color:var(--up)} .mkt .d{color:var(--down)}

.layout{display:grid;grid-template-columns:264px 1fr;min-height:60vh}
@media (max-width:820px){.layout{grid-template-columns:1fr}
  .side{max-height:232px;border-right:0;border-bottom:1px solid var(--rule)}}
/* 목록은 292종목이라 그냥 두면 페이지가 1만 픽셀이 된다. 화면에 붙여두고
   자기 안에서만 구르게 한다. */
.side{border-right:1px solid var(--rule);padding:16px 0 16px 20px;
  position:sticky;top:0;align-self:start;max-height:100vh;overflow-y:auto}
@media (max-width:820px){.side{position:static;max-height:232px}}
.side input{width:calc(100% - 20px);font:inherit;font-size:14px;padding:8px 0;
  border:0;border-bottom:1px solid var(--rule);background:transparent;
  color:var(--ink);margin-bottom:6px;outline:none}
.side input:focus{border-bottom-color:var(--accent)}
/* 목록도 박스가 아니라 줄. 선택은 배경이 아니라 왼쪽 괘선으로 표시한다. */
.item{padding:9px 20px 9px 0;cursor:pointer;display:flex;
  justify-content:space-between;gap:10px;align-items:baseline;
  border-bottom:1px solid var(--rule);border-left:2px solid transparent;
  padding-left:10px;margin-left:-12px}
.item:hover{background:var(--surface)}
.item.on{border-left-color:var(--accent);background:var(--surface)}
.item b{font-weight:400;font-size:15px}
.item span{color:var(--ink-muted);font-size:11.5px;white-space:nowrap}
.halt{font-style:normal;font-size:11px;margin-left:7px;color:var(--up)}

main{padding:32px 20px 64px;overflow-x:auto}
.title{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  padding-bottom:12px;border-bottom:1px solid var(--rule)}
.title h2{margin:0;font-size:26px;letter-spacing:-.3px}
.title .code{color:var(--ink-muted);font-size:13px;
  font-family:'IBM Plex Mono',monospace}
.tag{color:var(--ink-muted);font-size:11.5px}
.tag.warn{color:var(--up)}

/* 요약 숫자는 셋까지. 나머지는 아래 표에 있다. */
.cards{display:flex;gap:40px;flex-wrap:wrap;margin:32px 0;
  padding-bottom:20px;border-bottom:1px solid var(--rule)}
.card .k{color:var(--ink-muted);font-size:11.5px;letter-spacing:.3px}
.card .v{font-size:30px;font-weight:500;line-height:1.25;margin-top:2px}
.card .v.na{color:var(--ink-muted)}

table{border-collapse:collapse;font-size:13px;width:100%}
th,td{border-bottom:1px solid var(--rule);padding:8px 12px;text-align:right;
  white-space:nowrap}
thead th{position:sticky;top:0;background:var(--paper);font-size:11.5px;
  font-weight:400;color:var(--ink-muted);border-bottom:1px solid var(--ink-muted)}
tbody th{text-align:left;background:var(--paper);position:sticky;left:0;
  font-weight:400;font-family:'Pretendard',sans-serif;color:var(--ink-muted)}
.pos{color:var(--up)}.neg{color:var(--down)}.na{color:var(--ink-muted)}

.chart{margin:32px 0}
.chart h3{font-size:12.5px;margin:0 0 10px;color:var(--ink-muted);font-weight:400;
  letter-spacing:.3px}
.chart .box{height:180px}
.tv{font-size:10.5px;color:var(--ink-muted);margin-top:6px}
.tv a{color:var(--ink-muted)}

.foot{color:var(--ink-muted);font-size:12px;margin-top:32px;line-height:1.8;
  max-width:70ch;padding-top:16px;border-top:1px solid var(--rule)}
.empty{color:var(--ink-muted);padding:40px 0}
.newbuild{position:fixed;left:0;right:0;bottom:0;z-index:50;
  padding:14px 16px calc(14px + env(safe-area-inset-bottom));
  background:var(--surface);border-top:1px solid var(--accent);
  color:var(--accent);font-size:13.5px;text-align:center;cursor:pointer}
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
      '"><b>' + esc(c.name) +
      (c.halted ? '<i class="halt" title="분기말 종가가 여러 분기째 그대로입니다">정지?</i>' : '') +
      '</b><span>' + esc(c.latest || '') + '</span></div>';
  }).join('') || '<div class="empty">검색 결과 없음</div>';
  list.querySelectorAll('.item').forEach(function(el){
    el.addEventListener('click', function(){ select(el.dataset.code); });
  });
}
function compact(v){
  // 억 단위가 커지면 '조'로 접는다.
  var a = Math.abs(v);
  if (a >= 10000) return (v / 10000).toFixed(a >= 100000 ? 0 : 1) + '조';
  if (a >= 1000) return Math.round(v).toLocaleString('ko-KR');
  return (Math.round(v * 10) / 10).toLocaleString('ko-KR');
}
// 분기 라벨(2025Q3)을 차트가 쓰는 날짜로. 분기말 달의 1일이면 순서만 맞으면 된다.
function qDate(q){
  var y = +q.slice(0, 4), n = +q.slice(5);
  return {year: y, month: n * 3, day: 1};
}
function cssVar(n){
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}
var CHARTS = [];
function disposeCharts(){
  CHARTS.forEach(function(c){ try{ c.remove(); }catch(e){} });
  CHARTS = [];
}
// TradingView Lightweight Charts. 격자선은 --rule 색으로 최소한만 남긴다.
function drawChart(el, labels, vals, kind, digits){
  if (!window.LightweightCharts) return;
  var pts = [];
  for (var i = 0; i < labels.length; i++){
    if (vals[i] === null || vals[i] === undefined) continue;
    pts.push({time: qDate(labels[i]), value: vals[i]});
  }
  if (pts.length < 2) return;
  var ink = cssVar('--ink'), muted = cssVar('--ink-muted'), rule = cssVar('--rule');
  var chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: 180,
    layout: {background: {color: 'transparent'}, textColor: muted,
             fontFamily: "'IBM Plex Mono', monospace", fontSize: 10},
    grid: {vertLines: {visible: false}, horzLines: {color: rule}},
    rightPriceScale: {borderColor: rule},
    timeScale: {borderColor: rule, fixLeftEdge: true, fixRightEdge: true},
    crosshair: {mode: 0, vertLine: {color: muted, width: 1, style: 2, labelBackgroundColor: ink},
                horzLine: {color: muted, width: 1, style: 2, labelBackgroundColor: ink}},
    handleScroll: true, handleScale: true,
  });
  var series;
  if (kind === 'bar'){
    series = chart.addHistogramSeries({
      priceFormat: {type: 'volume'},
      color: cssVar('--accent'),
    });
    // 적자 분기는 색을 바꿔 0선 아래가 바로 읽히게 한다
    pts = pts.map(function(p){
      return {time: p.time, value: p.value,
              color: p.value >= 0 ? cssVar('--accent') : cssVar('--up')};
    });
  } else {
    series = chart.addLineSeries({
      color: cssVar('--accent'), lineWidth: 1,
      priceLineVisible: false, lastValueVisible: true,
      priceFormat: {type: 'price', precision: digits == null ? 2 : digits,
                    minMove: Math.pow(10, -(digits == null ? 2 : digits))},
    });
  }
  series.setData(pts);
  chart.timeScale().fitContent();
  CHARTS.push(chart);
}
function select(code){
  current = code;
  renderList();
  var c = DB.data[code];
  if (!c){ pane.innerHTML = '<div class="empty">데이터 없음</div>'; return; }
  var qs = c.quarters, oldFirst = qs.slice().reverse();
  var M = DB.metrics;

  // 요약 숫자는 셋까지. 나머지는 아래 표에 다 있다 (디자인 규칙).
  var cards = '';
  ['PBR','PER','ROE(%)'].forEach(function(k){
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
  // 차트는 그릴 자리만 잡아두고, DOM 에 붙은 뒤 Lightweight Charts 로 그린다.
  var SPECS = [
    {id:'c-rev', title:'분기 매출액 (억원)',            key:'매출액(억)',   kind:'bar'},
    {id:'c-op',  title:'분기 영업이익 (억원)',          key:'영업이익(억)', kind:'bar'},
    {id:'c-pbr', title:'PBR — 각 분기말 주가 기준',     key:'PBR',          kind:'line', d:2},
    {id:'c-per', title:'PER — 각 분기말 주가 기준',     key:'PER',          kind:'line', d:1},
  ];
  var charts = SPECS.map(function(sp){
    return '<div class="chart"><h3>' + sp.title + '</h3>' +
           '<div class="box" id="' + sp.id + '"></div></div>';
  }).join('') +
  '<div class="tv">차트: TradingView Lightweight Charts\u2122</div>';

  pane.innerHTML =
    '<div class="title"><h2>' + esc(c.name) + '</h2><span class="code">' + code + '</span>' +
    (c.fs_div ? '<span class="tag">' + esc(c.fs_div) + '</span>' : '') +
    '<span class="tag">' + qs.length + '개 분기</span>' +
    (c.report ? '<span class="tag">최근 ' + esc(c.report) + '</span>' : '') +
    (c.currency && c.currency !== 'KRW'
      ? '<span class="tag warn">' + esc(c.currency) + ' 공시 — PBR·PER 계산 안 함</span>' : '') +
    (c.halted
      ? '<span class="tag warn">거래정지 의심 — ' + c.halted.quarters + '개 분기 연속 종가 ' +
        c.halted.close.toLocaleString('ko-KR') + '원</span>' : '') +
    '</div>' +
    '<div class="cards">' + cards + '</div>' + charts +
    '<div style="overflow-x:auto"><table><thead>' + head + '</thead><tbody>' + body +
    '</tbody></table></div>' +
    '<div class="foot">PBR·PER·ROE는 <b>각 분기말 종가와 그 시점 주식수</b>로 계산합니다. ' +
    '오늘 주가로 과거를 계산하면 시계열이 아니라 착시가 됩니다.<br>' +
    '분기말 시점에는 아직 그 분기 실적이 공시되기 전이므로, ' +
    '"그때 이 PER로 살 수 있었다"는 뜻은 아닙니다.<br>' +
    'PER은 최근 4분기 순이익이 적자면 계산하지 않습니다(–). ' +
    '금융지주·은행·보험은 자본 구조가 달라 제조업과 같은 기준으로 비교하면 안 됩니다.' +
    (c.halted
      ? '<br><b>거래정지 의심</b>은 분기말 종가가 ' + c.halted.quarters +
        '개 분기 연속 같은 값이라는 뜻입니다(' + esc(c.halted.since) +
        ' 이후). 거래정지 목록을 직접 조회한 것이 아니라 주가가 멈춘 것을 보고 ' +
        '추정한 것이므로, 매매 전에 반드시 확인하세요.' : '') +
    '</div>';

  // DOM 에 붙은 뒤에 그려야 컨테이너 폭이 잡힌다
  disposeCharts();
  SPECS.forEach(function(sp){
    drawChart(document.getElementById(sp.id), oldFirst, seriesOld(sp.key), sp.kind, sp.d);
  });
}
// 마지막 실행 시각을 보는 사람의 시간대로, '몇 시간 전'까지 함께 보여준다
// 지수 요약 — 시장 신호 파일이 있으면 헤더에 한 줄로 띄운다.
// 없어도(수집 전이거나 실패했어도) 대시보드는 그대로 돌아가야 하므로 조용히 넘어간다.
fetch('market_signal.json', {cache:'no-store'})
  .then(function(r){ if(!r.ok) throw 0; return r.json(); })
  .then(function(p){
    var r = p.computed || p, el = document.getElementById('mkt');
    if (!r || !r.indices) return;
    var arrow = {'상승':'▲', '하락':'▼', '횡보':'–'};
    var html = Object.keys(r.indices).map(function(k){
      var d = r.indices[k];
      var c = d.disparity > 0 ? 'u' : d.disparity < 0 ? 'd' : '';
      // 종가는 물들이지 않는다. 색까지 입히면 이격도가 '오늘 등락률'로 읽힌다.
      // 같은 이유로 숫자 앞에 '이격'을 붙여 무엇의 %인지 못 박는다.
      return '<span><i>' + k + '</i><b>' + d.close.toLocaleString('ko-KR') + '</b> ' +
        '<i>이격</i><b class="' + c + '" title="20일선 대비 이격도">' +
        (d.disparity > 0 ? '+' : '') + d.disparity.toFixed(1) + '%</b> ' +
        '<span class="' + c + '" title="20일선 방향 ' + d.trend + '">' +
        (arrow[d.trend] || '') + '</span></span>';
    }).join('');
    if (r.ratio) html += '<span><i>강세</i>' + esc(r.ratio.leader) + '</span>';
    el.innerHTML = html + '<span class="more">시장 신호 &rarr;</span>';
    el.hidden = false;
  })
  .catch(function(){});

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
        halted = frozen_price_run(rec)
        data[rec["code"]] = {
            "name": rec.get("name") or rec["code"],
            "quarters": ts["quarters"],
            "metrics": ts["metrics"],
            "fs_div": {"CFS": "연결", "OFS": "별도"}.get(latest_slot.get("fs_div"), ""),
            "report": latest_slot.get("report_nm", ""),
            "currency": (latest_slot.get("currency") or "KRW").upper(),
            "halted": halted,
        }
        companies.append({"code": rec["code"],
                          "name": rec.get("name") or rec["code"],
                          "latest": ts["quarters"][0],
                          "halted": bool(halted)})
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
<div class="hrow"><h1>코스피 분기 실적 시계열</h1>
<a class="navlink" href="index.html">시장 신호 &rarr;</a></div>
<div class="meta">{len(companies)}종목 · {html.escape(span)} ·
공시가 뜨면 자동으로 수집·갱신됩니다</div>
<div class="meta">{progress}</div>
<div class="meta" id="built"></div>
<a class="mkt" id="mkt" href="index.html" hidden></a>
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
           '<meta name="viewport" content="width=device-width,initial-scale=1,'
           'viewport-fit=cover">'
           '<title>코스피 분기 실적 시계열</title>'
           # 홈 화면에 추가하면 주소창 없이 앱처럼 뜬다
           '<link rel="manifest" href="manifest.webmanifest">'
           '<meta name="theme-color" content="#0f1115">'
           '<link rel="icon" href="icon-192.png">'
           '<link rel="apple-touch-icon" href="apple-touch-icon.png">'
           '<meta name="apple-mobile-web-app-capable" content="yes">'
           '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
           '<meta name="apple-mobile-web-app-title" content="코스피 실적">'
           f'<style>{CSS}</style></head><body>{body}'
           # 차트 라이브러리는 저장소 안에 둔 것을 쓴다 (오프라인·자체완결)
           '<script src="assets/vendor/lightweight-charts.standalone.production.js">'
           '</script>'
           f'<script>{script}</script>{SW_REGISTER}</body></html>')

    path = os.path.join(out_dir, "stocks.html")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    # 첫 화면이 읽어갈 "조건에 걸린 종목" 목록. 방금 계산한 지표를 다시 쓰므로
    # 새로 수집하는 것은 없다.
    screen.save(screen.build(data, all_q[0] if all_q else ""), out_dir)
    # Pages가 _로 시작하는 경로를 Jekyll로 처리하지 않게 한다
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    # 서비스워커 캐시 이름은 페이지 내용의 해시로 짓는다. 시각을 쓰면 안 된다 —
    # built_at 은 소급 수집이 끝난 뒤로 고정이라 sw.js 가 매번 같아지고,
    # 그러면 브라우저가 새 빌드를 영영 감지하지 못해 갱신 알림이 죽는다.
    # 해시로 두면 숫자가 바뀔 때만 정확히 바뀐다.
    _write_pwa(out_dir, hashlib.sha256(doc.encode("utf-8")).hexdigest()[:12])
    return path


MANIFEST = {
    "name": "코스피 분기 실적 시계열",
    "short_name": "코스피 실적",
    "description": "DART 공시로 만든 분기별 실적·PBR·PER 시계열",
    "start_url": ".",
    "scope": ".",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0f1115",
    "theme_color": "#0f1115",
    "lang": "ko",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}

# 서비스워커. index.html 은 네트워크 우선 — 데이터가 본문에 박혀 있으므로 캐시를
# 먼저 쓰면 낡은 숫자를 보게 된다. 아이콘 같은 정적 파일은 캐시 우선.
# 오프라인이면 마지막으로 받은 화면을 그대로 띄운다.
SW_JS = """\
// 캐시를 둘로 나눈다.
//   ASSETS — 폰트·차트 라이브러리·아이콘. 내용이 바뀌지 않는다.
//   PAGES  — html 과 지수 json. 데이터가 바뀌면 같이 바뀐다.
// 하나로 두면 캐시 이름이 페이지 해시라 데이터가 바뀌는 날마다 캐시가 통째로
// 갈리고, 폰트 700KB 를 매일 다시 받게 된다.
const V = '__VERSION__';
const PAGES = 'pages-' + V;
const ASSETS = 'assets-__ASSETV__';
const STATIC = ['./assets/vendor/lightweight-charts.standalone.production.js',
                './assets/fonts/noto-serif-kr-korean-400.woff2',
                './assets/fonts/noto-serif-kr-latin-400.woff2',
                './assets/fonts/pretendard-400.woff2',
                './assets/fonts/pretendard-600.woff2',
                './assets/fonts/ibm-plex-mono-400.woff2',
                './assets/fonts/ibm-plex-mono-500.woff2',
                './icon-192.png', './icon-512.png', './apple-touch-icon.png',
                './manifest.webmanifest'];
// 첫 화면이 읽는 데이터 파일들. 숫자라서 페이지와 같이 다뤄야 한다 —
// 캐시 우선으로 두면 어제 값을 오늘처럼 보여주고, 아예 안 담으면 오프라인에서
// 화면이 통째로 빈다.
const DATA_FILES = ['market_signal.json', 'screen.json', 'market_tree.json',
                    'market_calendar.json', 'market_macro.json',
                    'market_etf.json', 'market_news.json'];
const SHELL = ['./', './index.html', './stocks.html']
                .concat(DATA_FILES.map(f => './' + f));

self.addEventListener('install', e => {
  // addAll 은 하나라도 실패하면 전부 취소된다 — 경로 오타 하나에 오프라인이
  // 통째로 죽는다는 뜻이다. 하나씩 담고 실패는 넘긴다.
  const put = (name, urls) => caches.open(name)
    .then(c => Promise.all(urls.map(u => c.add(u).catch(() => {}))));
  e.waitUntil(Promise.all([put(ASSETS, STATIC), put(PAGES, SHELL)])
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks
      .filter(k => k !== PAGES && k !== ASSETS)
      .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const path = new URL(req.url).pathname;
  // 지수 요약도 페이지와 같이 다룬다. 캐시 우선으로 두면 어제 숫자를 오늘 값처럼
  // 보여주고, 캐시를 아예 안 하면 오프라인에서 시장 화면이 통째로 빈다.
  const isPage = req.mode === 'navigate' || path.endsWith('/') ||
                 path.endsWith('index.html') || path.endsWith('stocks.html') ||
                 DATA_FILES.some(f => path.endsWith(f));
  if (isPage) {
    e.respondWith(fetch(req)
      .then(r => { const copy = r.clone();
                   caches.open(PAGES).then(c => c.put(req, copy)); return r; })
      .catch(() => caches.match(req).then(r => r || caches.match('./index.html'))));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
});
"""

# 새 빌드가 올라오면 알려준다. 수시로 열어보는 용도라 '언제 바뀌었는지'가 중요하다.
SW_REGISTER = """<script>
if ('serviceWorker' in navigator) {
  addEventListener('load', function(){
    navigator.serviceWorker.register('sw.js').then(function(reg){
      reg.addEventListener('updatefound', function(){
        var sw = reg.installing;
        if (!sw) return;
        sw.addEventListener('statechange', function(){
          // 이미 돌고 있던 워커가 있는데 새 워커가 대기 = 새 빌드가 올라왔다
          if (sw.state === 'installed' && navigator.serviceWorker.controller) {
            var bar = document.createElement('div');
            bar.className = 'newbuild';
            bar.textContent = '새 데이터가 있습니다 — 눌러서 새로고침';
            bar.onclick = function(){ location.reload(); };
            document.body.appendChild(bar);
          }
        });
      });
      setInterval(function(){ reg.update(); }, 15 * 60 * 1000);
    }).catch(function(){});
  });
}
</script>"""


def _write_pwa(out_dir: str, version: str) -> None:                      # noqa: D401
    """
    홈 화면에 추가해 앱처럼 쓰기 위한 파일들.

    아이콘(icon-*.png, apple-touch-icon.png)은 저장소에 그대로 두고 여기서
    건드리지 않는다. 매 빌드마다 다시 만들 이유가 없고, build() 는 docs/ 를
    청소하지 않으므로 그대로 살아남는다.
    """
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w", encoding="utf-8") as fp:
        json.dump(MANIFEST, fp, ensure_ascii=False, indent=1)
    # version 은 페이지 내용의 해시다. 내용이 바뀔 때만 sw.js 가 바뀌므로
    # 갱신 알림이 '진짜 새 데이터일 때만' 뜬다.
    # 정적 자산 캐시는 파일 목록이 바뀔 때만 갈리면 된다. 페이지 해시를 쓰면
    # 데이터가 바뀌는 날마다 폰트 700KB 를 다시 받는다.
    asset_v = hashlib.sha256(
        "|".join(sorted(_asset_files(out_dir))).encode("utf-8")).hexdigest()[:8]
    with open(os.path.join(out_dir, "sw.js"), "w", encoding="utf-8") as fp:
        fp.write(SW_JS.replace("__VERSION__", version or "dev")
                      .replace("__ASSETV__", asset_v))


def _asset_files(out_dir: str) -> list:
    """캐시 이름을 정하는 데 쓰는 정적 자산 목록 (이름+크기)."""
    out = []
    for root, _dirs, files in os.walk(os.path.join(out_dir, "assets")):
        for f in files:
            path = os.path.join(root, f)
            out.append(f"{os.path.relpath(path, out_dir)}:{os.path.getsize(path)}")
    return out
