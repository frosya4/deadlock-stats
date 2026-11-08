import { api, el, link, avatar, processControl } from './auth.js';
import { portrait } from './report-v2.js';
import { heroes } from './heroes.js';
export const duration = s => Math.floor(Number(s || 0) / 60) + ':' + String(Math.floor(Number(s || 0) % 60)).padStart(2,'0');
async function load() {
  const p = await api('/api/players/' + encodeURIComponent(location.pathname.split('/').pop()));
  document.title = 'Deadlock · ' + p.name;
  document.querySelector('#profile-heading').replaceChildren(avatar(p.avatar),el('h1',p.name),el('p','Steam ID аккаунта: ' + p.accountId,'mut'));
  document.querySelector('#profile-status').textContent = p.matches.length ? '' : 'Матчей пока нет';
  for (const m of p.matches) {
    const card = el('article','','card profile-match');
    const hero = heroes[m.heroId] || 'Герой ' + m.heroId;
    const title = el('h3'); title.append(link('Матч ' + m.matchId,'/m/' + m.matchId));
    const identity = el('div',hero,'hero-identity'); identity.prepend(portrait({heroId:m.heroId,hero}));
    card.append(title,identity,el('p',new Date(m.startTime*1000).toLocaleString('ru-RU') + ' · ' + duration(m.durationS),'mut'),el('strong',m.won?'Победа':'Поражение',m.won?'amber':'sapphire'),el('p','У/С/П: ' + [m.kills,m.deaths,m.assists].join(' / ') + ' · Нетворс: ' + Number(m.netWorth).toLocaleString('ru-RU')),processControl(m.matchId,m.state));
    document.querySelector('#profile-matches').append(card);
    if (m.state === 'queued') watchQueued(card, p.accountId, m.matchId);
  }
}
if (document.querySelector('#profile-status')) load().catch(e=>{ const s=document.querySelector('#profile-status'); s.textContent=e.message; s.className='error'; s.setAttribute('role','alert'); });
export async function remoteMatch(id) {
  const m = await api('/api/remote/' + id);
  document.querySelector('h1').textContent = 'Матч ' + id;
  document.querySelector('#subtitle').textContent = duration(m.duration) + ' · Победитель: ' + (m.winningTeam===0?'Янтарь':'Сапфир');
  const section = el('section','','remote-summary');
  for (const [team,name,cls] of [[0,'Янтарь','amber'],[1,'Сапфир','sapphire']]) {
    const players = m.players.filter(p=>p.team===team);
    section.append(el('h2',name + ' · ' + players.reduce((s,p)=>s+p.kills,0) + ' убийств',cls));
    for (const p of players) {
      const row = el('article','','card remote-player'); const hero = heroes[p.heroId] || 'Герой ' + p.heroId;
      row.append(portrait({heroId:p.heroId,hero}),link(String(p.accountId),'/p/'+p.accountId),el('span',hero),el('span',[p.kills,p.deaths,p.assists].join(' / ')),el('span','Нетворс: '+Number(p.netWorth).toLocaleString('ru-RU'))); section.append(row);
    }
  }
  section.append(processControl(id)); document.querySelector('#status').replaceWith(section);
}

async function watchQueued(card, accountId, matchId) {
  while (card.isConnected) {
    await new Promise(resolve => setTimeout(resolve,5000));
    if (!card.isConnected) return;
    try {
      const p = await api('/api/players/' + accountId);
      const match = p.matches.find(m => String(m.matchId) === String(matchId));
      if (match && match.state !== 'queued') { card.querySelector('.process-control').replaceWith(processControl(matchId,match.state)); return; }
    } catch (e) { card.querySelector('.process-control').textContent = e.message; }
  }
}