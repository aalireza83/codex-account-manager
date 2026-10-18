// Apply the saved preference before first paint; storage may be unavailable.
(() => {
  let theme = null;
  try { theme = localStorage.getItem('codex-ui-theme'); } catch (_) {}
  const dark = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
})();
