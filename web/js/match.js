import { remoteMatch } from './profile.js';
import { link } from './auth.js';
import { setupReport, portrait, enhancePlayer } from './report-v2.js';
import { setupCharts, goldSources, sourceColors } from './charts.js';

const $ = selector => document.querySelector(selector);
const format = number => Number(number ?? 0).toLocaleString('ru-RU');
const teamClass = team => String(team).startsWith('Amber') ? 'amber' : 'sapphire';
const teamName = team => String(team).startsWith('Amber') ? 'Янтарь' : 'Сапфир';
function el(tag, text = '', className) {
  const node = document.createElement(tag); node.textContent = text ?? '—';
  if (className) node.className = className;
  return node;
}
function table(target, headers, rows) {
  const head = el('thead'), heading = el('tr'), body = el('tbody');
  for (const label of headers) { const cell = el('th'); cell.scope = 'col'; cell.append(label instanceof Node ? label : document.createTextNode(label)); heading.append(cell); }
  head.append(heading);
  for (const row of rows) {
    const tr = el('tr');
    for (const value of row) { const cell = el('td'); cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); tr.append(cell); }
    body.append(tr);
  }
  target.replaceChildren(head, body);
}

function svgNode(tag, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function deathMap(players) {
  const svg = svgNode('svg', { viewBox: '0 0 400 300', class: 'map', role: 'img', 'aria-label': 'Позиции смертей игроков' });
  for (const y of [75, 150, 225]) svg.append(svgNode('line', { x1: 0, x2: 400, y1: y, y2: y }));
  for (const x of [100, 200, 300]) svg.append(svgNode('line', { y1: 0, y2: 300, x1: x, x2: x }));
  for (const player of players) for (const death of player.deathDetails || []) {
    if (!death.pos || !Number.isFinite(death.pos.x) || !Number.isFinite(death.pos.y)) continue;
    const circle = svgNode('circle', { cx: 200 + death.pos.x / 10000 * 190, cy: 150 - death.pos.y / 10000 * 140, r: 3.5, fill: 'var(--' + teamClass(player.team) + ')', opacity: .8 });
    const title = svgNode('title'); title.textContent = death.time + ' ' + player.name + ' ← ' + death.killer; circle.append(title); svg.append(circle);
  }
  return svg;
}

function chips(items, abilities = false) {
  const container = el('div', '', 'chips');
  for (const item of [...items].sort((a, b) => a.t - b.t)) {
    const chip = el('span', item.time + ' ' + item.name + (abilities && item.upgrade ? ' · улучшение' : ''), 'chip');
    // Slot and other data are never treated as markup or CSS selectors.
    if (['WeaponMod', 'Armor', 'Tech'].includes(item.slot)) chip.classList.add(item.slot);
    if (item.sold) { chip.classList.add('sold'); chip.append(document.createTextNode(' · продан ' + item.sold)); }
    if (item.imbued) chip.append(document.createTextNode(' · ' + item.imbued));
    container.append(chip);
  }
  if (!items.length) container.textContent = 'Нет данных';
  return container;
}

async function load() {
  const id = location.pathname.split('/').filter(Boolean).pop();
  const response = await fetch('/api/matches/' + encodeURIComponent(id));
  if (response.status === 404 && /^\d+$/.test(id)) { await remoteMatch(id); return; }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось загрузить матч');
  const m = data.match, players = data.players;
  const bySlot = new Map(players.map(p => [p.slot, p]));
  const dialog = $('#player-card');
  $('#close-card').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });

  function playerButton(player, hero = false) {
    const button = el('button', hero ? player.hero : player.name, 'player-link ' + teamClass(player.team));
    if (hero) button.prepend(portrait(player));
    button.type = 'button'; button.addEventListener('click', () => openPlayer(player));
    if (!hero && player.accountId) {
      const wrap = el('span'); wrap.append(link(player.name, '/p/' + player.accountId), document.createTextNode(' · '), button); button.textContent = 'Карточка'; button.setAttribute('aria-label', player.name); return wrap;
    }
    return button;
  }
  function named(slot, name) { return bySlot.has(slot) ? playerButton(bySlot.get(slot)) : el('span', name); }
  function openPlayer(player) {
    $('#player-title').textContent = player.name + ' — ' + player.hero;
    const content = $('#player-content'); content.querySelectorAll('.chart').forEach(h => h.plot?.destroy()); content.replaceChildren();
    content.append(el('p', teamName(player.team) + ' · ' + player.kills + '/' + player.deaths + '/' + player.assists + ' · Нетворс ' + format(player.netWorth), 'mut'));
    content.append(el('h3', 'Билд по времени'), chips(player.items || []));
    content.append(el('h3', 'Способности'), chips(player.abilities || [], true));
    content.append(el('h3', 'Смерти'));
    const list = el('ol', '', 'death-list');
    for (const death of player.deathDetails || []) {
      const row = el('li', death.time + ' ← '); row.append(named(death.killerSlot, death.killer), document.createTextNode(' (' + (death.killerHero || 'неизвестно') + ') · возрождение через ' + death.respawn_s + ' с')); list.append(row);
    }
    if (!list.children.length) content.append(el('p', 'Смертей нет')); else content.append(list);
    content.append(deathMap([player]), el('p', 'Схема координат смертей. Наведите на точку для подробностей.', 'mut small'));
    content.append(el('h3', 'Урон кому / от кого'));
    const wrap = el('div', '', 'table-scroll'), damage = el('table');
    table(damage, ['Игрок', 'Нанесено', 'Получено'], players.filter(p => p.slot !== player.slot).map(p => [playerButton(p), format(data.damageMatrix[player.slot]?.[p.slot]), format(data.damageMatrix[p.slot]?.[player.slot])]));
    wrap.append(damage); content.append(wrap);
    if (!dialog.open) dialog.showModal();
    enhancePlayer(content, player, m);
    dialog.scrollTop = 0;
  }

  document.title = 'Deadlock · матч ' + m.matchId;
  $('h1').textContent = 'Deadlock · матч ' + m.matchId;
  const modes = { k_ECitadelMatchMode_Unranked: 'Без рейтинга', k_ECitadelMatchMode_Ranked: 'Рейтинговый', k_ECitadelGameMode_Normal: 'Обычный режим' };
  $('#subtitle').textContent = new Date(m.startUTC).toLocaleString('ru-RU') + ' · ' + m.duration + ' · ' + (modes[m.matchMode] || 'Матч') + ' / ' + (modes[m.gameMode] || 'Другой режим') + ' · ' + m.server;
  const totals = Object.entries(m.teamTotals).sort(([a], [b]) => a.localeCompare(b));
  const kpis = [
    ['Победитель', teamName(m.winner)], ['Счёт убийств', totals.map(([, t]) => t.kills).join(' : ')],
    ['Нетворс', totals.map(([, t]) => format(t.netWorth)).join(' : ')],
    ['Урон по героям', totals.map(([, t]) => format(t.heroDamage)).join(' : ')],
    ['Урон по объектам', totals.map(([, t]) => format(t.objDamage)).join(' : ')],
    ['Мидбосс', m.midBoss.map(b => b.time + ' · ' + teamName(b.claimedBy)).join(', ') || '—'],
  ];
  for (const [label, value] of kpis) {
    const card = el('div', '', 'card kpi'); card.append(el('span', label), el('b', value, label === 'Победитель' ? teamClass(m.winner) : '')); $('#kpis').append(card);
  }
  table($('#players'), ['Игрок', 'Герой', 'Линия', 'Ур.', 'У/С/П', 'КДА', 'Нетворс', 'GPM', 'Добив.', 'Денаи', 'Урон героям', 'Получ. урон', 'Урон объектам', 'Лечение'], players.map(p => [playerButton(p), playerButton(p, true), (typeof p.lane === 'object' ? p.lane?.id : p.lane), p.level, p.kills + '/' + p.deaths + '/' + p.assists, p.kda, ...['netWorth','gpm','lastHits','denies','heroDamage','damageTaken','objDamage','healing'].map(k => format(p[k]))]));

  const sourceColumns = goldSources.filter(([key]) => key !== 'Other');
  const goldRows = [...players].sort((a, b) => b.netWorth - a.netWorth).map(p => {
    const sources = sourceColumns.map(([key]) => p.gold[key]?.gold || 0);
    const orbs = Object.values(p.gold).reduce((a, source) => a + (source.orbs || 0), 0);
    const rest = Math.max(0, p.netWorth - sources.reduce((a, b) => a + b, 0) - orbs);
    const parts = [...sources, orbs, rest];
    const bar = el('div', '', 'gold-bar');
    const labels = [...sourceColumns.map(([, name]) => name), 'Орбы душ', 'Пассив и прочее'];
    parts.forEach((value, i) => {
      const segment = el('span'); segment.style.width = (p.netWorth ? value / p.netWorth * 100 : 0) + '%'; segment.style.background = sourceColors[i]; segment.title = labels[i] + ': ' + format(value); bar.append(segment);
    });
    return [playerButton(p), format(p.netWorth), format(p.gpm), ...parts.map(format), format(p.goldDeathLoss), bar];
  });
  table($('#gold'), ['Игрок', 'Всего', 'GPM', ...sourceColumns.map(([, label]) => label), 'Орбы душ', 'Пассив и прочее', 'Потеряно при смертях', 'Структура'], goldRows);
  table($('#objectives'), ['Объект', 'Чей', 'Первый урон', 'Уничтожен', 'Урон игроков'], data.objectives.map(o => [o.name, el('span', teamName(o.owner), teamClass(o.owner)), o.firstDamage, o.destroyed || 'цел', format(o.playerDamage)]));
  $('#death-map').append(deathMap(players));
  table($('#damage'), ['Кто → Кому', ...players.map(p => playerButton(p, true))], players.map(a => [playerButton(a), ...players.map(b => format(data.damageMatrix[a.slot]?.[b.slot]))]));
  const maxDamage = Math.max(1, ...Object.values(data.damageMatrix).flatMap(row => Object.values(row)));
  [...$('#damage tbody').rows].forEach((row, a) => {
    players.forEach((player, b) => {
      const value = data.damageMatrix[players[a].slot]?.[player.slot] || 0;
      row.cells[b + 1].style.backgroundColor = 'rgba(192,57,43,' + (value / maxDamage * .45) + ')';
    });
  });
  table($('#accuracy'), ['Игрок', 'Выстрелов по героям', 'Попаданий', 'Точность', 'Хедшоты от попаданий', 'Парирования', 'Самолечение', 'Смягчено урона', 'Добито крипов', 'Нейтралов', 'Потеряно золота при смертях'], players.map(p => [playerButton(p), format(p.heroShots), format(p.heroHits), p.accuracyHero == null ? '—' : p.accuracyHero + '%', p.headshotRate == null ? '—' : p.headshotRate + '%', p.parrySuccess + '/' + (p.parrySuccess + p.parryMiss), ...['selfHealing','damageMitigated','creepKills','neutralKills','goldDeathLoss'].map(k => format(p[k]))]));
  for (const player of players) {
    const details = el('details'), summary = el('summary');
    summary.append(playerButton(player), document.createTextNode(' — ' + player.hero + ' (' + player.items.length + ' предм.)'));
    details.append(summary, chips(player.items), el('h3', 'Способности'), chips(player.abilities, true)); $('#builds').append(details);
  }
  table($('#kills'), ['Время', 'Убийца', 'Жертва', 'Ассисты'], data.killFeed.map(k => {
    const assists = el('span');
    k.assists.forEach((name, i) => { if (i) assists.append(document.createTextNode(', ')); assists.append(named(k.assistSlots[i], name)); });
    const killer = el('span'); killer.append(named(k.killerSlot, k.killer), document.createTextNode(k.killerHero ? ' (' + k.killerHero + ')' : ''));
    const victim = el('span'); victim.append(named(k.victimSlot, k.victim), document.createTextNode(' (' + k.victimHero + ')'));
    return [k.time, killer, victim, assists];
  }));
  table($('#chat'), ['Время', 'Игрок', 'Кому', 'Сообщение'], data.chat.map(c => [c.time, named(c.playerSlot, c.player), c.all ? 'всем' : 'команде', c.text]));
  for (const row of $('#chat tbody').rows) row.cells[3].className = 'wrap';
  if (!data.chat.length) $('#chat').after(el('p', 'Сообщений нет', 'mut'));
  const downloads = [['match.json','Матч JSON'], ['postmatch_raw.json','Итоги JSON'], ['players.csv','Игроки CSV'], ['items.csv','Предметы CSV'], ['deaths.csv','Смерти CSV'], ['chat.csv','Чат CSV'], ['gold.csv','Золото CSV']];
  for (const [file, title] of downloads) { const link = el('a', title); link.href = '/api/matches/' + encodeURIComponent(id) + '/files/' + file; link.download = file; $('#downloads').append(link); }
  setupReport(data, openPlayer);
  $('#report').hidden = false; $('#status').hidden = true;
  if (typeof window.uPlot === 'undefined') {
    // The requested cdnjs URLs currently return 404. Keep the exact version
    // and use the same published distribution through jsDelivr as fallback.
    try {
      await Promise.all([
        new Promise((resolve, reject) => {
          const css = $('#uplot-css'); css.onload = resolve; css.onerror = reject;
          css.href = 'https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css';
        }),
        new Promise((resolve, reject) => {
          const script = document.createElement('script'); script.onload = resolve; script.onerror = reject;
          script.src = 'https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js'; document.head.append(script);
        }),
      ]);
    } catch {
      $('#timeline').textContent = 'Не удалось загрузить графики. Проверьте подключение и обновите страницу.';
      return;
    }
  }
  setupCharts(data, playerButton);
}
load().catch(error => {
  $('#status').hidden = false; $('#status').classList.add('error');
  $('#status').textContent = error instanceof TypeError || error instanceof SyntaxError ? 'Не удалось загрузить матч. Проверьте подключение и обновите страницу.' : error.message || 'Не удалось загрузить матч';
});
