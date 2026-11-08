import { api, el, link, avatar, processControl, me } from './auth.js';
import { portrait } from './report-v2.js';
import { heroes } from './heroes.js';
export const duration = s => Math.floor(Number(s || 0) / 60) + ':' + String(Math.floor(Number(s || 0) % 60)).padStart(2,'0');
const heroName = id => heroes[id] || 'Герой ' + id;
export function relativeDate(seconds) {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - seconds) / 60));
  if (minutes < 1) return 'Только что';
  if (minutes < 60) return minutes + ' мин назад';
  if (minutes < 1440) return Math.floor(minutes / 60) + ' ч назад';
  return Math.floor(minutes / 1440) + ' дн назад';
}
function summary(p) {
  const identity = el('div', '', 'profile-identity');
  const face = el('div', '', 'profile-avatar');
  face.append(el('span', (p.name || 'Игрок').trim().split(/\s+/).slice(0,2).map(w=>Array.from(w)[0]).join('').toUpperCase()), avatar(p.avatar));
  const name = el('div'); name.append(el('span','ПРОФИЛЬ ИГРОКА','eyebrow'), el('h1',p.name || 'Игрок'),el('span','Steam ID аккаунта: ' + p.accountId,'mut small'));
  identity.append(face,name);
  const chips = el('div','','profile-summary');
  const add = (label,value) => { const chip=el('div','','summary-chip'); chip.append(el('span',label,'mut small'),el('strong',value)); chips.append(chip); return chip; };
  const n=p.matches.length, wins=p.matches.filter(m=>m.won).length;
  add('Показано матчей',String(n));
  add('Победы — поражения',n ? `${wins} — ${n-wins} · ${Math.round(wins/n*100)}%` : '—');
  add('Среднее K / D / A',n ? ['kills','deaths','assists'].map(k=>(p.matches.reduce((s,m)=>s+Number(m[k] || 0),0)/n).toFixed(1)).join(' / ') : '—');
  const counts=new Map(); p.matches.forEach(m=>counts.set(m.heroId,(counts.get(m.heroId)||0)+1));
  const favorite=[...counts].sort((a,b)=>b[1]-a[1])[0];
  const chip=add('Любимый герой',favorite ? heroName(favorite[0]) : '—');
  if(favorite) chip.prepend(portrait({heroId:favorite[0],hero:heroName(favorite[0])}));
  document.querySelector('#profile-heading').replaceChildren(identity,chips);
}
async function refreshControl(p) {
  const box=el('div','','profile-refresh');
  box.append(el('span','Нет свежих матчей? deadlock-api обновляет историю с задержкой.','mut small'));
  const user=await me;
  if(!user.accountId) { box.append(link('Войти, чтобы обновить из Steam','/auth/login')); }
  else {
    const button=el('button','Обновить из Steam'); button.type='button';
    const note=el('span','','small'); note.setAttribute('role','status');
    button.onclick=async()=>{ button.disabled=true; note.textContent='Запрашиваю историю у Steam… (до минуты)';
      try { await api('/api/players/'+p.accountId+'/refresh',{method:'POST'}); note.textContent='Готово'; load(); }
      catch(e) { note.textContent=e.message; button.disabled=false; } };
    box.append(button,note);
  }
  document.querySelector('#profile-heading').append(box);
}
async function load() {
  const list=document.querySelector('#profile-matches'), status=document.querySelector('#profile-status');
  status.className='mut'; status.setAttribute('role','status'); status.textContent='Загрузка профиля…';
  list.setAttribute('aria-busy','true'); list.replaceChildren();
  for(let i=0;i<8;i++) { const row=el('div','','profile-skeleton'); row.setAttribute('aria-hidden','true'); row.append(el('i'),el('i'),el('i')); list.append(row); }
  try {
    const p = await api('/api/players/' + encodeURIComponent(location.pathname.split('/').pop()));
    document.title = 'Deadlock · ' + p.name;
    summary(p); refreshControl(p); list.replaceChildren(); status.textContent='';
    document.querySelector('.profile-columns').hidden=!p.matches.length;
    if(!p.matches.length) { const empty=el('div','','profile-empty'); empty.append(el('h3','Матчей пока нет'),el('p','Здесь появится история последних матчей игрока.','mut')); list.append(empty); }
    for (const m of p.matches) {
      const row=el('article','','profile-match ' + (m.won?'is-win':'is-loss'));
      const identity=link('', '/m/'+encodeURIComponent(m.matchId)); identity.className='hero-identity match-row-link'; identity.setAttribute('aria-label',heroName(m.heroId)+' · Матч '+m.matchId);
      identity.append(portrait({heroId:m.heroId,hero:heroName(m.heroId)}),el('strong',heroName(m.heroId)));
      const result=el('div','','match-result'); const date=el('time',relativeDate(m.startTime),'mut small'); date.dateTime=new Date(m.startTime*1000).toISOString(); date.title=new Date(m.startTime*1000).toLocaleString('ru-RU');
      row.title=date.title;
      result.append(el('strong',m.won?'Победа':'Поражение'),date);
      const kda=el('div','','match-kda'); kda.append(el('strong',[m.kills,m.deaths,m.assists].join(' / ')),el('small',((m.kills+m.assists)/Math.max(1,m.deaths)).toFixed(2)+' KDA','mut'));
      row.append(identity,result,el('span',duration(m.durationS),'match-duration mut'),kda,el('span',Number(m.netWorth).toLocaleString('ru-RU'),'match-worth'),processControl(m.matchId,m.state,true));
      const more=el('button','Игроки ▾','match-players-toggle'); more.type='button'; more.setAttribute('aria-expanded','false');
      const box=el('div','','match-players'); box.hidden=true;
      more.onclick=async()=>{ const open=box.hidden; box.hidden=!open; more.setAttribute('aria-expanded',String(open)); more.textContent=open?'Игроки ▴':'Игроки ▾';
        if(open && !box.dataset.loaded) { box.textContent='Загрузка…';
          try { const r=await api('/api/remote/'+m.matchId); box.replaceChildren(); box.dataset.loaded='1';
            for (const [team,cls] of [[0,'amber'],[1,'sapphire']]) { const col=el('div','','match-players-team '+cls);
              for (const q of r.players.filter(q=>q.team===team)) { const line=el('div','','match-player'+(q.accountId===p.accountId?' is-self':''));
                line.append(portrait({heroId:q.heroId,hero:heroName(q.heroId)}),link(q.name||String(q.accountId),'/p/'+q.accountId),el('span',[q.kills,q.deaths,q.assists].join('/'),'mut small')); col.append(line); }
              box.append(col); } }
          catch(e) { box.textContent='Не удалось загрузить игроков: '+e.message; } } };
      row.lastElementChild.prepend(more); list.append(row,box);
      if(m.state==='queued') watchQueued(row,p.accountId,m.matchId);
    }
  } catch {
    list.replaceChildren(); document.querySelector('.profile-columns').hidden=true;
    status.className='profile-empty error'; status.setAttribute('role','alert');
    const retry=el('button','Попробовать снова'); retry.type='button'; retry.onclick=load;
    status.replaceChildren(el('h3','API временно недоступен'),el('p','Не удалось загрузить матчи. Попробуйте немного позже.','mut'),retry);
  } finally { list.setAttribute('aria-busy','false'); }
}
if (document.querySelector('#profile-status')) load();
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
      row.append(portrait({heroId:p.heroId,hero}),link(p.name || String(p.accountId),'/p/'+p.accountId),el('span',hero),el('span',[p.kills,p.deaths,p.assists].join(' / ')),el('span','Нетворс: '+Number(p.netWorth).toLocaleString('ru-RU'))); section.append(row);
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
      if (match && match.state !== 'queued') { card.querySelector('.process-control').replaceWith(processControl(matchId,match.state,true)); return; }
    } catch (e) { card.querySelector('.process-control').title = 'API временно недоступен. Повторяем запрос…'; }
  }
}
