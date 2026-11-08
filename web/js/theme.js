const button = document.querySelector('#theme-toggle');
let theme = 'dark';
try { if (localStorage.getItem('deadlock-theme') === 'light') theme = 'light'; } catch {}
function apply() {
  document.documentElement.dataset.theme = theme;
  button.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  button.setAttribute('aria-label', button.textContent);
  document.dispatchEvent(new Event('themechange'));
}
apply();
button.addEventListener('click', () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('deadlock-theme', theme); } catch {}
  apply();
});
