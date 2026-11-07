export const goldSources = [
  ['Players', 'Герои'], ['Assists', 'Ассисты'], ['LaneCreeps', 'Крипы линии'],
  ['Neutrals', 'Нейтралы'], ['Bosses', 'Боссы/объекты'], ['Treasure', 'Сокровища'],
  ['Denies', 'Денаи'], ['Other', 'Прочее'],
];
export const sourceColors = ['#c64b40', '#dc8028', '#b69a19', '#428d55', '#9a65b6', '#2b9790', '#7c858f', '#b17b96', '#8b97a7'];
const amber = ['#b87417', '#d09e25', '#de6231', '#a25229', '#a69b35', '#e99864'];
const sapphire = ['#397ee0', '#6c66d9', '#1998ad', '#8a68bc', '#377694', '#619bc0'];
const number = value => Math.round(value).toLocaleString('ru-RU');
const minutes = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');

export function playerColor(players, player) {
  const team = player.team.startsWith('Amber') ? amber : sapphire;
  return team[players.filter(p => p.team === player.team).findIndex(p => p.slot === player.slot) % team.length];
}

// Align sparse snapshots by carrying the latest observed value forward.
// Before a player's first observation the value is unknown, not an invented zero.
function aligned(points, times, value) {
  let index = -1;
  return times.map(time => {
    while (index + 1 < points.length && points[index + 1].t <= time) index++;
    return index < 0 ? null : value(points[index], time);
  });
}
function allTimes(lists) {
  return [...new Set(lists.flatMap(points => points.map(point => point.t)))].sort((a, b) => a - b);
}
function sum(values, index) {
  const known = values.map(row => row[index]).filter(value => value !== null);
  return known.length ? known.reduce((a, b) => a + b, 0) : null;
}

function createPlot(host, tooltip, series, data) {
  if (host.plot) host.plot.destroy();
  tooltip.hidden = true;
  tooltip.replaceChildren();
  const style = getComputedStyle(document.documentElement);
  const foreground = style.getPropertyValue('--mut').trim();
  const grid = style.getPropertyValue('--line').trim();
  const plot = new uPlot({
    width: Math.max(200, host.clientWidth), height: 300,
    legend: { show: false },
    scales: { x: { time: false } },
    series: [{ label: 'Время' }, ...series.map(s => ({ ...s, width: 2, points: { show: false } }))],
    axes: [
      { stroke: foreground, grid: { stroke: grid }, values: (_, values) => values.map(minutes), size: 36 },
      { stroke: foreground, grid: { stroke: grid }, values: (_, values) => values.map(number), size: 64 },
    ],
    cursor: { drag: { x: false, y: false }, y: false, points: { show: false } },
    hooks: { setCursor: [chart => {
      const index = chart.cursor.idx;
      if (index == null || chart.cursor.left < 0) { tooltip.hidden = true; return; }
      const title = document.createElement('strong'); title.textContent = minutes(chart.data[0][index]);
      const rows = document.createElement('div'); rows.className = 'tooltip-values';
      chart.series.slice(1).map((s, i) => ({ series: s, value: chart.data[i + 1][index] }))
        .filter(row => row.series.show && row.value != null)
        .sort((a, b) => b.value - a.value)
        .forEach(({ series: s, value }) => {
          const row = document.createElement('div'); row.className = 'tooltip-row'; row.dataset.value = value;
          const label = document.createElement('span'); label.textContent = s.label;
          const dot = document.createElement('i'); dot.className = 'swatch'; dot.style.background = s._stroke;
          label.prepend(dot);
          const text = document.createElement('strong'); text.textContent = number(value);
          row.append(label, text); rows.append(row);
        });
      tooltip.replaceChildren(title, rows); tooltip.hidden = false;
    }] },
  }, data, host);
  // Keep the active plot on its host for resizing and series updates.
  host.plot = plot;
  return plot;
}

export function setupCharts(data, playerButton) {
  const players = data.players;
  const selected = new Set(players.map(p => p.slot));
  const main = document.querySelector('#timeline');
  const gold = document.querySelector('#gold-chart');
  const metric = document.querySelector('#metric');
  const totals = document.querySelector('#team-totals');
  const legend = document.querySelector('#player-legend');
  const inputs = new Map();
  let teamMode = false;

  for (const player of players) {
    const entry = document.createElement('div'); entry.className = 'legend-entry';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = true;
    checkbox.setAttribute('aria-label', 'Линия ' + player.name);
    inputs.set(player.slot, checkbox);
    const label = playerButton(player);
    label.style.color = playerColor(players, player);
    const hero = document.createElement('small'); hero.textContent = player.hero; label.append(hero);
    entry.append(checkbox, label); legend.append(entry);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selected.add(player.slot); else selected.delete(player.slot);
      teamMode = false; render();
    });
  }

  function renderMain() {
    const lists = players.map(p => data.playerTimeline?.[p.slot] || []);
    const times = allTimes(lists);
    const values = lists.map(points => aligned(points, times, (point, time) => metric.value === 'gpm' ? (time > 0 ? point.netWorth / (time / 60) : 0) : point[metric.value] ?? null));
    let series, rows;
    if (teamMode) {
      series = [{ label: 'Янтарь', stroke: amber[0] }, { label: 'Сапфир', stroke: sapphire[0] }];
      rows = ['Amber', 'Sapphire'].map(team => times.map((_, i) => sum(values.filter((_, j) => players[j].team.startsWith(team)), i)));
    } else {
      series = players.map(p => ({ label: p.name, stroke: playerColor(players, p), show: selected.has(p.slot) }));
      rows = values;
    }
    createPlot(main, document.querySelector('#timeline-tooltip'), series, [times, ...rows]);
  }

  function renderGold() {
    const lists = players.filter(p => selected.has(p.slot)).map(p => data.goldTimeline?.[p.slot] || []);
    const times = allTimes(lists);
    const keys = [...new Set(lists.flatMap(points => points.flatMap(point => Object.keys(point).filter(k => k !== 't'))))];
    const sources = keys.map(key => [key, goldSources.find(([k]) => key === k)?.[1] || 'Другой источник']);
    const series = sources.map(([, label], i) => ({ label, stroke: sourceColors[i % sourceColors.length] }));
    const rows = sources.map(([key]) => {
      const values = lists.map(points => aligned(points, times, point => point[key] ?? 0));
      return times.map((_, i) => sum(values, i));
    });
    createPlot(gold, document.querySelector('#gold-tooltip'), series, [times, ...rows]);
    const sourceLegend = document.querySelector('#gold-legend'); sourceLegend.replaceChildren();
    if (!lists.length) sourceLegend.textContent = 'Выберите хотя бы одного игрока выше.';
    for (const item of series) {
      const label = document.createElement('span'); label.textContent = item.label;
      const dot = document.createElement('i'); dot.className = 'swatch'; dot.style.background = item.stroke;
      label.prepend(dot); sourceLegend.append(label);
    }
  }

  function render() {
    totals.setAttribute('aria-pressed', String(teamMode));
    for (const [slot, checkbox] of inputs) checkbox.checked = selected.has(slot);
    renderMain(); renderGold();
  }
  metric.addEventListener('change', renderMain);
  totals.addEventListener('click', () => { teamMode = !teamMode; render(); });
  for (const button of document.querySelectorAll('[data-team]')) button.addEventListener('click', () => {
    selected.clear();
    for (const p of players) if (button.dataset.team === 'all' || p.team.startsWith(button.dataset.team)) selected.add(p.slot);
    teamMode = false; render();
  });
  const observer = new ResizeObserver(entries => {
    for (const entry of entries) {
      const chart = entry.target.plot;
      const width = Math.max(200, Math.floor(entry.contentRect.width));
      if (chart && Math.abs(chart.width - width) > 1) chart.setSize({ width, height: 300 });
    }
  });
  observer.observe(main); observer.observe(gold);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
  render();
}
