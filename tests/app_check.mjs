// App 端的規則檢查（不用瀏覽器）：node tests/app_check.mjs <deadline_cases.json>
// 1. App 的 deadlineTiming 跟後台 _deadline_timing 在每個案例都得到一樣的分數與說明；「為什麼是現在」的時效那句跟著重算
// 2. 方向設定合併（兩台裝置同步）照規則：新的設定覆蓋舊的、企劃逐筆合併、刪除會留紀錄
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, '..', 'site', 'app.js'), 'utf8');
const grab = (name) => {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`app.js 找不到 ${name}`);
  let d = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') d++;
    else if (src[k] === '}' && --d === 0) return src.slice(i, k + 1);
  }
  throw new Error(`${name} 沒有結尾`);
};
const lib = new Function([
  "const pad = (n) => String(n).padStart(2, '0');", "const WD = '日一二三四五六';",
  ...['dayOf', 'md', 'mdw', 'daysBetween', 'addDays', 'deadlineTiming', 'grade', 'mergePrefs', 'whyNow'].map(grab),
  'return { deadlineTiming, grade, mergePrefs, whyNow };'].join('\n'))();

let fails = 0;
const ok = (cond, msg) => { if (!cond) { fails++; console.log('  ✗', msg); } };

// 1. 時效分數一致
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let diff = 0;
for (const [today, od, sb, pub, cl, s, txt] of cases) {
  const [js, jt] = lib.deadlineTiming(today, od, sb, pub, cl);
  const norm = (x) => (x || '').replace(/（週.）/g, '');
  if (js !== s || norm(jt) !== norm(txt)) { if (diff++ < 3) console.log('  不一致', today, od, sb, pub, cl, '後台', s, txt, 'App', js, jt); }
}
ok(diff === 0, `時效分數 ${cases.length} 個案例完全一致（不一致 ${diff}）`);
ok(lib.grade(70) === '必拍' && lib.grade(58) === '可拍' && lib.grade(57) === '觀望' && lib.grade(45) === '先跳過', '等級門檻');

// 「為什麼是現在」的時效那句跟著 App 重算的結果換掉
const tw = { why: 'r/Games 當日熱門第 1；PTT 3 天 27 篇討論。今天就要開工，10/31（週六）前上架', reasons: { timing: ['今天就要開工，10/31（週六）前上架'] } };
ok(lib.whyNow(tw, '時間很趕：最晚 10/31（週六）要上架，只剩 1 天') === 'r/Games 當日熱門第 1；PTT 3 天 27 篇討論。時間很趕：最晚 10/31（週六）要上架，只剩 1 天', '換日後說明的時效跟著換');
ok(lib.whyNow(tw, tw.reasons.timing[0]) === tw.why && lib.whyNow(tw, undefined) === tw.why, '時效沒變就不動');
ok(lib.whyNow({ why: '本週報導最多的 6 件事', reasons: { timing: ['本週開工'] } }, '下週開工就好') === '本週報導最多的 6 件事', '說明裡沒有時效那句就不動');

// 2. 方向設定合併
const ago = (h) => new Date(Date.now() - h * 3600e3).toISOString();
const laptop = { updated: ago(5), weights: { demand: 40, timing: 25, fit: 20, ease: 15 }, plan: { a: { status: 'live', at: ago(50), views: 12000 } },
  videos: { v1: { views: 30000, status: 'done' } }, watch: ['GTA'], watchAt: { GTA: ago(40) } };
const phone = { updated: ago(1), weights: { demand: 60, timing: 20, fit: 10, ease: 10 }, plan: { b: { status: 'idea', at: ago(2) } } };
const m = lib.mergePrefs(phone, laptop);
ok(m.weights.demand === 60 && m.plan.a && m.plan.b && m.videos.v1 && m.watch.includes('GTA'), '新設定覆蓋權重，企劃、觀看數、關注逐筆保留');
const phoneDel = { ...m, updated: ago(0.5), plan: { b: m.plan.b }, del: { 'plan:a': ago(0.6) } };
ok(!lib.mergePrefs(phoneDel, laptop).plan.a, '一台刪掉的企劃，合併後不會被另一台救回來');
ok(!!lib.mergePrefs(phoneDel, { ...laptop, updated: ago(0.1), plan: { a: { status: 'idea', at: ago(0.2) } } }).plan.a, '刪掉之後又重新加回的會保留');
ok(lib.mergePrefs(phone, null) === phone, '沒有遠端設定時用本機的');

console.log(fails ? `  App 規則檢查失敗 ${fails} 項` : '  App 規則檢查全部通過');
process.exit(fails ? 1 : 0);
