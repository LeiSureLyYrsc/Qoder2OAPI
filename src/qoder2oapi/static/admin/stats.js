/**
 * Qoder2OAPI Request Analytics & Metrics Client Script
 */

const STORAGE_KEY = 'qoder2oapi_api_key';

let state = {
  apiKey: localStorage.getItem(STORAGE_KEY) || '',
  overview: null,
  models: [],
  trends: [],
  pricing: [],
};

// --- Notifications / Toast ---
function showToast(message, type = 'info', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// --- API Client ---
async function api(path, opts = {}) {
  const key = localStorage.getItem(STORAGE_KEY) || '';
  const headers = {
    'Authorization': 'Bearer ' + key,
    'Content-Type': 'application/json',
    ...(opts.headers || {}),
  };

  const resp = await fetch(path, {
    ...opts,
    headers,
  });

  if (resp.status === 401) {
    throw new Error('UNAUTHORIZED');
  }

  return resp;
}

// Format numbers
function formatNumber(num) {
  if (num === null || num === undefined || isNaN(num)) return '0';
  return Number(num).toLocaleString('zh-CN');
}

function formatCurrency(num) {
  if (num === null || num === undefined || isNaN(num)) return '¥0.00';
  return '¥' + Number(num).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// --- Authentication & Lock Screen ---
function checkAuth() {
  const lockScreen = document.getElementById('lock-screen');
  const mainDash = document.getElementById('main-dashboard');

  if (!state.apiKey) {
    lockScreen.classList.remove('hidden');
    mainDash.classList.add('hidden');
    return false;
  } else {
    lockScreen.classList.add('hidden');
    mainDash.classList.remove('hidden');
    return true;
  }
}

function initLockScreen() {
  const saveBtn = document.getElementById('lock-save-btn');
  const input = document.getElementById('lock-key-input');

  const onSave = () => {
    const val = (input.value || '').trim();
    if (!val) {
      showToast('请输入本代理的 API 密钥', 'error');
      return;
    }
    state.apiKey = val;
    localStorage.setItem(STORAGE_KEY, val);
    if (checkAuth()) {
      loadAllMetrics();
    }
  };

  saveBtn.addEventListener('click', onSave);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') onSave();
  });
}

// --- Load Metrics ---
async function loadAllMetrics() {
  try {
    const [ovRes, mdRes, trRes, prRes] = await Promise.all([
      api('/api/admin/metrics/overview'),
      api('/api/admin/metrics/models'),
      api('/api/admin/metrics/trends?hours=24'),
      api('/api/admin/metrics/pricing'),
    ]);

    if (ovRes.ok) state.overview = await ovRes.json();
    if (mdRes.ok) state.models = await mdRes.json();
    if (trRes.ok) state.trends = await trRes.json();
    if (prRes.ok) state.pricing = await prRes.json();

    renderOverview();
    renderPricingTable();
    renderTrendsTable();
    renderModelsTable();
  } catch (err) {
    if (err.message === 'UNAUTHORIZED') {
      showToast('鉴权失败，请检查 API Key', 'error');
      state.apiKey = '';
      localStorage.removeItem(STORAGE_KEY);
      checkAuth();
    } else {
      showToast('加载统计数据失败: ' + err.message, 'error');
    }
  }
}

// --- Render Overview ---
function renderOverview() {
  const ov = state.overview || {};

  document.getElementById('stat-total-requests').textContent = formatNumber(ov.total_requests || 0);

  const avgTtft = Number(ov.avg_ttft || 0);
  document.getElementById('stat-avg-ttft').innerHTML = `${avgTtft.toFixed(0)} <span style="font-size: 14px; font-weight: normal; color: var(--text-muted);">ms</span>`;

  const avgTps = Number(ov.avg_tps || 0);
  document.getElementById('stat-avg-tps').innerHTML = `${avgTps.toFixed(1)} <span style="font-size: 14px; font-weight: normal; color: var(--text-muted);">tok/s</span>`;

  const hitRate = (Number(ov.cache_hit_rate || 0) * 100).toFixed(1);
  document.getElementById('stat-cache-hit-rate').textContent = `${hitRate}%`;

  const cachedToks = ov.cached_tokens || 0;
  const promptToks = ov.prompt_tokens || 0;
  document.getElementById('stat-cached-tokens-sub').textContent = `${formatNumber(cachedToks)} 缓存 / ${formatNumber(promptToks)} 输入`;

  const compToks = ov.completion_tokens || 0;
  document.getElementById('stat-tokens-summary').textContent = `${formatNumber(promptToks)} / ${formatNumber(compToks)}`;
  document.getElementById('stat-total-tokens-sub').textContent = `总计消耗 ${formatNumber(promptToks + compToks)} Tokens`;

  const totalCredits = Number(ov.total_credits || 0);
  document.getElementById('stat-total-credits').textContent = totalCredits.toFixed(2);
  const creditsRmb = (totalCredits * 0.04).toFixed(2);
  document.getElementById('stat-credits-rmb-sub').textContent = `折合 ¥${creditsRmb} 元 (基准 1 Credit = ¥0.04)`;

  const officialCost = Number(ov.total_official_cost || 0);
  document.getElementById('stat-official-cost').textContent = formatCurrency(officialCost);

  const totalSaved = Number(ov.total_saved || 0);
  document.getElementById('stat-total-saved').textContent = formatCurrency(totalSaved);
}

// --- Render Pricing Table ---
function renderPricingTable() {
  const tbody = document.getElementById('pricing-table-body');
  const list = state.pricing || [];

  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-dim); padding: 20px;">无价格数据</td></tr>';
    return;
  }

  // Update fetch badge with timestamp from first entry
  const firstFetched = list[0]?.last_fetched_at;
  if (firstFetched) {
    const d = new Date(firstFetched);
    const timeStr = isNaN(d.getTime()) ? firstFetched : d.toLocaleString('zh-CN', { hour12: false });
    document.getElementById('pricing-fetch-badge').textContent = `抓取时间：${timeStr} (静态基准)`;
  }

  const factorMap = {
    'dfmodel': '0.1x (深度求索峰谷)',
    'dmodel': '0.5x (深度求索峰谷)',
    'qmodel_38max': '0.5x (错峰 4 折)',
    'qmodel_latest': '0.5x (错峰 2 折)',
    'qmodel': '0.1x (错峰 4 折)',
    'qfmodel': '0.0x (限时免费)',
    'q37fmodel': '0.1x',
    'gmodel': '0.8x',
    'gfmodel': '0.1x',
    'gm51model': '0.6x',
    'kmodel_latest': '1.4x',
    'kmodel': '0.8x (高档 0.6x)',
    'mmodel': '0.2x',
    'auto': '0.5x',
    'lite': '0.0x (免费)',
  };

  tbody.innerHTML = list.map(item => {
    const key = item.model_key;
    const factor = factorMap[key] || '-';
    return `
      <tr>
        <td class="model-name">${item.model_name}</td>
        <td class="model-key">${item.model_key}</td>
        <td class="tabular-nums" style="color: var(--accent-emerald);">¥${Number(item.cached_rate).toFixed(2)}</td>
        <td class="tabular-nums">¥${Number(item.prompt_rate).toFixed(2)}</td>
        <td class="tabular-nums" style="color: var(--accent-amber);">¥${Number(item.completion_rate).toFixed(2)}</td>
        <td><span class="tag-badge tag-reasoning">官方高峰价</span></td>
        <td><span class="tag-badge ${factor.includes('免费') ? 'tag-active' : ''}">${factor}</span></td>
      </tr>
    `;
  }).join('');
}

// --- Render Trends Table ---
function renderTrendsTable() {
  const tbody = document.getElementById('trends-table-body');
  const trends = state.trends || [];

  if (trends.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-dim); padding: 20px;">近 24 小时暂无调用记录</td></tr>';
    return;
  }

  tbody.innerHTML = trends.map(row => {
    return `
      <tr>
        <td class="tabular-nums" style="font-weight: 500;">${row.hour}</td>
        <td class="tabular-nums">${formatNumber(row.request_count)}</td>
        <td class="tabular-nums">${Number(row.avg_ttft).toFixed(0)} ms</td>
        <td class="tabular-nums">${Number(row.avg_tps).toFixed(1)} tok/s</td>
        <td class="tabular-nums">${formatNumber(row.total_tokens)}</td>
        <td class="tabular-nums" style="color: var(--accent-amber); font-weight: 600;">${formatCurrency(row.total_official_cost)}</td>
      </tr>
    `;
  }).join('');
}

// --- Render Models Breakdown Table ---
function renderModelsTable() {
  const tbody = document.getElementById('model-metrics-table-body');
  const models = state.models || [];

  if (models.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-dim); padding: 20px;">暂无模型请求记录</td></tr>';
    return;
  }

  tbody.innerHTML = models.map(row => {
    const hitRate = (Number(row.cache_hit_rate || 0) * 100).toFixed(1);
    return `
      <tr>
        <td class="model-name">${row.model}</td>
        <td class="tabular-nums" style="font-weight: 600;">${formatNumber(row.request_count)}</td>
        <td class="tabular-nums">${Number(row.avg_ttft).toFixed(0)} ms</td>
        <td class="tabular-nums">${Number(row.avg_tps).toFixed(1)} tok/s</td>
        <td class="tabular-nums">${formatNumber(row.prompt_tokens)}</td>
        <td class="tabular-nums" style="color: var(--accent-emerald);">${formatNumber(row.cached_tokens)}</td>
        <td class="tabular-nums">${formatNumber(row.completion_tokens)}</td>
        <td class="tabular-nums">${hitRate}%</td>
        <td class="tabular-nums" style="color: var(--accent-rose);">${Number(row.total_credits).toFixed(2)}</td>
        <td class="tabular-nums" style="color: var(--accent-amber); font-weight: 600;">${formatCurrency(row.total_official_cost)}</td>
      </tr>
    `;
  }).join('');
}

// --- Initialize ---
document.addEventListener('DOMContentLoaded', () => {
  initLockScreen();

  const refreshBtn = document.getElementById('refresh-stats-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      loadAllMetrics();
      showToast('已刷新统计数据', 'info');
    });
  }

  if (checkAuth()) {
    loadAllMetrics();
  }
});