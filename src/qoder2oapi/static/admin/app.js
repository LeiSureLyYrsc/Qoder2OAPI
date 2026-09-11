/**
 * Qoder2OAPI Admin Console Client Script
 * Operator-grade frontend logic without dependencies
 */

const STORAGE_KEY = 'qoder2oapi_api_key';

let state = {
  apiKey: localStorage.getItem(STORAGE_KEY) || '',
  status: null,
  quota: null,
  accounts: [],
  models: [],
  pollingTimer: null,
  pollStartTime: 0,
};

// --- Notifications / Toast ---
function showToast(message, type = 'info', duration = 4000) {
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
    // Auth failed
    throw new Error('UNAUTHORIZED');
  }

  return resp;
}

// Format numbers with commas
function formatNumber(num) {
  if (num === null || num === undefined || isNaN(num)) return '0';
  return Number(num).toLocaleString('zh-CN');
}

// Format tokens (e.g. 200K, 1M, 32K)
function formatTokenCount(tokens) {
  if (!tokens || isNaN(tokens)) return '0';
  const n = Number(tokens);
  if (n >= 1000000) {
    const v = (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1);
    return `${v}M`;
  }
  if (n >= 1000) {
    const v = (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1);
    return `${v}K`;
  }
  return n.toString();
}

// Format Unix Timestamp (ms or sec)
function formatDate(timestamp) {
  if (!timestamp || timestamp <= 0) return '无';
  const ms = timestamp < 10000000000 ? timestamp * 1000 : timestamp;
  const d = new Date(ms);
  if (isNaN(d.getTime())) return '-';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// Mask key for safety
function maskKey(key) {
  if (!key) return '未设置';
  if (key.length <= 8) return '****';
  return `${key.slice(0, 4)}…${key.slice(-4)}`;
}

// --- UI View Switching ---
function showLockScreen() {
  document.getElementById('lock-screen').classList.remove('hidden');
  document.getElementById('main-dashboard').classList.add('hidden');
}

function showDashboard() {
  document.getElementById('lock-screen').classList.add('hidden');
  document.getElementById('main-dashboard').classList.remove('hidden');
}

// --- Status & Bootstrap ---
async function checkAuthAndLoad() {
  const currentKey = localStorage.getItem(STORAGE_KEY);
  if (!currentKey) {
    showLockScreen();
    return;
  }

  try {
    const resp = await api('/api/admin/status');
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    state.status = data;
    showDashboard();
    renderStatusSection();
    loadAccounts();
    loadQuota();
    loadModels();
  } catch (err) {
    if (err.message === 'UNAUTHORIZED') {
      showToast('本代理密钥无效，请重新输入', 'error');
      showLockScreen();
    } else {
      showToast(`连不上本机代理: ${err.message}`, 'error');
      showDashboard(); // Allow viewing interface even if backend had transient error
    }
  }
}

// --- Render Status Section ---
function renderStatusSection() {
  const s = state.status;
  if (!s) return;

  const authDot = document.getElementById('auth-status-dot');
  const authText = document.getElementById('auth-status-text');
  const loginBtn = document.getElementById('login-btn');

  const proxyHost = s.proxy_host || '127.0.0.1';
  const proxyPort = s.proxy_port || 8000;
  const baseUrl = `http://${proxyHost}:${proxyPort}/v1`;

  document.getElementById('proxy-endpoint').textContent = baseUrl;

  const currentKey = localStorage.getItem(STORAGE_KEY) || '';
  document.getElementById('current-key-display').textContent = maskKey(currentKey);

  const sampleCode = `curl ${baseUrl}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${maskKey(currentKey)}" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}]
  }'`;
  document.getElementById('sample-code-block').textContent = sampleCode;

  const total = Number(s.account_count || 0);
  const usable = Number(s.usable_count || 0);
  document.getElementById('pool-summary').textContent = `${usable} 可用 / 共 ${total}`;

  if (total === 0) {
    authDot.className = 'dot dot-amber';
    authText.textContent = '还没有账号';
    loginBtn.textContent = '登录 Qoder';
  } else {
    authDot.className = usable > 0 ? 'dot dot-green' : 'dot dot-amber';
    authText.textContent = `${total} 个账号 · ${usable} 可用`;
    loginBtn.textContent = '添加账号';
  }
}

// --- Quota Section ---
async function loadQuota() {
  const quotaContainer = document.getElementById('quota-content');
  const quotaEmptyState = document.getElementById('quota-empty-state');

  try {
    const resp = await api('/api/admin/quota');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    state.quota = data;

    const isLoggedIn = state.status && state.status.is_logged_in;

    if (!isLoggedIn || (!data.user_quota && !data.add_on_quota) || (data.user_type === 'none' && data.hard_limit === 0)) {
      quotaContainer.classList.add('hidden');
      quotaEmptyState.classList.remove('hidden');
      return;
    }

    quotaEmptyState.classList.add('hidden');
    quotaContainer.classList.remove('hidden');

    // 1. 套餐额度 (user_quota)
    renderQuotaBar('user', data.user_quota);

    // 2. 加量包额度 (add_on_quota)
    renderQuotaBar('addon', data.add_on_quota);

    // 3. 汇总信息
    document.getElementById('quota-user-type').textContent = formatQuotaSource(data.user_type);
    document.getElementById('quota-expiry').textContent = formatDate(data.expires_at);

    const exceedChip = document.getElementById('quota-exceeded-chip');
    if (data.is_quota_exceeded) {
      exceedChip.className = 'status-badge text-rose';
      exceedChip.innerHTML = '<span class="dot dot-red"></span> 已耗尽';
    } else {
      exceedChip.className = 'status-badge text-emerald';
      exceedChip.innerHTML = '<span class="dot dot-green"></span> 正常';
    }

    renderQuotaAccountLines(data.accounts || []);
  } catch (err) {
    if (err.message === 'UNAUTHORIZED') return;
    showToast(`获取额度失败: ${err.message}`, 'error');
  }
}

function formatQuotaSource(userType) {
  const value = String(userType || '').trim();
  if (!value || value === 'none') return '无账号';
  if (value === 'pool') return '多账号合计';
  if (value === 'personal_professional') return '个人专业版';
  if (value === 'personal') return '个人版';
  if (value === 'team') return '团队版';
  if (value === 'unknown' || value === 'error') return '未知';
  return value;
}

function formatAccountKind(kind) {
  return kind === 'pat' ? '官网令牌' : '浏览器登录';
}

function formatThinkingLevel(level) {
  const map = {
    none: '关闭',
    low: '低',
    medium: '中',
    high: '高',
    xhigh: '很高',
    max: '最大',
  };
  const key = String(level || '').toLowerCase();
  return map[key] || level;
}

function accountRemaining(quota) {
  if (!quota || quota.error) return 0;
  const u = quota.user_quota || {};
  const a = quota.add_on_quota || {};
  return Number(u.remaining || 0) + Number(a.remaining || 0);
}

function renderQuotaAccountLines(items) {
  const el = document.getElementById('quota-account-lines');
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = items.map((item) => {
    const label = item.email || item.name || item.user_id || item.id;
    const rem = accountRemaining(item.quota);
    const flags = item.flags || {};
    let mark = '';
    if (flags.skip_quota) mark = ' · 额度耗尽';
    if (flags.skip_auth) mark += ' · 登录失效';
    return `<div class="quota-account-line"><span>${label}</span><span class="tabular-nums">${formatNumber(rem)} 积分${mark}</span></div>`;
  }).join('');
}

function renderQuotaBar(prefix, quotaObj) {
  const labelElem = document.getElementById(`quota-${prefix}-label`);
  const numsElem = document.getElementById(`quota-${prefix}-nums`);
  const barElem = document.getElementById(`quota-${prefix}-bar`);
  const pctElem = document.getElementById(`quota-${prefix}-pct`);
  const unitElem = document.getElementById(`quota-${prefix}-unit`);

  if (!quotaObj || typeof quotaObj !== 'object' || Object.keys(quotaObj).length === 0) {
    numsElem.innerHTML = '无数据';
    barElem.style.width = '0%';
    pctElem.textContent = '0%';
    return;
  }

  const total = Number(quotaObj.total || 0);
  const used = Number(quotaObj.used || 0);
  const remaining = Number(quotaObj.remaining !== undefined ? quotaObj.remaining : Math.max(0, total - used));
  const unit = quotaObj.unit === 'credits' || !quotaObj.unit ? '积分' : quotaObj.unit;

  const remainingPct = total > 0 ? Math.round((remaining / total) * 100) : 0;

  numsElem.innerHTML = `剩余 <strong>${formatNumber(remaining)}</strong> / 总计 ${formatNumber(total)}`;
  unitElem.textContent = unit;
  pctElem.textContent = `${remainingPct}% 剩余`;

  barElem.style.width = `${Math.min(100, Math.max(0, remainingPct))}%`;

  if (remainingPct < 15) {
    barElem.className = 'progress-fill progress-fill-rose';
  } else if (remainingPct < 40) {
    barElem.className = 'progress-fill progress-fill-amber';
  } else {
    barElem.className = 'progress-fill progress-fill-teal';
  }
}

// --- Models Catalog Section ---
async function loadModels() {
  const tbody = document.getElementById('models-table-body');
  const countElem = document.getElementById('models-count');

  try {
    const resp = await api('/api/admin/models');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const models = data.models || [];
    state.models = models;

    countElem.textContent = `${models.length} 个模型`;
    tbody.innerHTML = '';

    if (models.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 24px; color: var(--text-dim);">还没有模型列表。加入账号后再点刷新。</td></tr>`;
      return;
    }

    models.forEach((m) => {
      const tr = document.createElement('tr');

      // Thinking format
      let thinkingDisplay = '不支持';
      if (m.thinking_levels && m.thinking_levels.length > 0) {
        const levels = m.thinking_levels.map(formatThinkingLevel).join(' / ');
        const def = m.default_thinking ? `（默认 ${formatThinkingLevel(m.default_thinking)}）` : '';
        thinkingDisplay = `${levels}${def}`;
      }

      // Capability Badges
      let badges = '';
      if (m.is_reasoning) {
        badges += `<span class="tag-badge tag-reasoning">推理</span>`;
      }
      if (m.is_vl) {
        badges += `<span class="tag-badge tag-vl">视觉</span>`;
      }
      if (!badges) {
        badges = `<span class="text-dim">-</span>`;
      }

      // Status badge
      const statusHtml = m.enable
        ? `<span class="tag-active">● 启用</span>`
        : `<span class="tag-inactive">○ 停用</span>`;

      tr.innerHTML = `
        <td>
          <div class="model-name">${m.display_name || m.public_id || m.key}</div>
          <div class="model-key">${m.public_id || m.key}${m.public_id && m.key && m.public_id !== m.key ? ` · 内部 ${m.key}` : ''}</div>
        </td>
        <td>
          <span class="tabular-nums" style="font-size: 11.5px;">${thinkingDisplay}</span>
        </td>
        <td class="tabular-nums">${formatTokenCount(m.max_input_tokens)}</td>
        <td class="tabular-nums">${formatTokenCount(m.max_output_tokens)}</td>
        <td>${badges}</td>
        <td>${statusHtml}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    if (err.message === 'UNAUTHORIZED') return;
    showToast(`获取模型列表失败: ${err.message}`, 'error');
  }
}

// --- OAuth Device Flow ---
async function startLogin() {
  const loginBtn = document.getElementById('login-btn');
  const pollingChip = document.getElementById('polling-chip');
  const pollingText = document.getElementById('polling-status-text');

  loginBtn.disabled = true;
  pollingChip.classList.remove('hidden');
  pollingText.textContent = '正在打开登录页…';

  try {
    const resp = await api('/api/admin/login/start', { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    const { login_id, verification_uri } = data;
    if (!login_id || !verification_uri) {
      throw new Error('没有拿到登录地址');
    }

    // Open browser authorization page in a new window
    window.open(verification_uri, '_blank');
    showToast('已打开 Qoder 登录页，请在浏览器里完成授权', 'info');

    // Start polling
    state.pollStartTime = Date.now();
    pollOAuthStatus(login_id);
  } catch (err) {
    stopPolling();
    loginBtn.disabled = false;
    showToast(`无法开始登录: ${err.message}`, 'error');
  }
}

function pollOAuthStatus(loginId) {
  const pollingText = document.getElementById('polling-status-text');

  if (state.pollingTimer) clearInterval(state.pollingTimer);
  let consecutiveErrors = 0;

  state.pollingTimer = setInterval(async () => {
    if (Date.now() - state.pollStartTime > 5 * 60 * 1000) {
      stopPolling();
      showToast('登录等待超时，请再点一次', 'error');
      return;
    }

    try {
      const resp = await api(`/api/admin/login/poll?login_id=${encodeURIComponent(loginId)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      consecutiveErrors = 0;

      if (data.status === 'ok') {
        stopPolling();
        showToast('已加入账号', 'success');
        await checkAuthAndLoad();
      } else if (data.status === 'pending') {
        pollingText.textContent = '等待浏览器授权…';
      } else {
        stopPolling();
        showToast(`登录失败: ${data.message || data.status}`, 'error');
      }
    } catch (err) {
      consecutiveErrors += 1;
      if (consecutiveErrors >= 3) {
        stopPolling();
        showToast(`查询登录状态失败: ${err.message}`, 'error');
      }
    }
  }, 2000);
}

async function loadAccounts() {
  const tbody = document.getElementById('accounts-table-body');
  const countElem = document.getElementById('accounts-count');
  try {
    const resp = await api('/api/admin/accounts');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const accounts = data.accounts || [];
    state.accounts = accounts;
    countElem.textContent = `${accounts.length} 个账号`;
    if (!accounts.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 24px; color: var(--text-dim);">还没有账号。用右上角登录，或粘贴官网个人令牌。</td></tr>`;
      return;
    }
    tbody.innerHTML = '';
    accounts.forEach((acc) => {
      const tr = document.createElement('tr');
      const snap = acc.quota_snapshot || {};
      const rem = accountRemaining(snap);
      const kind = formatAccountKind(acc.kind);
      const label = acc.email || acc.name || acc.user_id || acc.id;
      const enabledChecked = acc.enabled ? 'checked' : '';
      let flagsHtml = '';
      if (acc.skip_quota) {
        flagsHtml += `<button class="flag-chip active-warn" data-action="clear-flags" data-id="${acc.id}">额度耗尽</button>`;
      }
      if (acc.skip_auth) {
        flagsHtml += `<button class="flag-chip active-rose" data-action="clear-flags" data-id="${acc.id}">登录失效</button>`;
      }
      if (!flagsHtml) flagsHtml = `<span class="text-dim">无</span>`;
      const refreshBtn = acc.kind === 'pat'
        ? `<button class="btn btn-sm" data-action="refresh" data-id="${acc.id}">刷新令牌</button>`
        : '';
      const err = acc.last_error
        ? `<div class="text-dim" style="font-size:11px;margin-top:4px;">${acc.last_error}</div>`
        : '';
      tr.innerHTML = `
        <td>${kind}</td>
        <td>
          <div class="model-name">${label}</div>
          <div class="model-key">${acc.user_id || ''}</div>
          ${err}
        </td>
        <td class="tabular-nums">${formatNumber(rem)}</td>
        <td class="tabular-nums">${formatDate(acc.expires_at)}</td>
        <td><input type="checkbox" data-action="toggle-enabled" data-id="${acc.id}" ${enabledChecked}></td>
        <td>${flagsHtml}</td>
        <td class="accounts-actions">
          ${refreshBtn}
          <button class="btn btn-sm btn-danger" data-action="delete" data-id="${acc.id}">删除</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    if (err.message === 'UNAUTHORIZED') return;
    showToast(`获取账号失败: ${err.message}`, 'error');
  }
}

async function addPatAccount() {
  const input = document.getElementById('pat-input');
  const pat = (input.value || '').trim();
  if (!pat) {
    showToast('请粘贴官网个人令牌', 'error');
    return;
  }
  try {
    const resp = await api('/api/admin/accounts/pat', {
      method: 'POST',
      body: JSON.stringify({ pat }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    input.value = '';
    showToast('已添加官网令牌账号', 'success');
    await checkAuthAndLoad();
  } catch (err) {
    showToast(`添加令牌失败: ${err.message}`, 'error');
  }
}

async function patchAccount(id, body) {
  const resp = await api(`/api/admin/accounts/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
}

async function onAccountsTableClick(ev) {
  const btn = ev.target.closest('[data-action]');
  if (!btn) return;
  const id = btn.getAttribute('data-id');
  const action = btn.getAttribute('data-action');
  try {
    if (action === 'clear-flags') {
      await patchAccount(id, { skip_quota: false, skip_auth: false });
      showToast('已清除标记', 'success');
      await checkAuthAndLoad();
    } else if (action === 'toggle-enabled') {
      await patchAccount(id, { enabled: btn.checked });
      await checkAuthAndLoad();
    } else if (action === 'refresh') {
      const resp = await api(`/api/admin/accounts/${encodeURIComponent(id)}/refresh`, { method: 'POST' });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      showToast('已刷新官网令牌', 'success');
      await checkAuthAndLoad();
    } else if (action === 'delete') {
      const ok = await askConfirm('删除后这条账号不再参与转发。', '删除账号');
      if (!ok) return;
      const resp = await api(`/api/admin/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      showToast('已删除', 'success');
      await checkAuthAndLoad();
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function stopPolling() {
  if (state.pollingTimer) {
    clearInterval(state.pollingTimer);
    state.pollingTimer = null;
  }
  const loginBtn = document.getElementById('login-btn');
  if (loginBtn) loginBtn.disabled = false;
  const pollingChip = document.getElementById('polling-chip');
  if (pollingChip) pollingChip.classList.add('hidden');
}

// --- Key Management ---
function openKeyModal() {
  const modal = document.getElementById('key-modal');
  const input = document.getElementById('key-modal-input');
  input.value = localStorage.getItem(STORAGE_KEY) || '';
  modal.classList.remove('hidden');
  input.focus();
}

function closeKeyModal() {
  document.getElementById('key-modal').classList.add('hidden');
}

function saveKeyFromModal() {
  const input = document.getElementById('key-modal-input');
  const val = (input.value || '').trim();
  if (!val) {
    showToast('本代理密钥不能为空', 'error');
    return;
  }
  localStorage.setItem(STORAGE_KEY, val);
  state.apiKey = val;
  closeKeyModal();
  showToast('密钥已保存，正在验证…', 'info');
  checkAuthAndLoad();
}

async function handleRotateKey() {
  const ok = await askConfirm('重新生成后，旧密钥立刻失效，所有客户端都要改用新密钥。', '重新生成本代理密钥');
  if (!ok) return;

  try {
    const resp = await api('/api/admin/api-key/rotate', { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const newKey = data.api_key;

    localStorage.setItem(STORAGE_KEY, newKey);
    state.apiKey = newKey;

    showRotateResultModal(newKey);
    checkAuthAndLoad();
  } catch (err) {
    showToast(`重新生成密钥失败: ${err.message}`, 'error');
  }
}

function showRotateResultModal(newKey) {
  const modal = document.getElementById('rotate-modal');
  const input = document.getElementById('rotated-key-value');
  input.value = newKey;
  modal.classList.remove('hidden');
}

function closeRotateModal() {
  document.getElementById('rotate-modal').classList.add('hidden');
}

// --- Clipboard Copy Helper ---
function copyText(text, successMsg = '已复制到剪贴板') {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(successMsg, 'success');
    }).catch(() => fallbackCopy(text, successMsg));
  } else {
    fallbackCopy(text, successMsg);
  }
}

function askConfirm(message, title) {
  return new Promise((resolve) => {
    const modal = document.getElementById('confirm-modal');
    document.getElementById('confirm-title').textContent = title || '确认';
    document.getElementById('confirm-message').textContent = message;
    modal.classList.remove('hidden');

    const okBtn = document.getElementById('confirm-ok');
    const cancelBtn = document.getElementById('confirm-cancel');
    const finish = (value) => {
      modal.classList.add('hidden');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
  });
}

function fallbackCopy(text, successMsg) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand('copy');
    showToast(successMsg, 'success');
  } catch (e) {
    showToast('复制失败，请手动选择复制', 'error');
  }
  document.body.removeChild(textarea);
}

// --- Event Listeners Setup ---
function setupEventListeners() {
  // Lock screen save
  document.getElementById('lock-save-btn').addEventListener('click', () => {
    const val = (document.getElementById('lock-key-input').value || '').trim();
    if (!val) {
      showToast('请输入本代理密钥', 'error');
      return;
    }
    localStorage.setItem(STORAGE_KEY, val);
    state.apiKey = val;
    checkAuthAndLoad();
  });

  document.getElementById('lock-key-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      document.getElementById('lock-save-btn').click();
    }
  });

  // Top actions
  document.getElementById('login-btn').addEventListener('click', startLogin);
  document.getElementById('refresh-all-btn').addEventListener('click', () => {
    showToast('正在刷新账号、额度和模型…', 'info');
    checkAuthAndLoad();
  });

  // Key operations
  document.getElementById('change-key-btn').addEventListener('click', openKeyModal);
  document.getElementById('rotate-key-btn').addEventListener('click', handleRotateKey);
  document.getElementById('key-modal-cancel').addEventListener('click', closeKeyModal);
  document.getElementById('key-modal-save').addEventListener('click', saveKeyFromModal);

  document.getElementById('rotate-modal-close').addEventListener('click', closeRotateModal);
  document.getElementById('copy-rotated-key-btn').addEventListener('click', () => {
    const val = document.getElementById('rotated-key-value').value;
    copyText(val, '已复制新密钥');
  });

  // Copy sample code
  document.getElementById('copy-sample-code-btn').addEventListener('click', () => {
    const s = state.status;
    const proxyHost = (s && s.proxy_host) || '127.0.0.1';
    const proxyPort = (s && s.proxy_port) || 8000;
    const currentKey = localStorage.getItem(STORAGE_KEY) || '<本代理密钥>';
    
    // Copy full valid curl command with the actual key
    const realCurl = `curl http://${proxyHost}:${proxyPort}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${currentKey}" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "你好"}]
  }'`;
    copyText(realCurl, '已复制调用命令');
  });

  // Copy base URL
  document.getElementById('copy-endpoint-btn').addEventListener('click', () => {
    const endpoint = document.getElementById('proxy-endpoint').textContent;
    copyText(endpoint, '接口地址已复制');
  });

  document.getElementById('add-pat-btn').addEventListener('click', addPatAccount);
  document.getElementById('pat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addPatAccount();
  });
  document.getElementById('accounts-table-body').addEventListener('click', onAccountsTableClick);
  document.getElementById('accounts-table-body').addEventListener('change', onAccountsTableClick);
}

// Initial Boot
window.addEventListener('DOMContentLoaded', () => {
  setupEventListeners();
  checkAuthAndLoad();
});
