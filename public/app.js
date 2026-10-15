'use strict';

const state = { q: '', cost_type: '', category: '', editingId: null };
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
    const meta = [];
    if (r.source) meta.push('来源·' + r.source);
    if (r.created_at) meta.push(r.created_at);
    return `<div class="card" data-id="${r.id}">
      <div class="card-top">
        ${r.cost_type ? badge(r.cost_type, 'b-' + r.cost_type) : ''}
        ${(r.category === '事项' || r.category === '备忘') && !r.cost_type ? badge(r.category, 'b-thing') : ''}
        ${r.project ? badge(r.project, 'b-proj') : ''}
        ${r.feature ? badge(r.feature, 'b-feat') : ''}
        ${r.tax_mode && r.tax_mode !== '未标' ? badge(r.tax_mode, 'b-tax') : ''}
        ${amt}
      </div>
      <div class="card-content">${escapeHtml(r.content)}</div>
      ${r.note ? `<div class="card-note">备注：${escapeHtml(r.note)}</div>` : ''}
      <div class="card-meta">${meta.map(escapeHtml).join(' · ')}</div>
    </div>`;
  }).join('');
  list.querySelectorAll('.card').forEach(el => {
    el.onclick = () => openEdit(Number(el.dataset.id));
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
    parts.push(`<span class="pv-tag" style="color:${c};border-color:${c}">${rec.cost_type}</span>`);
  }
  if ((rec.category === '事项' || rec.category === '备忘') && !rec.cost_type) {
    parts.push(`<span class="pv-tag">${rec.category}</span>`);
  }
  if (rec.project) parts.push(`<span class="pv-tag">项目：${escapeHtml(rec.project)}</span>`);
  if (rec.amount != null) parts.push(`<span class="pv-amt">${rec.amount}${rec.unit || ''}</span>`);
  if (!parts.length) {
    el.innerHTML = '<span style="color:var(--sub)">写下内容，这里会实时显示我帮你认出的类型</span>';
    return;
  }
  el.innerHTML = '我帮你认成：' + parts.join('');
}

let _pvTimer;
function runPreview() {
  clearTimeout(_pvTimer);
  _pvTimer = setTimeout(async () => {
    const content = $('#content').value.trim();
    if (!content) { renderPreview(null); return; }
    const pick = getCostPick($('#costPick'));
    try {
      const rec = await api('POST', '/api/parse', { content, cost_type: pick, project: $('#projInput').value.trim() || null });
      renderPreview(rec);
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

// ---- 录入 / 编辑 弹层 ----
function openCreate() {
  state.editingId = null;
  $('#sheetTitle').textContent = '记一笔';
  $('#content').value = '';
  $('#projInput').value = '';
  $('#featInput').value = '';
  $('#noteInput').value = '';
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
  setCostPick($('#taxPick'), r.tax_mode || '未标');
  setCostPick($('#costPick'), r.cost_type || '自动');
  $('#delete').classList.remove('hidden');
  renderPreview(r);
  $('#sheet').classList.remove('hidden');
}

function closeSheet() { $('#sheet').classList.add('hidden'); }

async function saveRecord() {
  const content = $('#content').value.trim();
  if (!content) { toast('先写点内容'); return; }
  const project = $('#projInput').value.trim() || null;
  const cost_type = getCostPick($('#costPick'));
  const feature = $('#featInput').value.trim() || null;
  const note = $('#noteInput').value.trim() || null;
  const tax_mode = getCostPick($('#taxPick'));
  const payload = { content, project, feature, note, tax_mode, cost_type };
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
  $('#delete').addEventListener('click', deleteRecord);
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
