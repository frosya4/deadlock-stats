import { createPlot, sourceColors } from './charts.js';
const $ = s => document.querySelector(s);
const fmt = n => n == null ? '—' : Number(n).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
function el(tag, text = '', cls = '') { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; }
export function portrait(p) {
  const wrap = el('span', (p.hero || '?').slice(0, 1), 'portrait');
  if (p.heroId != null) {
    const img = document.createElement('img'); img.alt = ''; img.src = '/img/heroes/' + encodeURIComponent(p.heroId) + '.png';
    img.addEventListener('error', () => img.remove()); wrap.append(img);
  }
  return wrap;
}
function grade(p) { if (!/^[SABCD]$/.test(p.grade)) return document.createDocumentFragment(); return el('span', /^[SABCD]$/.test(p.grade) ? p.grade : '—', 'grade grade-' + (/^[SABCD]$/.test(p.grade) ? p.grade : 'unknown')); }
function lane(p) {
  const names = { won: 'Выиграл линию', even: 'Ровно линию', lost: 'Проиграл линию' };
  if (!p.lane || !names[p.lane.result]) return document.createDocumentFragment();
  const n = el('span', names[p.lane.result] + ' · ' + (p.lane.diffPct > 0 ? '+' : '') + fmt(p.lane.diffPct) + '%', 'lane lane-' + p.lane.result);
  n.title = 'Линия ' + p.lane.id + ' · ' + fmt(p.lane.teamNW) + ' / ' + fmt(p.lane.enemyNW) + ' · ' + fmt(p.lane.at / 60) + ' мин'; return n;
}
const parts = { kills:'Убийства', assists:'Ассисты', deaths:'Смерти', lastHits:'Добивания', denies:'Денаи', gpm:'GPM', heroDamage:'Урон героям', healing:'Лечение', objDamage:'Урон объектам', participation:'Участие', midboss:'Мидбосс', lane:'Линия' };
function fantasy(p) {
  if (p.fantasy?.total == null) return document.createDocumentFragment();
  const d = el('div', '', 'fantasy'), summary = el('button', fmt(p.fantasy?.total) + ' ФО');
  summary.type = 'button'; summary.setAttribute('aria-expanded', 'false'); d.append(summary);
  summary.addEventListener('click', () => {
    const open = d.toggleAttribute('open'); summary.setAttribute('aria-expanded', String(open));
  });
  const tip = el('div', '', 'fantasy-parts'); tip.setAttribute('role', 'tooltip');
  for (const [key, label] of Object.entries(parts)) if (p.fantasy?.parts?.[key] != null) tip.append(el('div', label + ': ' + fmt(p.fantasy.parts[key])));
  if (!tip.childNodes.length) tip.textContent = 'Нет данных';
  d.append(tip); d.addEventListener('click', e => e.stopPropagation()); return d;
}
export function setupReport(data, openPlayer) {
  const report = $('#report'), nav = el('nav', '', 'tabs'); nav.setAttribute('aria-label', 'Разделы матча');
  const names = { overview:'Обзор', players:'Игроки', gold:'Золото', fights:'Бои', chat:'Чат' }, panels = {};
  for (const [key, name] of Object.entries(names)) {
    const link = el('a', name); link.href = '#' + key; nav.append(link);
    panels[key] = el('div', '', 'tab-panel'); panels[key].id = 'panel-' + key;
  }
  panels.overview.append($('#kpis'));
  for (const [selector, key] of [['#timeline','players'],['#players','players'],['#builds','players'],['#gold','gold'],['#damage','fights'],['#accuracy','fights'],['#kills','fights'],['#chat','chat']]) panels[key].append($(selector).closest('section'));
  panels.fights.append($('#death-map').closest('.grid2'));
  const downloads = $('#downloads').closest('section');
  report.prepend(nav); for (const panel of Object.values(panels)) report.insertBefore(panel, downloads);
  const mvp = data.players.find(p => p.mvp) || data.players.find(p => data.match.mvpSlot != null && p.slot === data.match.mvpSlot);
  if (mvp) {
    const card = el('button', '', 'mvp card'); card.id = 'mvp-card';
    const text = el('span'); text.append(el('span', '★ MVP МАТЧА', 'eyebrow'), el('strong', mvp.name + ' · ' + mvp.hero), el('span', mvp.kills + ' / ' + mvp.deaths + ' / ' + mvp.assists + ' · ' + fmt(mvp.fantasy?.total) + ' фэнтези-очков'));
    card.append(portrait(mvp), text, grade(mvp)); card.addEventListener('click', () => openPlayer(mvp)); panels.overview.append(card);
  }
  panels.overview.append(el('h2', data.players.some(p => p.rank != null) ? 'Рейтинг игроков' : 'Игроки матча'));
  if (data.players.some(p => p.fantasy)) panels.overview.append(el('p', 'Фэнтези-очки · наведите или нажмите на очки для разбивки', 'mut small'));
  const ranking = el('div', '', 'ranking'); ranking.id = 'ranking';
  for (const p of [...data.players].sort((a,b) => (a.rank ?? 99) - (b.rank ?? 99))) {
    const row = el('div', '', 'rank-row ' + (String(p.team).startsWith('Amber') ? 'team-amber' : 'team-sapphire'));
    row.tabIndex = 0; row.setAttribute('role', 'group'); row.setAttribute('aria-label', p.name);
    const identity = el('button', '', 'rank-player'); identity.append(portrait(p)); const name = el('span', p.name); name.append(el('small', p.hero)); identity.append(name);
    row.append(el('span', p.rank == null ? '—' : '#' + p.rank, 'rank'), identity, grade(p), fantasy(p), lane(p));
    row.addEventListener('click', () => openPlayer(p)); row.addEventListener('keydown', e => { if (e.target === row && ['Enter',' '].includes(e.key)) { e.preventDefault(); openPlayer(p); } }); ranking.append(row);
  }
  panels.overview.append(ranking);
  const keyObjects = el('div', '', 'card key-objects'); keyObjects.append(el('h3', 'Ключевые объекты'));
  for (const b of data.match.midBoss || []) keyObjects.append(el('p', 'Мидбосс · ' + b.time + ' · ' + b.claimedBy));
  keyObjects.append(el('p', 'Уничтожено объектов: ' + (data.objectives || []).filter(o => o.destroyed).length)); panels.overview.append(keyObjects);
  function activate() {
    const key = Object.hasOwn(names, location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
    for (const [id, panel] of Object.entries(panels)) panel.hidden = id !== key;
    for (const link of nav.children) { if (link.hash === '#' + key) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); }
    window.dispatchEvent(new Event('resize'));
  }
  window.addEventListener('hashchange', activate); activate();
}
function watch(host) {
  const observer = new ResizeObserver(() => { if (host.clientWidth && host.plot) host.plot.setSize({width:host.clientWidth,height:300}); }); observer.observe(host); return observer;
}
export function enhancePlayer(content, p, match) {
  const top = el('div', '', 'player-stats');
  for (const [label, value] of [['Убийства',p.kills],['Смерти',p.deaths],['Ассисты',p.assists],['Нетворс',p.netWorth],['GPM',p.gpm],['Хедшоты %',p.headshotRate],['Точность %',p.accuracyHero]]) {
    if (value == null) continue;
    const tile = el('div', '', 'stat-tile'); tile.append(el('span', label), el('b', fmt(value)));
    if (label === 'Хедшоты %') {
      const bar = el('progress'); bar.max = 100; bar.value = value; bar.setAttribute('aria-label','Хедшоты'); tile.append(bar);
      if (match.hsAverage != null) tile.append(el('small', 'среднее по матчу ' + fmt(match.hsAverage) + '%'));
    }
    top.append(tile);
  }
  const badges = el('div', '', 'toolbar'); badges.append(portrait(p), grade(p), fantasy(p), lane(p));
  content.prepend(top, badges);
  if (p.hsTimeline?.length && window.uPlot) {
    const chart = el('div', '', 'chart hs-chart'), tip = el('div', '', 'chart-tooltip'); tip.hidden = true;
    badges.after(el('h3', 'Хедшоты по времени'), chart, tip);
    createPlot(chart, tip, [{label:'Хедшоты %',stroke:'#ff7373'}], [p.hsTimeline.map(x=>x.t),p.hsTimeline.map(x=>x.hsRate)]);
    const observer = watch(chart); chart.plot.hooks.destroy.push(() => observer.disconnect());
  }
}

export function setupSources(data) {
  const host = $('#gold-chart'), card = host.parentElement;
  card.querySelector('p')?.remove();
  const toolbar = el('div', '', 'toolbar'), compare = el('input'); compare.type = 'checkbox'; compare.id = 'compare';
  const toggle = el('label', 'Сравнить игроков '); toggle.append(compare); toolbar.append(toggle);
  const grid = el('div', '', 'source-grid'), panels = [];
  for (let i=0;i<2;i++) {
    const panel = el('div', '', 'source-panel'), select = el('select'); select.id = 'source-player-' + i; select.setAttribute('aria-label', i ? 'Второй игрок' : 'Игрок для золота');
    for (const p of data.players) { const option = el('option', p.name + ' · ' + p.hero); option.value = p.slot; select.append(option); }
    select.selectedIndex = Math.min(i, data.players.length - 1);
    const plot = i ? el('div','','chart') : host; if (i) plot.id='gold-compare-chart';
    const tooltip = i ? el('div','','chart-tooltip') : $('#gold-tooltip'); tooltip.hidden=true;
    const legend = i ? el('div','','source-legend') : $('#gold-legend');
    panel.append(select,el('p','','source-summary'),plot,legend,tooltip); grid.append(panel); panels.push({panel,select,plot,tooltip,legend}); select.addEventListener('change',render); watch(plot);
  }
  card.append(toolbar,grid);
  const sources = [['Players','Герои'],['Assists','Ассисты'],['LaneCreeps','Крипы линии'],['Neutrals','Нейтралы'],['Bosses','Боссы/объекты'],['Treasure','Сокровища'],['Denies','Денаи'],['Orbs','Орбы душ'],['Passive','Пассив']];
  function render() {
    grid.classList.toggle('comparing',compare.checked); panels[1].panel.hidden=!compare.checked;
    for (const {panel,select,plot,tooltip,legend} of panels) {
      if (panel.hidden) continue;
      const p = data.players.find(p => String(p.slot) === select.value), points = p?.sourceTimeline || [];
      panel.querySelector('.source-summary').textContent = p ? 'Получено золота: ' + fmt(p.netWorth) + ' · GPM: ' + fmt(p.gpm) : 'Нет игроков';
      plot.plot?.destroy(); plot.plot = null; plot.replaceChildren(); legend.replaceChildren(); tooltip.hidden=true;
      if (!points.length) { plot.textContent='Нет данных по источникам во времени'; continue; }
      const raw = [points.map(x=>x.t),...sources.map(([key])=>points.map(x=>x[key] ?? 0))];
      // Cumulative layers drawn largest first, each tooltip retains the original source value.
      const stacked = [raw[0],...sources.map((_,i)=>points.map((_,j)=>raw.slice(i+1).reduce((sum,row)=>sum+row[j],0)))];
      const series=sources.map(([,label],i)=>({label,stroke:sourceColors[i],fill:sourceColors[i]}));
      createPlot(plot,tooltip,series,stacked,raw);
      for (const s of series) { const label=el('span',s.label), dot=el('i','','swatch'); dot.style.background=s.stroke; label.prepend(dot); legend.append(label); }
    }
  }
  compare.addEventListener('change',render); document.addEventListener('themechange',render); render();
}
