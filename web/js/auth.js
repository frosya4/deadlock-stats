export function el(tag, text = '', cls = '') { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; }
export async function api(url, options) {
  const r = await fetch(url, options); const d = await r.json();
  if (!r.ok) throw new Error(d.error || 'Не удалось получить ответ сервера');
  return d;
}
export function link(text, href) { const a = el('a', text); a.href = href; return a; }
export const me = api('/api/me').catch(() => ({accountId:null}));
let account = null;
export function avatar(url) {
  const img = el('img', '', 'avatar'); img.alt = '';
  if (/^https?:\/\//.test(url)) img.src = url; else img.hidden = true;
  img.onerror = () => { img.hidden = true; }; return img;
}
async function header() {
  const host = document.querySelector('[data-auth]'); if (!host) return;
  const user = await me; account = user.accountId;
  if (account == null) { host.replaceChildren(link('Войти через Steam','/auth/login')); return; }
  const identity = link(user.steamId || String(account), '/p/' + account);
  const logout = el('button','Выйти'); logout.type = 'button';
  logout.onclick = async () => {
    logout.disabled = true;
    try { await api('/auth/logout',{method:'POST'}); account = null; host.replaceChildren(link('Войти через Steam','/auth/login')); document.dispatchEvent(new Event('auth-logout')); }
    catch (e) { logout.disabled = false; const error = el('span',e.message,'error'); error.setAttribute('role','alert'); host.append(error); }
  };
  host.replaceChildren(identity,logout);
  try { const p = await api('/api/players/' + account); identity.textContent = p.name || user.steamId; identity.prepend(avatar(p.avatar)); } catch {}
}
header();
export function processControl(id, state = 'available') {
  const host = el('div','','process-control');
  const done = () => host.replaceChildren(link('Обработан','/m/' + encodeURIComponent(id)));
  if (state === 'processed') { done(); return host; }
  if (state === 'queued') { host.textContent = 'В очереди / обрабатывается'; return host; }
  const button = el('button','Загрузка…'); button.disabled = true; button.type = 'button';
  const status = el('span'); status.setAttribute('aria-live','polite'); host.append(button,status);
  const update = () => { button.disabled = account == null; button.textContent = account == null ? 'Войдите через Steam, чтобы обработать' : 'Обработать'; };
  me.then(user => { account = user.accountId; update(); });
  document.addEventListener('auth-logout',update);
  button.onclick = async () => {
    button.disabled = true; status.removeAttribute('role'); status.textContent = 'Отправка…';
    try {
      let data = await api('/api/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({matchId:String(id)})});
      if (data.matchId) { done(); return; }
      if (!data.job) throw new Error('Сервер не вернул номер задачи');
      const job = data.job;
      while (host.isConnected) {
        data = await api('/api/jobs/' + encodeURIComponent(job));
        if (data.state === 'done') { done(); return; }
        if (data.state === 'error') throw new Error(data.error || 'Ошибка обработки');
        status.textContent = data.state === 'queued' ? 'Очередь: позиция ' + (data.position ?? '—') : 'Обработка…';
        await new Promise(resolve => setTimeout(resolve,1000));
      }
    } catch (e) { status.textContent = e instanceof TypeError ? 'Нет связи с сервером. Попробуйте снова.' : e.message; status.setAttribute('role','alert'); status.className = 'error'; }
    finally { update(); }
  };
  return host;
}
