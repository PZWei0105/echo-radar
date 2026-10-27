// 用 Chromium 實際打開網站檢查：node tests/ui_check.mjs <網站資料夾（含 data/）> <截圖資料夾>
// 手機 390 與桌機 1440 截圖；console 不能有錯；App 算的分數與 TOP 5 要跟後台一模一樣；每一題都打得開、產生得出企劃包；
// 「掃描」按鈕（沒有鑰匙、有鑰匙）流程走得完；離線（service worker）也打得開；更新紀錄只在改版後第一次打開時跳出來。
import fs from 'fs';
import http from 'http';
import path from 'path';
import { execSync } from 'child_process';

const [siteDir, shotDir] = process.argv.slice(2);
if (!siteDir || !shotDir) { console.log('用法：node tests/ui_check.mjs <網站資料夾> <截圖資料夾>'); process.exit(2); }
fs.mkdirSync(shotDir, { recursive: true });

async function loadPlaywright() {
  try { return await import('playwright'); } catch (e) { /* 找全域安裝的 */ }
  const root = execSync('npm root -g').toString().trim();
  return import(path.join(root, 'playwright', 'index.mjs'));
}
const { chromium } = await loadPlaywright();

// 小型靜態伺服器
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  const f = path.join(siteDir, p);
  if (!f.startsWith(path.resolve(siteDir)) || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream' });
  res.end(fs.readFileSync(f));
});
await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
const BASE = `http://127.0.0.1:${server.address().port}/`;
const radar = JSON.parse(fs.readFileSync(path.join(siteDir, 'data', 'radar.json'), 'utf8'));
// 最新一筆更新紀錄的代號：一般的檢查先當成「看過了」，才不會被自動跳出來的更新紀錄擋住
const lastUpdate = (fs.readFileSync(path.join(siteDir, 'updates.js'), 'utf8').match(/id:\s*'([^']+)'/) || [])[1] || '';

let fails = 0;
const ok = (cond, msg) => { if (!cond) { fails++; console.log('  ✗', msg); } };
const browser = await chromium.launch();

async function open(opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 390, height: 844 }, deviceScaleFactor: 2,
    colorScheme: opts.colorScheme || 'dark', serviceWorkers: opts.sw ? 'allow' : 'block', timezoneId: 'Asia/Taipei' });
  const page = await ctx.newPage();
  // App 會用「今天」重算截止日：把瀏覽器的時間定在資料產生後 10 分鐘，才能跟後台逐題比對
  await page.clock.setFixedTime(new Date(Date.parse(radar.generated) + 10 * 60e3));
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('console: ' + m.text()); });
  // 外部字型與 GitHub 在測試環境連不到，直接回空的
  await page.route(/fonts\.(googleapis|gstatic)\.com|raw\.githubusercontent\.com/, (r) => r.fulfill({ status: 404, body: '' }));
  if (!opts.fresh) await page.addInitScript((id) => { try { localStorage.setItem('echo.seenUpdate', id); } catch (e) { /* ignore */ } }, lastUpdate);
  if (opts.init) await page.addInitScript(opts.init);
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  return { ctx, page, errs };
}

// 1. 手機：每一頁截圖、分數與 TOP 5 跟後台一致
{
  const { ctx, page, errs } = await open();
  const top = await page.$$eval('#top .tbtn', (els) => els.map((e) => e.dataset.topic));
  ok(JSON.stringify(top) === JSON.stringify(radar.top), `App 的 TOP 5 跟後台一樣（App ${top}｜後台 ${radar.top}）`);
  await page.screenshot({ path: path.join(shotDir, 'phone-echo.png') });
  await page.click('[data-go="list"]');
  await page.waitForTimeout(200);
  const rows = await page.$$eval('#rows .rbtn', (els) => els.map((e) => [e.dataset.topic, Number(e.querySelector('.rs').textContent)]));
  const by = Object.fromEntries(radar.topics.map((t) => [t.id, t]));
  const bad = rows.filter(([id, s]) => !by[id] || Math.floor(by[id].score + 0.5) !== s);
  ok(rows.length === radar.topics.length && bad.length === 0, `題庫 ${rows.length} 題分數都跟後台一樣（不一致：${bad.slice(0, 3)}）`);
  await page.screenshot({ path: path.join(shotDir, 'phone-list.png') });
  for (const v of ['cal', 'plan', 'set']) {
    await page.click(`[data-go="${v}"]`);
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(shotDir, `phone-${v}.png`) });
  }
  // 每一題都打得開、產生得出企劃包
  await page.click('[data-go="list"]');
  let opened = 0;
  let briefs = 0;
  for (const [id] of rows) {
    await page.evaluate((x) => document.querySelector(`[data-topic="${x}"]`).click(), id);
    await page.waitForSelector('#sheet:not([hidden]) #sh-title');
    opened++;
    if (opened === 1) await page.screenshot({ path: path.join(shotDir, 'phone-topic.png') });
    await page.click('[data-brief]');
    const brief = await page.$eval('#brief', (e) => e.textContent);
    if (brief.includes('## 0. 這支影片') && brief.includes('給 Code 的第一句指令')) briefs++;
    await page.click('[data-back]');
    await page.click('[data-close]');
  }
  ok(opened === rows.length && briefs === rows.length, `每一題都打得開（${opened}）、產生得出企劃包（${briefs}）`);
  ok(errs.length === 0, `手機版沒有 console 錯誤：${errs.slice(0, 3)}`);
  await ctx.close();
}

// 2. 桌機 1440、淺色系統主題（App 只有深色主題，要確認不會變成半套）
{
  const { ctx, page, errs } = await open({ viewport: { width: 1440, height: 900 } });
  await page.screenshot({ path: path.join(shotDir, 'desktop-echo.png') });
  const twoCol = await page.$eval('#v-echo', (e) => getComputedStyle(e).display === 'grid');
  ok(twoCol, '桌機版雷達與 TOP 5 左右並排');
  await page.click('[data-go="list"]');
  await page.screenshot({ path: path.join(shotDir, 'desktop-list.png') });
  ok(errs.length === 0, `桌機版沒有 console 錯誤：${errs.slice(0, 3)}`);
  await ctx.close();
  const light = await open({ colorScheme: 'light' });
  const bg = await light.page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  ok(bg !== 'rgb(255, 255, 255)', `系統是淺色時 App 維持深色主題（${bg}）`);
  await light.page.screenshot({ path: path.join(shotDir, 'phone-light-system.png') });
  await light.ctx.close();
}

// 3. 掃描按鈕：沒有鑰匙→重新讀資料；有鑰匙→叫 GitHub 重跑（用假的 GitHub 回應）
{
  const { ctx, page, errs } = await open();
  await page.click('#scan');
  await page.waitForTimeout(800);
  const sheet = await page.$eval('#sheet', (e) => !e.hidden);
  ok(sheet, '沒有鑰匙時按掃描：說明已經是最新、怎麼設定遙控鑰匙');
  await ctx.close();
  const gen = radar.generated;
  const keyed = await open({ init: () => { localStorage.setItem('echo.key', 'github_pat_test'); } });
  let polls = 0;
  let served = 0;
  await keyed.page.route('https://api.github.com/**', async (r) => {
    const u = r.request().url();
    if (u.endsWith('/dispatches')) return r.fulfill({ status: 204, body: '' });
    if (u.includes('/actions/runs')) {
      polls++;
      return r.fulfill({ json: { workflow_runs: [{ created_at: new Date().toISOString(), status: polls > 1 ? 'completed' : 'in_progress', conclusion: polls > 1 ? 'success' : null }] } });
    }
    if (u.includes('/contents/prefs.json')) return r.fulfill({ status: 404, json: {} });
    return r.fulfill({ json: { permissions: { push: true } } });
  });
  await keyed.page.route(/data\/radar\.json/, async (r) => {
    served++;
    const resp = await r.fetch();
    const j = await resp.json();
    if (served > 1) j.generated = new Date(Date.parse(gen) + 3600e3).toISOString();
    return r.fulfill({ json: j });
  });
  await keyed.page.evaluate(() => { const o = window.setTimeout; window.setTimeout = (f, ms, ...a) => o(f, Math.min(ms, 50), ...a); });
  await keyed.page.click('#scan');
  await keyed.page.waitForFunction(() => /完成|掃描完成/.test(document.querySelector('#scanbox') ? document.querySelector('#scanbox').textContent : ''), null, { timeout: 30000 }).catch(() => {});
  const txt = await keyed.page.$eval('#scanbox', (e) => e.textContent).catch(() => '');
  ok(/完成/.test(txt), `有鑰匙時按掃描：叫 GitHub 重跑、等它跑完、讀到新的觀測（${txt.slice(-40)}）`);
  ok(keyed.errs.length === 0, `掃描流程沒有 console 錯誤：${keyed.errs.slice(0, 3)}`);
  await keyed.ctx.close();
}

// 4. 更新紀錄：改版後第一次打開跳出來一次，按「知道了」之後重新整理不會再跳；「方向」最下面可以再打開
{
  const { ctx, page, errs } = await open({ fresh: true });
  const first = await page.$eval('#sheet', (e) => !e.hidden && !!e.querySelector('.upd'));
  ok(first, '改版後第一次打開，更新紀錄自動跳出來');
  await page.screenshot({ path: path.join(shotDir, 'phone-updates.png') });
  await page.click('.upd-ok');
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  const again = await page.$eval('#sheet', (e) => !e.hidden);
  ok(!again, '看過之後重新整理，更新紀錄不會再跳出來');
  await page.click('[data-go="set"]');
  await page.click('[data-updates]');
  const reopen = await page.$eval('#sheet', (e) => !e.hidden && !!e.querySelector('.upd'));
  ok(reopen, '「方向」最下面的「更新紀錄」可以再打開');
  ok(errs.length === 0, `更新紀錄沒有 console 錯誤：${errs.slice(0, 3)}`);
  await ctx.close();
}

// 5. 離線：開過一次之後斷網也打得開
{
  const { ctx, page } = await open({ sw: true });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  await ctx.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForTimeout(800);
  const n = await page.$$eval('#top .tbtn', (els) => els.length).catch(() => 0);
  ok(n === 5, `離線也打得開，TOP 5 還在（${n}）`);
  await page.screenshot({ path: path.join(shotDir, 'phone-offline.png') });
  await ctx.close();
}

await browser.close();
server.close();
console.log(fails ? `  瀏覽器檢查失敗 ${fails} 項` : `  瀏覽器檢查全部通過（截圖在 ${shotDir}）`);
process.exit(fails ? 1 : 0);
