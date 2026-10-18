'use strict';

const state = { q: '', cost_type: '', category: '', editingId: null, photo: undefined };
const $ = (s) => document.querySelector(s);

async function api(method, path, body) {
  const opt = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (r.status === 401) { showLogin(); throw new Error('未登录'); }
  if (!r.ok) throw new Error('请求失败 ' + r.status);
  return r.json();
}

function showLogin() { $('#login').classList.remove('hidden'); $('#loginErr').textContent = ''; }
function hideLogin() { $('#login').classList.add('hidden'); }

async function doLogin() {
  const user = $('#lu').value.trim();
  const pass = $('#lp').value;
  if (!user || !pass) { $('#loginErr').textContent = '请输入用户名和密码'; return; }
  try {
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify({ user, pass }) });
    if (!r.ok) { $('#loginErr').textContent = '用户名或密码错误'; return; }
    $('#lp').value = '';
    hideLogin();
    await refreshAll();
  } catch (e) { $('#loginErr').textContent = '登录失败，稍后重试'; }
}

async function doLogout() {
  await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
  showLogin();
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), 1600);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---- 列表渲染 ----
function badge(text, cls) {
  if (!text) return '';
  return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function renderList(rows) {
  const list = $('#list');
  if (!rows.length) {
    const hint = (state.q || state.cost_type || state.category)
      ? `没找到${state.q ? `含「${escapeHtml(state.q)}」` : ''}的记录，换个说法或清掉筛选试试。`
      : `还没有记录。点右下角的 <b>＋</b> 记第一笔吧。`;
    list.innerHTML = `<div class="empty">${hint}</div>`;
    return;
  }
  list.innerHTML = rows.map(r => {
    const amt = (r.amount != null)
      ? `<span class="amount">${r.amount}<small> ${r.unit || ''}</small></span>` : '';
    const tot = (r.total != null)
      ? `<span class="amount amount-total">合价 ${r.total}</span>` : '';
    // 一条记录里识别出的多个价格项
    let itemsHtml = '';
    if (Array.isArray(r.items) && r.items.length) {
      itemsHtml = '<div class="card-items">' + r.items.map(it => {
        const nm = (it.name || '').replace(/</g, '&lt;');
        const u = it.unit || '';
        return `<div class="ci"><span class="ci-n">${escapeHtml(nm)}</span>`
          + `<span class="ci-p">${it.amount}${escapeHtml(u)}</span></div>`;
      }).join('') + '</div>';
    }
    const meta = [];
    if (r.source) meta.push('来源·' + r.source);
    if (r.created_at) meta.push(r.created_at);
    const ctBadge = r.cost_type === '事情'
      ? badge(r.cost_type, 'b-thing')
      : (r.cost_type ? badge(r.cost_type, 'b-' + r.cost_type) : '');
    return `<div class="card" data-id="${r.id}">
      <div class="card-top">
        ${ctBadge}
        ${!r.cost_type && (r.category === '事项' || r.category === '备忘') ? badge(r.category, 'b-thing') : ''}
        ${r.project ? badge(r.project, 'b-proj') : ''}
        ${r.feature ? badge(r.feature, 'b-feat') : ''}
        ${r.tax_mode && r.tax_mode !== '未标' ? badge(r.tax_mode, 'b-tax') : ''}
        ${r.price_src ? badge(r.price_src, 'b-src') : ''}
        ${amt}
        ${tot}
      </div>
      <div class="card-content">${escapeHtml(r.content)}</div>
      ${r.has_photo ? `<img class="thumb" src="/api/photo/${r.id}" alt="照片" loading="lazy">` : ''}
      ${itemsHtml}
      ${r.note ? `<div class="card-note">备注：${escapeHtml(r.note)}</div>` : ''}
      <div class="card-meta">${meta.map(escapeHtml).join(' · ')}</div>
    </div>`;
  }).join('');
  list.querySelectorAll('.card').forEach(el => {
    el.onclick = (e) => {
      // 点图片放大，不进编辑
      if (e.target.classList && e.target.classList.contains('thumb')) {
        openLightbox(e.target.src);
        return;
      }
      openEdit(Number(el.dataset.id));
    };
  });
}

async function loadRecords() {
  const p = new URLSearchParams();
  if (state.q) p.set('q', state.q);
  if (state.cost_type) p.set('cost_type', state.cost_type);
  if (state.category) p.set('category', state.category);
  const rows = await api('GET', '/api/records?' + p.toString());
  renderList(rows);
}

async function loadStats() {
  try {
    const s = await api('GET', '/api/stats');
    $('#count').textContent = '共 ' + s.total + ' 条';
  } catch (e) { /* ignore */ }
}

async function refreshAll() { await Promise.all([loadRecords(), loadStats()]); }

// ---- 智能识别预览 ----
const CTYPE_COLOR = { '人工': 'var(--人工)', '材料': 'var(--材料)', '机械': 'var(--机械)' };
function renderPreview(rec) {
  const el = $('#preview');
  if (!rec) { el.innerHTML = ''; return; }
  const parts = [];
  if (rec.cost_type) {
    const c = CTYPE_COLOR[rec.cost_type] || 'var(--teal)';
    const isThing = rec.cost_type === '事情';
    parts.push(`<span class="pv-tag" style="${isThing ? 'color:#5b21b6;border-color:#c4b5fd' : 'color:' + c + ';border-color:' + c}">${rec.cost_type}</span>`);
  }
  if ((rec.category === '事项' || rec.category === '备忘') && !rec.cost_type) {
    parts.push(`<span class="pv-tag">${rec.category}</span>`);
  }
  if (rec.project) parts.push(`<span class="pv-tag">项目：${escapeHtml(rec.project)}</span>`);
  if (rec.amount != null) parts.push(`<span class="pv-amt">单价 ${rec.amount}${rec.unit || ''}</span>`);
  if (rec.quantity != null) parts.push(`<span class="pv-amt">工程量 ${rec.quantity}</span>`);
  if (rec.total != null) parts.push(`<span class="pv-amt">合价 ${rec.total}</span>`);
  if (!parts.length) {
    el.innerHTML = '<span style="color:var(--sub)">写下内容，这里会实时显示我帮你认出的类型</span>';
    return;
  }
  let html = '我帮你认成：' + parts.join('');
  if (Array.isArray(rec.items) && rec.items.length > 0) {
    html += '<div class="pv-items">' + rec.items.map(it =>
      `<div class="pvi">${escapeHtml(it.name || '')} ${it.amount}${escapeHtml(it.unit || '')}</div>`
    ).join('') + '</div>';
  }
  el.innerHTML = html;
}

let _pvTimer;
function runPreview() {
  clearTimeout(_pvTimer);
  _pvTimer = setTimeout(async () => {
    const content = $('#content').value.trim();
    if (!content) { renderPreview(null); return; }
    const pick = getCostPick($('#costPick'));
    const q = getNum('qtyInput'), a = getNum('amtInput'), t = getNum('totInput');
    try {
      const rec = await api('POST', '/api/parse', {
        content, cost_type: pick, project: $('#projInput').value.trim() || null,
        quantity: q, total: t
      });
      renderPreview(rec);
      loadHistory(content);
    } catch (e) { /* ignore */ }
  }, 250);
}

// ---- 成本类型选择 ----
function getCostPick(container) {
  const active = container.querySelector('.cp.active');
  const v = active ? active.dataset.v : '自动';
  return v === '自动' ? null : v;
}
function setCostPick(container, val) {
  container.querySelectorAll('.cp').forEach(b => {
    b.classList.toggle('active', b.dataset.v === (val || '自动'));
  });
}
function bindCostPick(container) {
  container.querySelectorAll('.cp').forEach(b => {
    b.onclick = () => { setCostPick(container, b.dataset.v); runPreview(); };
  });
}

// ---- 报价单字段：工程量 × 单价 = 合价（自动算，可手填）----
function getNum(id) {
  const v = $('#' + id).value.trim();
  if (v === '') return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}
function calcTotal() {
  const q = getNum('qtyInput');
  const a = getNum('amtInput');
  const tEl = $('#totInput');
  const hint = $('#quoteHint');
  // 用户没手动填合价时，自动算
  if (tEl.value.trim() === '' && q != null && a != null) {
    const t = Math.round(q * a * 100) / 100;
    tEl.placeholder = '自动 ' + t;
    hint.textContent = `合价自动算：${q} × ${a} = ${t}`;
  } else if (q != null && a != null && tEl.value.trim() !== '') {
    hint.textContent = `合价 = 工程量 × 单价，可手填覆盖`;
  } else {
    tEl.placeholder = '自动算';
    hint.textContent = '';
  }
}

// ---- 录入 / 编辑 弹层 ----
function openCreate() {
  state.editingId = null;
  $('#sheetTitle').textContent = '记一笔';
  $('#content').value = '';
  $('#projInput').value = '';
  $('#featInput').value = '';
  $('#noteInput').value = '';
  $('#srcInput').value = '';
  $('#qtyInput').value = '';
  $('#amtInput').value = '';
  $('#totInput').value = '';
  $('#totInput').placeholder = '自动算';
  $('#quoteHint').textContent = '';
  state.photo = undefined;
  renderPhoto(null);
  hideHistory();
  setCostPick($('#costPick'), '自动');
  setCostPick($('#taxPick'), '未标');
  $('#delete').classList.add('hidden');
  renderPreview(null);
  $('#sheet').classList.remove('hidden');
  setTimeout(() => $('#content').focus(), 50);
}

async function openEdit(id) {
  const rows = await api('GET', '/api/records?q=');
  const r = rows.find(x => x.id === id);
  if (!r) return;
  state.editingId = id;
  $('#sheetTitle').textContent = '查看 / 修改';
  $('#content').value = r.content;
  $('#projInput').value = r.project || '';
  $('#featInput').value = r.feature || '';
  $('#noteInput').value = r.note || '';
  $('#srcInput').value = r.price_src || '';
  $('#qtyInput').value = (r.quantity != null) ? r.quantity : '';
  $('#amtInput').value = (r.amount != null) ? r.amount : '';
  $('#totInput').value = (r.total != null) ? r.total : '';
  $('#totInput').placeholder = '自动算';
  $('#quoteHint').textContent = '';
  state.photo = undefined;
  renderPhoto(r.has_photo ? ('/api/photo/' + id) : null);
  setCostPick($('#taxPick'), r.tax_mode || '未标');
  // 编辑时默认「自动」，内容改动后随新内容重新识别；用户仍可手动点选覆盖
  setCostPick($('#costPick'), '自动');
  $('#delete').classList.remove('hidden');
  renderPreview(r);
  loadHistory(r.content);
  $('#sheet').classList.remove('hidden');
}

function closeSheet() { $('#sheet').classList.add('hidden'); }

async function saveRecord() {
  const content = $('#content').value.trim();
  // 内容可空，但至少要有照片（state.photo 为 null/undefined/'' 时视为没图）
  const hasPhoto = state.photo && state.photo !== 'delete';
  if (!content && !hasPhoto) { toast('写点内容，或先拍一张图'); return; }
  const project = $('#projInput').value.trim() || null;
  const cost_type = getCostPick($('#costPick'));
  const feature = $('#featInput').value.trim() || null;
  const note = $('#noteInput').value.trim() || null;
  const price_src = $('#srcInput').value.trim() || null;
  const tax_mode = getCostPick($('#taxPick'));
  const payload = { content, project, feature, note, price_src, tax_mode, cost_type };
  // 报价单字段
  const q = getNum('qtyInput'), a = getNum('amtInput'), t = getNum('totInput');
  if (q != null) payload.quantity = q;
  if (a != null) payload.amount = a;
  if (t != null) payload.total = t;
  if (state.photo !== undefined) payload.photo = state.photo;
  try {
    if (state.editingId) {
      await api('PUT', '/api/records/' + state.editingId, payload);
      toast('已更新');
    } else {
      await api('POST', '/api/records', payload);
      toast('已记住');
    }
    closeSheet();
    await refreshAll();
  } catch (e) { toast('保存失败'); }
}

async function deleteRecord() {
  if (!confirm('确定删除这条记录？')) return;
  try {
    await api('DELETE', '/api/records/' + state.editingId);
    closeSheet();
    toast('已删除');
    await refreshAll();
  } catch (e) { toast('删除失败'); }
}

// ---- 批量导入 ----
async function doImport(file) {
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const records = Array.isArray(data) ? data : (data.records || []);
    if (!records.length) { toast('文件里没有可导入的记录'); return; }
    if (!confirm('确定导入 ' + records.length + ' 条记录？')) return;
    const r = await api('POST', '/api/import', { records });
    toast('已导入 ' + r.imported + ' 条');
    closeSheet();
    await refreshAll();
  } catch (e) { toast('导入失败：文件格式不对'); }
}

// ---- 照片 / 历史价格 / 成本汇总（P1 P2 P5）----
function renderPhoto(src) {
  const box = $('#photoPreview');
  const img = $('#photoImg');
  if (src) { img.src = src; box.classList.remove('hidden'); }
  else { img.removeAttribute('src'); box.classList.add('hidden'); }
}

function compressImage(file, maxW, quality, cb) {
  const reader = new FileReader();
  reader.onload = (e) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxW / img.width);
      const w = Math.max(1, Math.round(img.width * scale));
      const h = Math.max(1, Math.round(img.height * scale));
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d').drawImage(img, 0, 0, w, h);
      cb(c.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => cb(null);
    img.src = e.target.result;
  };
  reader.onerror = () => cb(null);
  reader.readAsDataURL(file);
}

function nameKey(content) {
  const s = (content || '').split(/[，,]/)[0].trim();
  return s.length >= 2 ? s : (content || '').slice(0, 8);
}

async function loadHistory(content) {
  const box = $('#historyBox');
  const key = nameKey(content);
  if (!key) { box.classList.add('hidden'); return; }
  try {
    const rows = await api('GET', '/api/history?q=' + encodeURIComponent(key));
    const list = state.editingId ? rows.filter(x => x.id !== state.editingId) : rows;
    if (!list.length) { box.classList.add('hidden'); return; }
    $('#historyList').innerHTML = list.slice(-8).map(x => {
      const price = (x.amount != null) ? (x.amount + (x.unit || '')) : '';
      return `<div class="history-item"><span class="h-date">${escapeHtml((x.created_at || '').slice(0, 10))}</span>`
        + `<span class="h-type">${escapeHtml(x.cost_type || '')}</span>`
        + `<span class="h-price">${escapeHtml(String(price))}</span></div>`;
    }).join('');
    box.classList.remove('hidden');
  } catch (e) { box.classList.add('hidden'); }
}

function hideHistory() { $('#historyBox').classList.add('hidden'); }

// ---- 图片放大灯箱 ----
function openLightbox(src) {
  $('#lightboxImg').src = src;
  $('#lightbox').classList.remove('hidden');
}
function closeLightbox() { $('#lightbox').classList.add('hidden'); $('#lightboxImg').removeAttribute('src'); }

async function doSummary() {
  $('#summaryModal').classList.remove('hidden');
  $('#summaryBody').innerHTML = '<div class="empty">统计中…</div>';
  try {
    const data = await api('GET', '/api/summary');
    const projs = Object.keys(data);
    if (!projs.length) { $('#summaryBody').innerHTML = '<div class="empty">还没有带金额的记录。</div>'; return; }
    let html = '';
    for (const p of projs) {
      const byType = data[p];
      let sum = 0, cnt = 0;
      const rowsHtml = Object.keys(byType).map(t => {
        const d = byType[t];
        sum += d.sum_total; cnt += d.count;
        return `<tr><td>${escapeHtml(t)}</td><td>${d.count} 条</td>`
          + `<td class="num">${d.total || 0}</td>`
          + `<td class="num">${d.sum_total || 0}</td></tr>`;
      }).join('');
      html += `<div class="sum-block"><div class="sum-proj">${escapeHtml(p)}</div>`
        + `<table class="sum-table"><tr><th>类别</th><th>条数</th><th class="num">单价合计</th><th class="num">合价合计</th></tr>`
        + rowsHtml + `<tr class="sum-total"><td>合计</td><td>${cnt} 条</td><td class="num">—</td><td class="num">${Math.round(sum * 100) / 100}</td></tr></table></div>`;
    }
    $('#summaryBody').innerHTML = html;
  } catch (e) {
    $('#summaryBody').innerHTML = '<div class="empty">统计失败，稍后重试。</div>';
  }
}

// ---- 初始化 ----
function init() {
  bindCostPick($('#costPick'));
  bindCostPick($('#taxPick'));

  document.querySelectorAll('#filters .f').forEach(b => {
    b.onclick = () => {
      const cat = b.dataset.cat;
      if (b.dataset.v === '' && !cat) {          // 全部
        state.cost_type = ''; state.category = '';
      } else if (cat) {                           // 事情
        state.category = (state.category === cat) ? '' : cat;
        state.cost_type = '';
      } else {                                    // 人材机
        state.cost_type = (state.cost_type === b.dataset.v) ? '' : b.dataset.v;
        state.category = '';
      }
      document.querySelectorAll('#filters .f').forEach(x => {
        const xc = x.dataset.cat;
        let on = false;
        if (x.dataset.v === '' && !xc) on = (!state.cost_type && !state.category);
        else if (xc) on = (xc === state.category && !!state.category);
        else on = (x.dataset.v === state.cost_type && !!state.cost_type);
        x.classList.toggle('active', on);
      });
      loadRecords();
    };
  });

  let dt;
  $('#q').addEventListener('input', (e) => {
    clearTimeout(dt);
    dt = setTimeout(() => { state.q = e.target.value.trim(); loadRecords(); }, 200);
  });
  $('#content').addEventListener('input', runPreview);
  $('#projInput').addEventListener('input', runPreview);

  $('#fab').addEventListener('click', openCreate);
  $('#sheetClose').addEventListener('click', closeSheet);
  $('#sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') closeSheet(); });
  $('#save').addEventListener('click', saveRecord);
  ['qtyInput', 'amtInput', 'totInput'].forEach(id =>
    $('#' + id).addEventListener('input', calcTotal));
  $('#delete').addEventListener('click', deleteRecord);
  $('#summaryBtn').addEventListener('click', doSummary);
  $('#summaryClose').addEventListener('click', () => $('#summaryModal').classList.add('hidden'));
  $('#summaryModal').addEventListener('click', (e) => { if (e.target.id === 'summaryModal') $('#summaryModal').classList.add('hidden'); });
  $('#photoBtn').addEventListener('click', () => $('#photoFile').click());
  $('#photoFile').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) compressImage(f, 1000, 0.7, (d) => { if (d) { state.photo = d; renderPhoto(d); } else { toast('图片读取失败'); } });
    e.target.value = '';
  });
  $('#photoDel').addEventListener('click', () => { state.photo = null; renderPhoto(null); });
  $('#lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });
  $('#lightboxClose').addEventListener('click', closeLightbox);
  $('#importBtn').addEventListener('click', () => $('#importFile').click());
  $('#importFile').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) doImport(f);
    e.target.value = '';
  });
  $('#logout').addEventListener('click', doLogout);
  $('#loginBtn').addEventListener('click', doLogin);
  $('#lp').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});

  fetch('/api/health', { credentials: 'same-origin' })
    .then(r => { if (r.ok) { hideLogin(); refreshAll(); } else { showLogin(); } })
    .catch(() => showLogin());
}

init();
