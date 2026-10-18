// Dashboard operations run only against synthetic accounts in an isolated fixture.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const fixture = spawn(process.env.PYTHON || 'python3', [path.join(__dirname, 'ui_fixture.py')], {
    cwd: root, env: { ...process.env, CODEX_UI_TEST_PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '', browser;
  fixture.stdout.on('data', chunk => output += chunk);
  fixture.stderr.on('data', chunk => output += chunk);
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (fixture.exitCode !== null) throw Error(output);
      try { if ((await fetch(base + '/api/health')).ok) { ready = true; break; } } catch (_) {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(ready, 'Fixture did not start: ' + output);
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const created = await page.request.post(base + '/api/profiles', { data: { name: 'Dashboard API', base_url: 'https://ttmapi.site/v1', api_key: 'synthetic-dashboard-key' } });
    assert(created.ok());
    const profile = (await created.json()).profile;
    await page.goto(base);
    const row = name => page.locator('#dashboardAccounts').getByRole('group', { name, exact: true });
    await row('Work').waitFor();
    assert.equal(await page.locator('#dashboardStatistics').isVisible(), false);
    assert(await row('Personal').getByText('5-hour remaining', { exact: true }).isVisible());
    assert(await row('Personal').getByText('Weekly remaining', { exact: true }).isVisible());
    assert.match(await row('Personal').innerText(), /77%/);
    assert(!/Credits/.test(await page.locator('#dashboardAccounts').innerText()));
    assert.equal(await page.locator('.logo img').getAttribute('src'), '/assets/codex-symbol-color.svg');
    assert((await page.request.get(base + '/assets/codex-symbol-color.svg')).ok());
    assert(await row('Personal').getByRole('button', { name: 'Active', exact: true }).isDisabled());
    assert(await row('Dashboard API').getByRole('button', { name: 'Ping', exact: true }).isDisabled());
    page.once('dialog', dialog => dialog.dismiss());
    await row('Work').getByRole('button', { name: 'Switch', exact: true }).click();
    await row('Work').getByRole('button', { name: 'Switch', exact: true }).waitFor();
    assert(await row('Personal').getByRole('button', { name: 'Active', exact: true }).isDisabled());
    page.on('dialog', dialog => dialog.accept());
    await row('Work').getByRole('button', { name: 'Switch', exact: true }).click();
    await row('Work').getByRole('button', { name: 'Active', exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, '#dashboard');
    let release, requests = 0;
    const gate = new Promise(resolve => release = resolve);
    page.on('request', request => { if (new URL(request.url()).pathname === '/api/ping-account') requests++; });
    await page.route('**/api/ping-account', async route => { await gate; await route.continue(); });
    await row('Personal').getByRole('button', { name: 'Ping', exact: true }).click();
    const pending = row('Personal').getByRole('button', { name: 'Pinging…', exact: true });
    assert(await pending.isDisabled());
    await pending.dispatchEvent('click');
    release();
    await row('Personal').getByText('Ping OK', { exact: false }).waitFor();
    assert.equal(requests, 1, 'Duplicate Ping was submitted');
    assert(await row('Work').getByRole('button', { name: 'Active', exact: true }).isDisabled());
    await page.unroute('**/api/ping-account');
    await row('Dashboard API').getByRole('button', { name: 'Switch', exact: true }).click();
    await row('Dashboard API').getByRole('button', { name: 'Active', exact: true }).waitFor();
    const selected = await (await page.request.get(base + '/api/profiles')).json();
    assert.equal(selected.active_profile, profile.id);
    await row('Work').getByRole('button', { name: 'Switch', exact: true }).click();
    await row('Work').getByRole('button', { name: 'Active', exact: true }).waitFor();
    assert.equal((await (await page.request.get(base + '/api/profiles')).json()).active_profile, 'default');
    await page.route('**/api/ping-account', route => route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'Synthetic Ping failure' }) }));
    await row('Personal').getByRole('button', { name: 'Ping', exact: true }).click();
    await page.locator('#banner').getByText('Synthetic Ping failure', { exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('#dashboardAccounts button')].some(e => e.textContent === 'Ping' && !e.disabled));
    assert(await row('Personal').getByRole('button', { name: 'Ping', exact: true }).isEnabled());
    await page.locator('[data-view=activity]').click();
    assert(await page.locator('#dashboardStatistics').isVisible());
    assert.equal(Number(await page.locator('#statPings').innerText()), 1);
    const stats = await (await page.request.get(base + '/api/activity')).json();
    assert(stats.accounts.find(a => a.id === profile.id).switch.success >= 1);
    await page.locator('[data-view=settings]').click();
    const commands = {app: ['echo "custom app restart"', 'echo "custom app status"']};
    await page.locator('#restartAppCommands').fill(commands.app.join('\n'));
    assert.equal(await page.locator('#restartWebCommands').count(),0);
    await page.getByRole('button', {name: 'Save commands', exact: true}).click();
    await page.getByText('Restart commands saved. No commands were executed.', {exact: true}).waitFor();
    assert.deepEqual((await (await page.request.get(base + '/api/config')).json()).restart_commands, commands);
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('restartAppCommands').disabled);
    assert.equal(await page.locator('#restartAppCommands').inputValue(), commands.app.join('\n'));
    await page.locator('#restartAppCommands').fill('echo "unfinished');
    await page.getByRole('button', {name: 'Save commands', exact: true}).click();
    await page.waitForFunction(() => document.getElementById('restartCommandsState').textContent.includes('invalid command quoting'));
    assert.deepEqual((await (await page.request.get(base + '/api/config')).json()).restart_commands, commands);
    await page.getByRole('button', {name: 'Use defaults', exact: true}).click();
    assert.match(await page.locator('#restartAppCommands').inputValue(), /systemctl restart codex-app-server/);
    await page.getByRole('button', {name: 'Save commands', exact: true}).click();
    await page.getByText('Restart commands saved. No commands were executed.', {exact: true}).waitFor();
    await page.locator('[data-view=accounts]').click();
    assert(!/Credits/.test(await page.locator('#grid').innerText()));
    await page.locator('[data-view=dashboard]').click();
    await page.getByRole('button', {name: 'Switch to dark mode', exact: true}).click();
    const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-dashboard-controls-'));
    await page.screenshot({ path: path.join(artifacts, 'desktop.png'), fullPage: true, animations: 'disabled' });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth));
    await page.screenshot({ path: path.join(artifacts, 'mobile.png'), fullPage: true, animations: 'disabled' });
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('PASS: dashboard usage, Activity navigation, configurable restart commands and native/API switching, return to native auth, inactive-account Ping, active state, cancellation, duplicate protection, failures, refreshed stats and mobile layout.');
    console.log('Screenshots: ' + artifacts);
  } finally {
    if (browser) await browser.close();
    if (fixture.exitCode === null) {
      const stopped = new Promise(resolve => fixture.once('exit', resolve));
      fixture.kill('SIGTERM'); await stopped;
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
