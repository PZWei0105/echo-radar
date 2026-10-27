// 回波雷達：每個題目是一團回波。離中心越近越急（中心是今天），顏色越熱分數越高，扇區是頻道路線。
// 回波用低解析度格點算強度再放大，跟氣象署雷達回波圖一樣一格一格的。

export const BANDS = [
  { max: 0, inner: 0.0, outer: 0.2, label: '今天' },
  { max: 3, inner: 0.2, outer: 0.42, label: '3 天' },
  { max: 7, inner: 0.42, outer: 0.64, label: '7 天' },
  { max: 14, inner: 0.64, outer: 0.84, label: '14 天' },
  { max: Infinity, inner: 0.84, outer: 0.985, label: '不急' },
];
export const PILLARS = ['season', 'issue', 'ranking', 'hardware', 'spotlight'];
const SCALE = [
  [14, null], [22, '#0a6f9e'], [30, '#00b4e0'], [38, '#00dfa0'], [46, '#7fe83a'], [54, '#f2e30c'],
  [62, '#ff9b1e'], [70, '#ff3a2e'], [80, '#e52bd6'], [101, '#a35cff'],
];
const RGB = {};
for (const [, c] of SCALE) if (c) RGB[c] = [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];

export function echoColor(v) {
  let prev = '#0a6f9e';
  for (const [lim, c] of SCALE) {
    if (v < lim) return c || prev;
    if (c) prev = c;
  }
  return '#a35cff';
}

// 台灣本島的簡化輪廓（經緯度），雷達站就在台灣
const TAIWAN = [[121.54, 25.3], [121.74, 25.14], [121.93, 25.03], [121.83, 24.86], [121.86, 24.6], [121.73, 24.36],
  [121.62, 23.98], [121.5, 23.5], [121.35, 23.12], [121.15, 22.76], [120.95, 22.4], [120.87, 21.92], [120.74, 21.96],
  [120.7, 22.15], [120.6, 22.36], [120.4, 22.5], [120.27, 22.63], [120.17, 22.9], [120.09, 23.1], [120.12, 23.45],
  [120.24, 23.8], [120.37, 24.06], [120.52, 24.28], [120.68, 24.52], [120.9, 24.8], [121.06, 25.03], [121.28, 25.12],
  [121.42, 25.21]];

export class Scope {
  constructor(canvas, { onPick, onHover } = {}) {
    this.c = canvas;
    this.ctx = canvas.getContext('2d');
    this.onPick = onPick;
    this.onHover = onHover;
    this.blobs = [];
    this.labels = {};
    this.size = canvas.width;
    this.grid = 144;
    this.low = document.createElement('canvas');
    this.low.width = this.low.height = this.grid;
    canvas.addEventListener('click', (e) => this._click(e));
    canvas.addEventListener('pointermove', (e) => this._move(e));
    canvas.addEventListener('pointerleave', () => this.onHover && this.onHover(null));
  }

  setLabels(labels) {
    this.labels = labels || {};
  }

  // items: [{id, score, pillar, days, rank (1–5 或 null), title}]
  draw(items, sweepAngle = null) {
    const { ctx, size } = this;
    const cx = size / 2;
    const cy = size / 2;
    const R = size * 0.47;
    ctx.clearRect(0, 0, size, size);

    // 海面
    const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.05);
    g.addColorStop(0, '#0d2131');
    g.addColorStop(1, '#06111b');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fill();

    // 台灣
    this._taiwan(cx, cy, R * 0.22);

    // 扇區分隔
    ctx.save();
    ctx.strokeStyle = '#173043';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 7]);
    PILLARS.forEach((p, i) => {
      const a = this._sector(i).start;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * R * 0.2, cy + Math.sin(a) * R * 0.2);
      ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
      ctx.stroke();
    });
    ctx.restore();

    // 距離環
    ctx.save();
    ctx.strokeStyle = '#1d3a4f';
    ctx.lineWidth = 2;
    BANDS.slice(0, 4).forEach((b) => {
      ctx.beginPath();
      ctx.arc(cx, cy, R * b.outer, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.strokeStyle = '#2b4a5f';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    // 回波
    this.blobs = this._layout(items, cx, cy, R);
    this._echo(this.blobs);
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.clip();
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = 0.92;
    ctx.drawImage(this.low, 0, 0, size, size);
    ctx.restore();

    // 文字：距離環與路線。先量好每個標籤的位置（名次章會避開），等指引線畫完再畫字，線才不會劃過字
    const ZH = '"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif';
    const texts = [];
    ctx.save();
    // 距離標籤放在「檔期」和「爭議」兩個扇區的分界線上，不會壓到回波
    ctx.font = `600 21px ${ZH}`;
    const la = this._sector(1).start;
    BANDS.slice(0, 4).forEach((b) => {
      const x = cx + Math.cos(la) * R * b.outer - 14;
      const y = cy + Math.sin(la) * R * b.outer + 4;
      const w = ctx.measureText(b.label).width;
      texts.push({ t: b.label, x, y, font: `600 21px ${ZH}`, align: 'left', bg: 'rgba(6,17,27,.75)', fg: '#7f97a8', box: [x - 3, y - 12, w + 6, 24] });
    });
    ctx.font = `700 24px ${ZH}`;
    PILLARS.forEach((p, i) => {
      const s = this._sector(i);
      const a = (s.start + s.end) / 2;
      const x = cx + Math.cos(a) * R * 0.9;
      const y = cy + Math.sin(a) * R * 0.9;
      const t = this.labels[p] || p;
      const w = ctx.measureText(t).width;
      texts.push({ t, x, y, font: `700 24px ${ZH}`, align: 'center', bg: 'rgba(6,17,27,.7)', fg: '#c9d8e3', box: [x - w / 2 - 6, y - 15, w + 12, 30] });
    });
    ctx.restore();

    // TOP 5 名次：好幾題都是「今天」時會擠在中心互相蓋住，名次章照名次順序擺，撞到別的名次章或文字標籤就往外或往旁邊挪，
    // 再拉一條細線指回回波（真的擺不開時，至少不蓋到別的名次章）
    ctx.save();
    const placed = [];
    const offText = (nx, ny) => texts.every(({ box: [x, y, w, h] }) => Math.hypot(nx - Math.max(x, Math.min(nx, x + w)), ny - Math.max(y, Math.min(ny, y + h))) >= 19);
    const fits = (nx, ny, avoidText) => Math.hypot(nx - cx, ny - cy) <= R - 22 && placed.every((q) => Math.hypot(q.x - nx, q.y - ny) >= 38)
      && (!avoidText || offText(nx, ny));
    for (const b of this.blobs.filter((x) => x.rank).sort((a, c) => a.rank - c.rank)) {
      const ang = Math.atan2(b.y - cy, b.x - cx);
      const tries = [[0, 0]];
      for (const d of [40, 76, 112]) {
        tries.push([Math.cos(ang) * d, Math.sin(ang) * d]);
        for (const side of [1, -1]) tries.push([Math.cos(ang + side * Math.PI / 2) * d, Math.sin(ang + side * Math.PI / 2) * d]);
      }
      const [dx, dy] = tries.find(([x, y]) => fits(b.x + x, b.y + y, true)) || tries.find(([x, y]) => fits(b.x + x, b.y + y, false)) || [0, 0];
      b.bx = b.x + dx;
      b.by = b.y + dy;
      placed.push({ x: b.bx, y: b.by });
      if (dx || dy) {
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.bx, b.by);
        ctx.lineWidth = 2;
        ctx.strokeStyle = 'rgba(232,240,246,.7)';
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(b.x, b.y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = '#e8f0f6';
        ctx.fill();
      }
    }
    ctx.textBaseline = 'middle';
    for (const { t, x, y, font, align, bg, fg, box } of texts) {
      ctx.fillStyle = bg;
      ctx.fillRect(...box);
      ctx.font = font;
      ctx.textAlign = align;
      ctx.fillStyle = fg;
      ctx.fillText(t, x, y);
    }
    ctx.textAlign = 'center';
    ctx.font = '800 22px "Archivo","Helvetica Neue",Arial,sans-serif';
    for (const b of this.blobs) {
      if (!b.rank) continue;
      ctx.beginPath();
      ctx.arc(b.bx, b.by, 17, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(6,17,27,.86)';
      ctx.fill();
      ctx.lineWidth = 3;
      ctx.strokeStyle = '#e8f0f6';
      ctx.stroke();
      ctx.fillStyle = '#e8f0f6';
      ctx.fillText(String(b.rank), b.bx, b.by + 1);
    }
    ctx.restore();

    // 中心點
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#ff3a2e';
    ctx.fill();
  }

  _sector(i) {
    const w = (Math.PI * 2) / PILLARS.length;
    const start = -Math.PI / 2 - w / 2 + i * w;
    return { start, end: start + w };
  }

  _layout(items, cx, cy, R) {
    const groups = {};
    for (const it of items) {
      const band = BANDS.findIndex((b) => it.days <= b.max);
      const si = Math.max(0, PILLARS.indexOf(it.pillar));
      const k = `${si}:${band}`;
      (groups[k] = groups[k] || []).push({ ...it, band, si });
    }
    const out = [];
    for (const list of Object.values(groups)) {
      list.sort((a, b) => b.score - a.score);
      const { start, end } = this._sector(list[0].si);
      const pad = 0.1;
      const n = list.length;
      list.forEach((it, i) => {
        const b = BANDS[it.band];
        const t = n === 1 ? 0.5 : (i + 0.5) / n;
        // 同一格的題目左右錯開，避免疊在一起
        const a = start + pad + (end - start - 2 * pad) * t;
        const depth = 0.3 + 0.45 * (1 - Math.min(1, it.score / 100)) + (i % 2 ? 0.12 : 0);
        const rr = R * (b.inner + (b.outer - b.inner) * Math.min(0.92, depth));
        const size = 2.4 + 4.4 * Math.pow(Math.max(0, it.score) / 100, 1.5);
        out.push({ ...it, x: cx + Math.cos(a) * rr, y: cy + Math.sin(a) * rr, r: size });
      });
    }
    return out;
  }

  _echo(blobs) {
    const n = this.grid;
    const cell = this.size / n;
    const lctx = this.low.getContext('2d');
    const img = lctx.createImageData(n, n);
    const pts = blobs.map((b) => ({ gx: b.x / cell, gy: b.y / cell, r: b.r, s: b.score }));
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        let v = 0;
        for (const p of pts) {
          const dx = x + 0.5 - p.gx;
          const dy = y + 0.5 - p.gy;
          const d2 = (dx * dx + dy * dy) / (p.r * p.r);
          if (d2 > 6) continue;
          const val = p.s * Math.exp(-d2 * 0.55);
          if (val > v) v = val;
        }
        const o = (y * n + x) * 4;
        if (v < 14) {
          img.data[o + 3] = 0;
          continue;
        }
        const c = echoColor(v);
        const [r, g, bl] = RGB[c] || [255, 255, 255];
        img.data[o] = r;
        img.data[o + 1] = g;
        img.data[o + 2] = bl;
        img.data[o + 3] = v < 22 ? 150 : 235;
      }
    }
    lctx.putImageData(img, 0, 0);
  }

  _taiwan(cx, cy, h) {
    const { ctx } = this;
    const k = h / 3.4;
    ctx.save();
    ctx.beginPath();
    TAIWAN.forEach(([lon, lat], i) => {
      const x = cx + (lon - 121.0) * k * 0.92;
      const y = cy - (lat - 23.6) * k;
      if (i) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = '#132b3b';
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#3d6680';
    ctx.stroke();
    ctx.restore();
  }

  _pos(e) {
    const rect = this.c.getBoundingClientRect();
    const k = this.size / rect.width;
    return { x: (e.clientX - rect.left) * k, y: (e.clientY - rect.top) * k, k, rect };
  }

  _hit(x, y) {
    let best = null;
    let bd = Infinity;
    const cell = this.size / this.grid;
    for (const b of this.blobs) {
      // 名次章挪開了也點得到
      const d = Math.min(Math.hypot(b.x - x, b.y - y), b.rank && b.bx != null ? Math.hypot(b.bx - x, b.by - y) : Infinity);
      const lim = Math.max(26, b.r * cell * 1.3);
      if (d < lim && d < bd) {
        bd = d;
        best = b;
      }
    }
    return best;
  }

  _click(e) {
    const p = this._pos(e);
    const b = this._hit(p.x, p.y);
    if (b && this.onPick) this.onPick(b.id);
  }

  _move(e) {
    if (e.pointerType !== 'mouse' || !this.onHover) return;
    const p = this._pos(e);
    const b = this._hit(p.x, p.y);
    this.c.style.cursor = b ? 'pointer' : 'default';
    this.onHover(b ? { ...b, sx: b.x / p.k, sy: b.y / p.k } : null);
  }
}
