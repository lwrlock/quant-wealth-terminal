let chartInstance = null;
let globalAccounts = [];
let globalMarket = null;
let globalPortfolio = null;
const GRADUATION_TARGET_TRY = 650000;


let __toastTimer = null;
function hideToastNow() {
  const toast = document.getElementById('toast-notify');
  if (toast) toast.classList.add('translate-y-12', 'opacity-0', 'pointer-events-none');
  if (__toastTimer) {
    clearTimeout(__toastTimer);
    __toastTimer = null;
  }
}

function showToast(msg, type = 'success') {
  const toast = document.getElementById('toast-notify');
  const toastMsg = document.getElementById('toast-msg');
  const toastIcon = document.getElementById('toast-icon');
  if (!toast || !toastMsg) return;
  if (type === true) type = 'error';
  const durSec = window.appPrefs && window.appPrefs.toastDurationSec !== undefined ? Number(window.appPrefs.toastDurationSec) : 3;
  if (durSec === 0 && type !== 'error') return;
  toastMsg.innerText = msg;
  toast.classList.remove('border-indigo-500/40', 'border-emerald-500/50', 'border-amber-500/50', 'border-rose-500/50');
  if (type === 'warn' || type === 'warning') {
    toast.classList.add('border-amber-500/50');
    if (toastIcon) toastIcon.className = 'fa-solid fa-triangle-exclamation text-amber-400 text-base';
  } else if (type === 'error') {
    toast.classList.add('border-rose-500/50');
    if (toastIcon) toastIcon.className = 'fa-solid fa-circle-exclamation text-rose-400 text-base';
  } else if (type === 'info') {
    toast.classList.add('border-indigo-500/40');
    if (toastIcon) toastIcon.className = 'fa-solid fa-circle-info text-indigo-400 text-base';
  } else {
    toast.classList.add('border-emerald-500/50');
    if (toastIcon) toastIcon.className = 'fa-solid fa-circle-check text-emerald-400 text-base';
  }
  toast.classList.remove('translate-y-12', 'opacity-0', 'pointer-events-none');
  if (__toastTimer) clearTimeout(__toastTimer);
  __toastTimer = setTimeout(() => {
    hideToastNow();
  }, Math.max(1500, durSec * 1000));
}


function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeHttpUrl(url) {
  const u = String(url ?? '').trim();
  return (u.startsWith('https://') || u.startsWith('http://')) ? escapeHtml(u) : '';
}


function parseTrNumber(raw) {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : 0;
  const s = String(raw ?? '').trim().replace(/\s+/g, '');
  if (!s) return 0;
  const normalized = s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s;
  const n = parseFloat(normalized);
  return Number.isFinite(n) ? n : 0;
}

function showTxModalError(msg) {
  const box = document.getElementById('tx-modal-error');
  if (!box) return;
  if (!msg) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = `<i class="fa-solid fa-circle-exclamation shrink-0"></i><span>${escapeHtml(String(msg))}</span>`;
}

function extractApiErrorMsg(errData, fallback = 'İşlem kaydedilemedi!') {
  if (!errData || typeof errData !== 'object') return fallback;
  if (typeof errData.detail === 'string' && errData.detail.trim()) return errData.detail.trim();
  if (Array.isArray(errData.detail) && errData.detail.length) {
    const first = errData.detail[0];
    if (first && first.msg) return String(first.msg);
  }
  if (typeof errData.error === 'string' && errData.error.trim()) return errData.error.trim();
  return fallback;
}


const FIN_PREFS_STORAGE_KEY = 'fin_ui_prefs_v1';
const FIN_PREFS_DEFAULTS = {
  goalTargetTry: 650000,
  goalAnnualYieldPct: 35,
  defaultMonthlyDeposit: 5500,
  etaDisplayMode: 'compound',
  autoRefreshSec: '20',
  refreshOnTabFocus: true,
  moneyDecimals: '2',
  fundShareDecimals: '2',
  confirmDeleteTx: true,
  toastDurationSec: '3',
  liveTitleNetWorth: true,
  chartAnimations: true,
  tickerClickAction: 'buy',
  defaultTab: 'last',
  reduceMotion: false,
  privacyMaskOnBlur: false,
  uiDensity: 'standard',
  highContrast: false,
  backdropMode: 'cosmic',
  tabularNums: true
};
window.appPrefs = Object.assign({}, FIN_PREFS_DEFAULTS);
let __dataPollingTimer = null;

function rescheduleDataPolling() {
  if (__dataPollingTimer) {
    clearInterval(__dataPollingTimer);
    __dataPollingTimer = null;
  }
  const sec = Number(window.appPrefs && window.appPrefs.autoRefreshSec !== undefined ? window.appPrefs.autoRefreshSec : 20);
  if (!Number.isFinite(sec) || sec <= 0) return;
  const ms = Math.max(5000, sec * 1000);
  __dataPollingTimer = setInterval(() => {
    if (document.hidden) return;
    const overlay = document.getElementById('auth-lock-overlay');
    if (overlay && overlay.classList.contains('hidden')) {
      fetchData();
      const nwDays = Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30;
      fetchNetWorthHistory(nwDays);
    }
  }, ms);
}

function applyAppPreferences(prefsInput) {
  window.appPrefs = Object.assign({}, FIN_PREFS_DEFAULTS, prefsInput || window.appPrefs || {});
  const p = window.appPrefs;
  const b = document.body;
  if (b) {
    b.classList.toggle('pref-tabular-nums', p.tabularNums !== false);
    b.classList.toggle('pref-high-contrast', Boolean(p.highContrast));
    b.classList.toggle('pref-compact', p.uiDensity === 'compact');
    b.classList.toggle('pref-spacious', p.uiDensity === 'spacious');
    b.classList.toggle('pref-no-motion', Boolean(p.reduceMotion));
    b.classList.toggle('pref-backdrop-institutional', p.backdropMode === 'institutional');
  }
  const tgtPill = document.getElementById('goal-target-pill');
  if (tgtPill) tgtPill.innerText = '₺' + Math.round(Number(p.goalTargetTry || GRADUATION_TARGET_TRY)).toLocaleString('tr-TR');
  rescheduleDataPolling();
  window.__lastHoldingsSig = null;
  if (typeof updateGoalEtaBadge === 'function') updateGoalEtaBadge();
  if (typeof updateGoalsView === 'function') updateGoalsView();
  if (typeof updateBrowserTabTitle === 'function') updateBrowserTabTitle();
  if (typeof runPortfolioRebalance === 'function') runPortfolioRebalance();
}

async function loadAppPreferences(fetchRemote = true) {
  let localPrefs = {};
  try {
    localPrefs = JSON.parse(localStorage.getItem(FIN_PREFS_STORAGE_KEY) || '{}') || {};
    if (!localStorage.getItem('fin_cosmic_restore_v2')) {
      localStorage.setItem('fin_cosmic_restore_v2', '1');
      if (localPrefs.backdropMode === 'institutional') {
        delete localPrefs.backdropMode;
        localStorage.setItem(FIN_PREFS_STORAGE_KEY, JSON.stringify(localPrefs));
      }
    }
  } catch (_) {
    localPrefs = {};
  }
  let remotePrefs = {};
  if (fetchRemote) {
    try {
      const resp = await fetch('/api/preferences');
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.preferences && typeof data.preferences === 'object') {
          remotePrefs = data.preferences;
        }
      }
    } catch (_) {}
  }
  applyAppPreferences(Object.assign({}, FIN_PREFS_DEFAULTS, localPrefs, remotePrefs));
}

window.addEventListener('blur', () => {
  if (window.appPrefs && window.appPrefs.privacyMaskOnBlur && typeof applyPrivacyUI === 'function') {
    applyPrivacyUI(true);
  }
});
window.addEventListener('focus', () => {
  if (window.appPrefs && window.appPrefs.privacyMaskOnBlur && localStorage.getItem('finans_privacy_mode') !== '1' && typeof applyPrivacyUI === 'function') {
    applyPrivacyUI(false);
  }
});

// =========================================================================
// KURUMSAL MODAL ONAY & GİRİŞ DİYALOGLARI (Native alert/confirm/prompt yerine)
// =========================================================================
let __appDialogResolver = null;
let __appDialogLastFocus = null;

function closeAppDialog(result = null) {
  const modal = document.getElementById('app-dialog-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
  const resolver = __appDialogResolver;
  __appDialogResolver = null;
  if (__appDialogLastFocus && typeof __appDialogLastFocus.focus === 'function') {
    try { __appDialogLastFocus.focus(); } catch (_) {}
  }
  __appDialogLastFocus = null;
  if (typeof resolver === 'function') resolver(result);
}

function showAppConfirm({
  title = 'İşlemi Onayla',
  message = '',
  confirmText = 'Onayla',
  cancelText = 'Vazgeç',
  tone = 'indigo'
} = {}) {
  return new Promise((resolve) => {
    const modal = document.getElementById('app-dialog-modal');
    const titleEl = document.getElementById('app-dialog-title');
    const msgEl = document.getElementById('app-dialog-message');
    const fieldsEl = document.getElementById('app-dialog-fields');
    const confirmBtn = document.getElementById('app-dialog-confirm-btn');
    const cancelBtn = document.getElementById('app-dialog-cancel-btn');
    const iconEl = document.getElementById('app-dialog-icon');
    if (!modal || !titleEl || !msgEl || !confirmBtn) {
      resolve(false);
      return;
    }
    if (typeof __appDialogResolver === 'function') closeAppDialog(false);
    __appDialogLastFocus = document.activeElement;
    __appDialogResolver = resolve;

    titleEl.innerText = title;
    msgEl.innerText = message;
    if (fieldsEl) {
      fieldsEl.innerHTML = '';
      fieldsEl.classList.add('hidden');
    }
    if (cancelBtn) {
      cancelBtn.classList.remove('hidden');
      cancelBtn.innerText = cancelText;
    }
    confirmBtn.innerText = confirmText;

    const toneStyles = {
      danger: {
        btn: 'bg-rose-600 hover:bg-rose-500 text-white shadow-rose-600/25',
        iconWrap: 'bg-rose-500/15 border-rose-400/35 text-rose-300',
        icon: 'fa-solid fa-triangle-exclamation'
      },
      warning: {
        btn: 'bg-amber-600 hover:bg-amber-500 text-white shadow-amber-600/25',
        iconWrap: 'bg-amber-500/15 border-amber-400/35 text-amber-300',
        icon: 'fa-solid fa-circle-exclamation'
      },
      emerald: {
        btn: 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/25',
        iconWrap: 'bg-emerald-500/15 border-emerald-400/35 text-emerald-300',
        icon: 'fa-solid fa-circle-check'
      },
      indigo: {
        btn: 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-600/25',
        iconWrap: 'bg-indigo-500/15 border-indigo-400/35 text-indigo-300',
        icon: 'fa-solid fa-shield-halved'
      }
    };
    const st = toneStyles[tone] || toneStyles.indigo;
    confirmBtn.className = `h-10 px-5 rounded-xl font-bold text-sm transition shadow-lg active:scale-95 ${st.btn}`;
    if (iconEl) {
      iconEl.className = `w-10 h-10 rounded-xl border flex items-center justify-center shrink-0 ${st.iconWrap}`;
      iconEl.innerHTML = `<i class="${st.icon} text-base"></i>`;
    }

    confirmBtn.onclick = () => closeAppDialog(true);
    if (cancelBtn) cancelBtn.onclick = () => closeAppDialog(false);

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    requestAnimationFrame(() => confirmBtn.focus());
  });
}

function showAppPrompt({
  title = 'Bilgi Girişi',
  message = '',
  fields = [],
  confirmText = 'Kaydet',
  cancelText = 'Vazgeç'
} = {}) {
  return new Promise((resolve) => {
    const modal = document.getElementById('app-dialog-modal');
    const titleEl = document.getElementById('app-dialog-title');
    const msgEl = document.getElementById('app-dialog-message');
    const fieldsEl = document.getElementById('app-dialog-fields');
    const confirmBtn = document.getElementById('app-dialog-confirm-btn');
    const cancelBtn = document.getElementById('app-dialog-cancel-btn');
    const iconEl = document.getElementById('app-dialog-icon');
    if (!modal || !titleEl || !msgEl || !fieldsEl || !confirmBtn) {
      resolve(null);
      return;
    }
    if (typeof __appDialogResolver === 'function') closeAppDialog(null);
    __appDialogLastFocus = document.activeElement;
    __appDialogResolver = resolve;

    titleEl.innerText = title;
    msgEl.innerText = message;
    if (iconEl) {
      iconEl.className = 'w-10 h-10 rounded-xl border bg-indigo-500/15 border-indigo-400/35 text-indigo-300 flex items-center justify-center shrink-0';
      iconEl.innerHTML = '<i class="fa-solid fa-pen-to-square text-base"></i>';
    }

    fieldsEl.innerHTML = fields.map((f, idx) => `
      <div>
        <label for="app-dlg-f-${idx}" class="block text-xs font-semibold text-slate-300 mb-1.5">${escapeHtml(f.label || f.name)}</label>
        <input id="app-dlg-f-${idx}" data-dlg-field="${escapeHtml(f.name)}" type="${escapeHtml(f.type || 'text')}" value="${escapeHtml(f.value ?? '')}" placeholder="${escapeHtml(f.placeholder || '')}" ${f.inputmode ? `inputmode="${escapeHtml(f.inputmode)}"` : ''} class="h-11 w-full px-3.5 rounded-xl bg-[#070c1e] border border-white/[0.12] text-white font-mono text-sm outline-none focus:border-indigo-400">
      </div>
    `).join('');
    fieldsEl.classList.remove('hidden');

    if (cancelBtn) {
      cancelBtn.classList.remove('hidden');
      cancelBtn.innerText = cancelText;
      cancelBtn.onclick = () => closeAppDialog(null);
    }
    confirmBtn.innerText = confirmText;
    confirmBtn.className = 'h-10 px-5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-sm transition shadow-lg shadow-indigo-600/25 active:scale-95';

    const submitFields = () => {
      const result = {};
      for (let i = 0; i < fields.length; i++) {
        const f = fields[i];
        const el = document.getElementById(`app-dlg-f-${i}`);
        const val = el ? String(el.value ?? '').trim() : '';
        if (f.required && !val) {
          showToast(`Lütfen "${f.label || f.name}" alanını doldurun.`, 'warn');
          if (el) el.focus();
          return;
        }
        result[f.name] = val;
      }
      closeAppDialog(result);
    };

    confirmBtn.onclick = submitFields;
    fieldsEl.querySelectorAll('input').forEach((inp) => {
      inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          submitFields();
        }
      });
    });

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    requestAnimationFrame(() => {
      const firstInp = document.getElementById('app-dlg-f-0');
      if (firstInp) {
        firstInp.focus();
        firstInp.select();
      }
    });
  });
}

// =========================================================================
// TERMİNAL TERCIHLERİ & GÖRÜNÜM AYARLARI MODALI
// =========================================================================
function openPreferencesModal() {
  const modal = document.getElementById('preferences-modal');
  if (!modal) return;
  const p = Object.assign({}, FIN_PREFS_DEFAULTS, window.appPrefs || {});
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v ?? ''); };
  const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = Boolean(v); };
  setVal('pref-goal-target', p.goalTargetTry);
  setVal('pref-monthly-deposit', p.defaultMonthlyDeposit);
  setVal('pref-annual-yield', p.goalAnnualYieldPct);
  setVal('pref-auto-refresh', p.autoRefreshSec);
  setVal('pref-money-decimals', p.moneyDecimals);
  setVal('pref-share-decimals', p.fundShareDecimals);
  setVal('pref-ui-density', p.uiDensity || 'standard');
  setVal('pref-backdrop-mode', p.backdropMode || 'cosmic');
  setChk('pref-high-contrast', p.highContrast);
  setChk('pref-tabular-nums', p.tabularNums !== false);
  setChk('pref-reduce-motion', p.reduceMotion);
  setChk('pref-chart-animations', p.chartAnimations !== false);
  setChk('pref-confirm-delete', p.confirmDeleteTx !== false);
  setChk('pref-privacy-blur', p.privacyMaskOnBlur);
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closePreferencesModal() {
  const modal = document.getElementById('preferences-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

async function savePreferencesFromModal(e) {
  if (e && e.preventDefault) e.preventDefault();
  const getVal = (id, fallback) => document.getElementById(id)?.value ?? fallback;
  const getChk = (id) => Boolean(document.getElementById(id)?.checked);
  const next = Object.assign({}, FIN_PREFS_DEFAULTS, window.appPrefs || {}, {
    goalTargetTry: Math.max(10000, Number(getVal('pref-goal-target', 650000)) || 650000),
    defaultMonthlyDeposit: Math.max(500, Number(getVal('pref-monthly-deposit', 5500)) || 5500),
    goalAnnualYieldPct: Math.max(1, Math.min(200, Number(getVal('pref-annual-yield', 35)) || 35)),
    autoRefreshSec: String(getVal('pref-auto-refresh', '20')),
    moneyDecimals: String(getVal('pref-money-decimals', '2')),
    fundShareDecimals: String(getVal('pref-share-decimals', '2')),
    uiDensity: String(getVal('pref-ui-density', 'standard')),
    backdropMode: String(getVal('pref-backdrop-mode', 'cosmic')),
    highContrast: getChk('pref-high-contrast'),
    tabularNums: getChk('pref-tabular-nums'),
    reduceMotion: getChk('pref-reduce-motion'),
    chartAnimations: getChk('pref-chart-animations'),
    confirmDeleteTx: getChk('pref-confirm-delete'),
    privacyMaskOnBlur: getChk('pref-privacy-blur')
  });
  try {
    localStorage.setItem(FIN_PREFS_STORAGE_KEY, JSON.stringify(next));
  } catch (_) {}
  applyAppPreferences(next);
  try {
    await fetch('/api/preferences', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ preferences: next })
    });
  } catch (_) {}
  closePreferencesModal();
  if (typeof refreshAllData === 'function') refreshAllData(false, { silentToast: true });
  showToast('Terminal tercihleri ve görünüm ayarları kaydedildi.');
}

async function resetPreferencesDefaults() {
  const fresh = Object.assign({}, FIN_PREFS_DEFAULTS);
  try {
    localStorage.setItem(FIN_PREFS_STORAGE_KEY, JSON.stringify(fresh));
  } catch (_) {}
  applyAppPreferences(fresh);
  try {
    await fetch('/api/preferences', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ preferences: fresh })
    });
  } catch (_) {}
  closePreferencesModal();
  if (typeof refreshAllData === 'function') refreshAllData(false, { silentToast: true });
  showToast('Tercihler kurumsal varsayılanlara döndürüldü.', 'info');
}

function openOfficialStatementPrint() {
  const dateEl = document.getElementById('print-statement-date');
  const totEl = document.getElementById('print-statement-total');
  if (dateEl) {
    dateEl.innerText = new Date().toLocaleString('tr-TR', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    });
  }
  if (totEl && globalPortfolio) {
    totEl.innerText = '₺' + Number(globalPortfolio.total_value_try || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  window.print();
}
