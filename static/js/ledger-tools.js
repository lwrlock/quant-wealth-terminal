function openBankSyncModal() {
  const modal = document.getElementById('bank-sync-modal');
  const wrap = document.getElementById('bank-sync-rows');
  if (!modal || !wrap) return;
  const held = ((globalPortfolio && globalPortfolio.funds) || []).filter(f => Number(f.shares || 0) > 0);
  if (!held.length) {
    wrap.innerHTML = '<div class="text-sm text-slate-400 py-4 text-center">Aktif fon pozisyonun bulunmuyor.</div>';
  } else {
    wrap.innerHTML = held.map(f => {
      const sh = Number(f.shares || 0);
      const cost = (sh * Number(f.buy_price || 0)).toFixed(2);
      const pnl = Number(f.profit_try || 0).toFixed(2);
      return `
        <div class="p-3.5 rounded-2xl bg-[#070c1e] border border-white/[0.09] space-y-2" data-sync-code="${escapeHtml(f.code)}">
          <div class="flex items-center justify-between text-xs font-mono">
            <span class="text-sm font-bold text-white">${escapeHtml(f.code)} <span class="text-slate-400 font-normal">(${sh.toLocaleString('tr-TR', {maximumFractionDigits:2})} pay)</span></span>
            <span class="text-slate-400">Güncel Birim: ₺${Number(f.current_price || 0).toFixed(6)}</span>
          </div>
          <div class="grid grid-cols-2 gap-2.5">
            <div>
              <label class="block text-[11px] text-slate-400 mb-1">Maliyet (₺)</label>
              <input type="text" inputmode="decimal" data-sync-field="cost" value="${cost}" class="h-9 w-full px-3 rounded-xl bg-[#0b1226] border border-white/[0.12] text-white font-mono text-sm outline-none focus:border-emerald-400">
            </div>
            <div>
              <label class="block text-[11px] text-slate-400 mb-1">Top K/Z (₺)</label>
              <input type="text" inputmode="decimal" data-sync-field="profit" value="${pnl}" class="h-9 w-full px-3 rounded-xl bg-[#0b1226] border border-white/[0.12] text-emerald-300 font-mono text-sm outline-none focus:border-emerald-400">
            </div>
          </div>
        </div>
      `;
    }).join('');
  }
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closeBankSyncModal() {
  const modal = document.getElementById('bank-sync-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

async function submitBankSyncModal() {
  const wrap = document.getElementById('bank-sync-rows');
  const btn = document.getElementById('bank-sync-save-btn');
  if (!wrap) return;
  const items = [];
  wrap.querySelectorAll('[data-sync-code]').forEach(row => {
    const code = row.getAttribute('data-sync-code');
    const costRaw = String(row.querySelector('[data-sync-field="cost"]')?.value || '').replace(',', '.').trim();
    const profitRaw = String(row.querySelector('[data-sync-field="profit"]')?.value || '').replace(',', '.').trim();
    const cost = Number(costRaw);
    const profit = Number(profitRaw);
    if (code && Number.isFinite(cost) && cost > 0 && Number.isFinite(profit)) {
      items.push({ code, cost_try: cost, profit_try: profit });
    }
  });
  if (!items.length) {
    showToast('Geçerli maliyet ve K/Z değeri gir.', 'error');
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/portfolio/calibrate-bank', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Eşitleme başarısız');
    closeBankSyncModal();
    showToast('Ziraat Mobil maliyet & K/Z değerleriyle birebir eşitlendi!', 'success');
    if (typeof loadPortfolio === 'function') await loadPortfolio(true);
  } catch (e) {
    showToast(e.message || 'Eşitleme hatası', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Hedef Kumbaraları & Hafta Sonu Nema Güncelleme

// Tarayıcı Oto-Kurtarma Kasası (Render geçici disk sıfırlanmalarına karşı son kullanıcı koruması)
const VAULT_STORAGE_KEY = 'fin_ledger_vault_v1';
const VAULT_DELETED_KEY = 'fin_ledger_vault_deleted_v1';
let __vaultSaveTimer = null;
let __vaultReconciledBoot = null;
let __bgWarmupTimer = null;

function recordDeletedVaultTx(txId) {
  if (!txId) return;
  try {
    const list = JSON.parse(localStorage.getItem(VAULT_DELETED_KEY) || '[]');
    const next = Array.isArray(list) ? list : [];
    if (!next.includes(String(txId))) next.push(String(txId));
    localStorage.setItem(VAULT_DELETED_KEY, JSON.stringify(next.slice(-120)));
  } catch (_) {}
}

function getDeletedVaultTxIds() {
  try {
    const list = JSON.parse(localStorage.getItem(VAULT_DELETED_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch (_) { return []; }
}

function scheduleLedgerVaultSave(bootId) {
  if (__vaultSaveTimer) clearTimeout(__vaultSaveTimer);
  __vaultSaveTimer = setTimeout(async () => {
    try {
      const resp = await fetch('/api/backup/download');
      if (!resp.ok) return;
      const backup = await resp.json();
      if (!backup || !Array.isArray(backup.transactions)) return;
      localStorage.setItem(VAULT_STORAGE_KEY, JSON.stringify({
        bootId: bootId || window.__serverBootId || null,
        savedAt: Date.now(),
        backup
      }));
    } catch (_) {}
  }, 900);
}

async function checkAndReconcileEphemeralVault(resPort, txsFromServer) {
  const pers = resPort && resPort.persistence;
  if (!pers || !pers.boot_id) return false;
  window.__serverBootId = pers.boot_id;
  if (!pers.ephemeral_warning) {
    scheduleLedgerVaultSave(pers.boot_id);
    return false;
  }
  if (__vaultReconciledBoot === pers.boot_id) return false;
  try {
    const raw = localStorage.getItem(VAULT_STORAGE_KEY);
    if (!raw) {
      __vaultReconciledBoot = pers.boot_id;
      scheduleLedgerVaultSave(pers.boot_id);
      return false;
    }
    const vault = JSON.parse(raw);
    const backup = vault && vault.backup;
    const vaultTxs = (backup && Array.isArray(backup.transactions)) ? backup.transactions : [];
    if (!vaultTxs.length) {
      __vaultReconciledBoot = pers.boot_id;
      return false;
    }
    const deleted = new Set(getDeletedVaultTxIds());
    const serverIds = new Set((txsFromServer || globalTransactions || []).map(t => String(t.id)));
    const missing = vaultTxs.filter(t => t && t.id && !deleted.has(String(t.id)) && !serverIds.has(String(t.id)));
    __vaultReconciledBoot = pers.boot_id;
    if (vault.bootId && vault.bootId !== pers.boot_id && (missing.length > 0 || deleted.size > 0)) {
      const recResp = await fetch('/api/backup/reconcile', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ backup, deleted_ids: Array.from(deleted) })
      });
      if (recResp.ok) {
        const recData = await recResp.json().catch(() => ({}));
        if (recData.restored) {
          scheduleLedgerVaultSave(pers.boot_id);
          showToast(`Sunucu yeniden başladığı için ${recData.recovered_tx_count || missing.length} işlemin tarayıcı kasasından kurtarıldı!`, 'success');
          return true;
        }
      }
    }
  } catch (_) {}
  scheduleLedgerVaultSave(pers.boot_id);
  return false;
}

function scheduleBackgroundWarmup() {
  if (__bgWarmupTimer) clearTimeout(__bgWarmupTimer);
  __bgWarmupTimer = setTimeout(() => {
    if (document.hidden) return;
    const run = () => Promise.allSettled([
      fetchLiveCouncilVerdict(),
      fetchLiveNews(),
      fetchTelegramStatus(),
      fetchCashflowAnalytics(),
      fetchRecurringRules(),
      fetchPriceAlerts(),
      fetchHelpersSummary()
    ]);
    if (typeof window.requestIdleCallback === 'function') {
      window.requestIdleCallback(() => run(), { timeout: 1800 });
    } else {
      run();
    }
  }, 900);
}


function classifyFundBucket(code, fundObj) {
  const c = String(code || '').toUpperCase();
  const cat = String((fundObj && (fundObj.category || fundObj.type)) || '').toLowerCase();
  const name = String((fundObj && (fundObj.name || fundObj.title)) || '').toLowerCase();
  const text = `${cat} ${name}`;
  if (['ZBJ', 'TZL', 'PPN', 'PRY', 'HVT', 'FIL', 'BGP'].includes(c) || /para piy|ppf|likit|kısa vadeli|borçlanma/.test(text)) {
    return 'ppf';
  }
  if (['TCA', 'KZL', 'KTN', 'OTJ', 'OGD', 'GZY', 'NJF'].includes(c) || /altın|altin|kıymetli|maden|gümüş|gumus|gold/.test(text)) {
    return 'altin';
  }
  return 'growth';
}

function getSelectedTxFundCode() {
  const sel = document.getElementById('tx-code');
  if (!sel) return '';
  if (sel.value === '__CUSTOM__') {
    const inp = document.getElementById('tx-custom-code');
    const cleaned = String(inp ? inp.value : '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6);
    if (inp && inp.value !== cleaned) inp.value = cleaned;
    return cleaned;
  }
  return String(sel.value || '').toUpperCase().trim();
}

function syncTxPendingDefault() {
  const pendChk = document.getElementById('tx-is-pending');
  if (!pendChk) return;
  const now = new Date();
  const fcode = getSelectedTxFundCode() || 'ZBJ';
  const bucket = classifyFundBucket(fcode, globalMarket?.funds?.[fcode]);
  const isInstantT0 = (fcode === 'TZL');
  const afterCutoff = (now.getDay() === 0 || now.getDay() === 6) || (now.getHours() > 13 || (now.getHours() === 13 && now.getMinutes() >= 30));
  pendChk.checked = (!isInstantT0) && (afterCutoff || bucket !== 'ppf');
  const pLbl = document.getElementById('tx-is-pending-label');
  if (pLbl) {
    pLbl.innerHTML = isInstantT0
      ? '<strong>Valörde Beklet (İsteğe Bağlı):</strong> <b>TZL</b> Ziraat’te genellikle 7/24 T+0 anında onaylanır; sadece emir beklemedeyse işaretleyin.'
      : '<strong>Valörde (Henüz Hesaba Geçmedi):</strong> Emir gerçekleşene kadar sahte kâr/zarar yazılmasını engeller.';
  }
}

function onTxCodeSelectChange(fromUserChange = false) {
  const sel = document.getElementById('tx-code');
  const wrap = document.getElementById('tx-custom-code-wrap');
  const inp = document.getElementById('tx-custom-code');
  if (sel && wrap) {
    if (sel.value === '__CUSTOM__') {
      wrap.classList.remove('hidden');
      if (inp) setTimeout(() => inp.focus(), 20);
    } else {
      wrap.classList.add('hidden');
    }
  }
  if (fromUserChange && document.getElementById('tx-type')?.value === 'ALIM') {
    syncTxPendingDefault();
  }
  if (typeof updateTxLivePreview === 'function') updateTxLivePreview();
}

function getCatalogFundCodes(marketFunds, ownedFunds) {
  const baseOrder = ['ZBJ', 'TCA', 'AFT', 'TI3', 'TZL', 'KZL'];
  const seen = new Set();
  const ownedSet = new Set();
  (ownedFunds || []).forEach(f => {
    const c = String(f.code || '').toUpperCase().trim();
    if (c) ownedSet.add(c);
  });
  baseOrder.forEach(c => seen.add(c));
  Object.keys(marketFunds || {}).forEach(c => {
    const uc = String(c || '').toUpperCase().trim();
    if (uc) seen.add(uc);
  });
  ownedSet.forEach(c => seen.add(c));
  return Array.from(seen).sort((a, b) => {
    const ao = ownedSet.has(a) ? 0 : 1;
    const bo = ownedSet.has(b) ? 0 : 1;
    if (ao !== bo) return ao - bo;
    const ai = baseOrder.indexOf(a);
    const bi = baseOrder.indexOf(b);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    return a.localeCompare(b);
  });
}

function syncDynamicFundSelects(marketFunds, ownedFunds) {
  const mMap = marketFunds || (globalMarket && globalMarket.funds) || {};
  const oList = ownedFunds || (globalPortfolio && globalPortfolio.funds) || [];
  const ownedSet = new Set(oList.map(f => String(f.code || '').toUpperCase().trim()).filter(Boolean));
  const codes = getCatalogFundCodes(mMap, oList);

  const shortFundTitle = (code) => {
    const meta = mMap[code] || oList.find(f => String(f.code || '').toUpperCase() === code) || {};
    return String(meta.name || meta.title || code).trim();
  };

  const txSel = document.getElementById('tx-code');
  if (txSel) {
    const prev = txSel.value;
    txSel.innerHTML = codes.map(c => {
      const price = Number(mMap[c]?.price || 0);
      const pText = price > 0 ? ` (₺${price.toFixed(4)})` : '';
      const ownTag = ownedSet.has(c) ? ' (Portföyde)' : '';
      return `<option value="${escapeHtml(c)}">${escapeHtml(c)} — ${escapeHtml(shortFundTitle(c))}${escapeHtml(pText)}${ownTag}</option>`;
    }).join('') + `<option value="__CUSTOM__">+ Diğer TEFAS Fon Kodu Gir (MAC, TTE...)</option>`;
    if (prev && (prev === '__CUSTOM__' || codes.includes(prev))) {
      txSel.value = prev;
    }
    onTxCodeSelectChange();
  }

  const alertSel = document.getElementById('alert-code');
  if (alertSel) {
    const prev = alertSel.value;
    const fundOpts = codes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)} — ${escapeHtml(shortFundTitle(c))}</option>`).join('');
    alertSel.innerHTML = fundOpts + `<option value="GRAM_ALTIN">Gram Altın (HAS)</option><option value="USD">USD / TRY</option>`;
    if (prev && (codes.includes(prev) || prev === 'GRAM_ALTIN' || prev === 'USD')) alertSel.value = prev;
  }

  const splitPickers = [
    { id: 'split-code-ppf', bucket: 'ppf', fallback: 'ZBJ' },
    { id: 'split-code-altin', bucket: 'altin', fallback: 'TCA' },
    { id: 'split-code-growth', bucket: 'growth', fallback: 'AFT' },
  ];
  let splitChanged = false;
  splitPickers.forEach(sp => {
    const el = document.getElementById(sp.id);
    if (!el) return;
    const prev = el.value || sp.fallback;
    const sortedForBucket = [...codes].sort((a, b) => {
      const am = classifyFundBucket(a, mMap[a]) === sp.bucket ? 0 : 1;
      const bm = classifyFundBucket(b, mMap[b]) === sp.bucket ? 0 : 1;
      return am - bm;
    });
    el.innerHTML = sortedForBucket.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)} · ${escapeHtml(shortFundTitle(c))}</option>`).join('');
    if (codes.includes(prev)) el.value = prev;
    else if (codes.includes(sp.fallback)) el.value = sp.fallback;
    if (el.value !== prev) splitChanged = true;
  });
  if (typeof calculateSplit === 'function' && (splitChanged || document.getElementById('split-code-ppf'))) {
    calculateSplit();
  }
}


let globalTransactions = [];
let ledgerTypeFilter = 'ALL';
let ledgerDateFilter = 'ALL';
let ledgerSortCol = 'date';
let ledgerSortDir = 'desc';
let ledgerShownCount = 50;

function setLedgerDateFilter(rangeVal, btn) {
  ledgerDateFilter = rangeVal;
  ledgerShownCount = 50;
  document.querySelectorAll('#ledger-date-pills .ledger-date-pill').forEach(el => {
    el.className = 'ledger-date-pill min-h-[34px] px-3 rounded-lg bg-[#070c1e] border border-white/[0.08] text-slate-300 hover:border-indigo-400/40 text-xs transition';
  });
  if (btn) {
    btn.className = 'ledger-date-pill min-h-[34px] px-3 rounded-lg bg-indigo-500/20 border border-indigo-400/40 text-indigo-200 text-xs font-semibold transition';
  }
  renderFilteredLedger();
}

function setLedgerTypeFilter(typeVal, btn) {
  ledgerTypeFilter = typeVal;
  ledgerShownCount = 50;
  document.querySelectorAll('#ledger-filter-pills .ledger-pill').forEach(el => {
    el.className = 'ledger-pill min-h-[40px] px-3.5 py-1.5 rounded-xl bg-[#070c1e] text-slate-300 border border-white/[0.08] text-[13px] transition';
  });
  if (btn) {
    btn.className = 'ledger-pill min-h-[40px] px-3.5 py-1.5 rounded-xl bg-indigo-600 text-white font-semibold text-[13px] transition';
  }
  renderFilteredLedger();
}

function sortLedgerBy(col) {
  if (ledgerSortCol === col) {
    ledgerSortDir = ledgerSortDir === 'asc' ? 'desc' : 'asc';
  } else {
    ledgerSortCol = col;
    ledgerSortDir = (col === 'date' || col === 'amount') ? 'desc' : 'asc';
  }
  renderFilteredLedger(true);
}

function loadMoreLedgerRows() {
  ledgerShownCount += 50;
  renderFilteredLedger(true);
}

function showAllLedgerRows() {
  ledgerShownCount = Math.max(50, (window.__lastFilteredTransactions || []).length);
  renderFilteredLedger(true);
}

function renderFilteredLedger(keepPage = false) {
  if (!keepPage) ledgerShownCount = 50;
  const tbody = document.getElementById('ledger-table');
  if (!tbody) return;
  tbody.innerHTML = '';

  const dockLedgerBadge = document.getElementById('dock-badge-ledger');
  if (dockLedgerBadge) {
    const cnt = (globalTransactions || []).length;
    dockLedgerBadge.classList.toggle('hidden', cnt === 0);
    dockLedgerBadge.innerText = cnt > 99 ? '99+' : String(cnt);
  }

  const nowDate = new Date();
  const todayIso = `${nowDate.getFullYear()}-${String(nowDate.getMonth() + 1).padStart(2, '0')}-${String(nowDate.getDate()).padStart(2, '0')}`;
  const monthIso = todayIso.slice(0, 7);
  const sevenDaysAgo = new Date(nowDate.getTime() - 6 * 86400000);
  const sevenIso = `${sevenDaysAgo.getFullYear()}-${String(sevenDaysAgo.getMonth() + 1).padStart(2, '0')}-${String(sevenDaysAgo.getDate()).padStart(2, '0')}`;

  const query = (document.getElementById('ledger-search-input')?.value || '').toLowerCase().trim();
  const filtered = (globalTransactions || []).filter(t => {
    if (ledgerTypeFilter !== 'ALL') {
      if (ledgerTypeFilter === 'VIRMAN') {
        if (t.type !== 'VIRMAN' && t.type !== 'TRANSFER') return false;
      } else if (t.type !== ledgerTypeFilter) {
        return false;
      }
    }
    if (ledgerDateFilter !== 'ALL') {
      const d = String(t.date || '').slice(0, 10);
      if (ledgerDateFilter === 'TODAY' && d !== todayIso) return false;
      if (ledgerDateFilter === '7D' && d < sevenIso) return false;
      if (ledgerDateFilter === 'MONTH' && !d.startsWith(monthIso)) return false;
    }
    if (!query) return true;
    const hay = `${t.date} ${t.type} ${t.account_id || ''} ${t.to_account_id || ''} ${t.category || ''} ${t.code || ''} ${t.description || ''}`.toLowerCase();
    return hay.includes(query);
  });

  document.querySelectorAll('th[data-ledger-sort]').forEach(th => {
    const c = th.dataset.ledgerSort;
    const arrow = th.querySelector('.sort-arrow');
    if (c === ledgerSortCol) {
      th.setAttribute('aria-sort', ledgerSortDir === 'asc' ? 'ascending' : 'descending');
      if (arrow) arrow.innerText = ledgerSortDir === 'asc' ? '▲' : '▼';
    } else {
      th.setAttribute('aria-sort', 'none');
      if (arrow) arrow.innerText = '↕';
    }
  });

  const sorted = [...filtered].sort((a, b) => {
    const m = ledgerSortDir === 'asc' ? 1 : -1;
    switch (ledgerSortCol) {
      case 'type': return m * String(a.type || '').localeCompare(String(b.type || ''), 'tr');
      case 'account': return m * String(a.account_id || '').localeCompare(String(b.account_id || ''), 'tr');
      case 'category': return m * String(a.category || a.code || '').localeCompare(String(b.category || b.code || ''), 'tr');
      case 'amount': return m * (Number(a.amount_try || 0) - Number(b.amount_try || 0));
      default: return m * String(a.date || '').localeCompare(String(b.date || ''));
    }
  });

  window.__lastFilteredTransactions = sorted;

  const summaryEl = document.getElementById('ledger-summary-badge');
  if (summaryEl) {
    const sumAmt = sorted.reduce((acc, it) => acc + Number(it.amount_try || 0), 0);
    summaryEl.innerText = `${sorted.length} kayıt • Hacim: ₺${sumAmt.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  const flowStrip = document.getElementById('ledger-flow-strip');
  if (flowStrip) {
    let inSum = 0, outSum = 0, fundSum = 0;
    sorted.forEach(it => {
      const v = Number(it.amount_try || 0);
      if (it.type === 'GİRİŞ') inSum += v;
      else if (it.type === 'ÇIKIŞ') outSum += v;
      else if (it.type === 'ALIM' || it.type === 'SATIM') fundSum += v;
    });
    const net = inSum - outSum;
    flowStrip.innerHTML = `
      <span class="px-2.5 py-1 rounded-lg bg-emerald-500/12 border border-emerald-500/25 text-emerald-300">Giriş: +₺${inSum.toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>
      <span class="px-2.5 py-1 rounded-lg bg-rose-500/12 border border-rose-500/25 text-rose-300">Çıkış: -₺${outSum.toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>
      <span class="px-2.5 py-1 rounded-lg bg-indigo-500/12 border border-indigo-500/25 text-indigo-300">Fon Hacmi: ₺${fundSum.toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>
      <span class="px-2.5 py-1 rounded-lg bg-white/[0.06] border border-white/[0.1] ${net >= 0 ? 'text-emerald-200' : 'text-rose-200'}">Net Nakit: ${net >= 0 ? '+' : '-'}₺${Math.abs(net).toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>
    `;
  }

  const pagEl = document.getElementById('ledger-pagination-bar');
  if (sorted.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">Filtreye uyan işlem bulunamadı.</td></tr>`;
    if (pagEl) pagEl.classList.add('hidden');
    return;
  }

  const visibleRows = sorted.slice(0, ledgerShownCount);
  const frag = document.createDocumentFragment();
  visibleRows.forEach(t => {
    const row = document.createElement('tr');
    row.className = 'hover:bg-slate-800/35 transition-colors';
    const typeBadge = t.type === 'GİRİŞ' ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' :
                      t.type === 'ALIM' ? 'bg-indigo-500/15 text-indigo-300 border border-indigo-500/30' :
                      t.type === 'ÇIKIŞ' ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30' :
                      t.type === 'VIRMAN' || t.type === 'TRANSFER' ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/30' :
                      'bg-amber-500/15 text-amber-300 border border-amber-500/30';

    const accText = t.to_account_id ? `${t.account_id} → ${t.to_account_id}` : (t.account_id || '5001');

    row.innerHTML = `
      <td class="py-3.5 text-slate-300 text-xs font-mono">${escapeHtml(t.date)}</td>
      <td class="py-3.5"><span class="px-3 py-1 rounded-full text-xs font-bold ${typeBadge}">${escapeHtml(t.type)}</span></td>
      <td class="py-3.5 text-slate-200 font-mono text-xs">${escapeHtml(accText)}</td>
      <td class="py-3.5 text-slate-200">${escapeHtml(t.category || t.code)}</td>
      <td class="py-3.5 font-bold font-mono privacy-mask ${t.type === 'GİRİŞ' || t.type === 'SATIM' ? 'text-emerald-400' : 'text-white'}">₺${Number(t.amount_try || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2})}</td>
      <td class="py-3.5 text-slate-300 font-mono text-xs privacy-mask">${t.shares > 0 ? escapeHtml(t.shares) + ' pay (' + escapeHtml(t.code) + ')' : '—'}</td>
      <td class="py-3.5 text-slate-200">${escapeHtml(t.description)}</td>
      <td class="py-3.5 text-right whitespace-nowrap no-print">
        <button data-tx-id="${escapeHtml(t.id)}" onclick="openEditTxModal(this.dataset.txId)" class="w-8 h-8 rounded-lg inline-flex items-center justify-center text-slate-400 hover:text-indigo-300 hover:bg-white/[0.06] transition mr-1" title="İşlemi Düzenle">
          <i class="fa-solid fa-pen text-xs"></i>
        </button>
        <button data-tx-id="${escapeHtml(t.id)}" onclick="deleteTx(this.dataset.txId)" class="w-8 h-8 rounded-lg inline-flex items-center justify-center text-slate-400 hover:text-rose-400 hover:bg-white/[0.06] transition" title="İşlemi Sil ve Kasayı Geri Al">
          <i class="fa-solid fa-trash-can text-xs"></i>
        </button>
      </td>
    `;
    frag.appendChild(row);
  });
  tbody.replaceChildren(frag);

  if (pagEl) {
    if (sorted.length > visibleRows.length) {
      pagEl.classList.remove('hidden');
      const lbl = document.getElementById('ledger-pagination-label');
      if (lbl) lbl.innerText = `${visibleRows.length} / ${sorted.length} işlem gösteriliyor`;
    } else {
      pagEl.classList.add('hidden');
    }
  }
}

async function fetchTransactions() {
  try {
    const res = await fetch('/api/transactions').then(r => r.json());
    globalTransactions = res.transactions || [];
    renderFilteredLedger();
    return globalTransactions;
  } catch (e) {
    console.error("Ledger error:", e);
    return globalTransactions || [];
  }
}

async function undoLastTx() {
  const ok = await showAppConfirm({
    title: 'Son İşlemi Geri Al',
    message: 'En son işlenen kayıt defterden silinecek ve ilgili hesap bakiyesi ile fon payları önceki durumuna döndürülecektir. Onaylıyor musunuz?',
    confirmText: 'Geri Al',
    tone: 'warning'
  });
  if (!ok) return;
  const removedId = (globalTransactions && globalTransactions[0] && globalTransactions[0].id) || null;
  try {
    const res = await fetch('/api/transactions/undo', { method: 'POST' }).then(r => r.json());
    if (res.status === 'ok') {
      recordDeletedVaultTx(res.tx?.id || removedId);
      showToast(res.message || "Son işlem geri alındı.");
      refreshAllData(false, { silentToast: true });
    } else {
      showToast(res.message || "Geri alınacak işlem bulunamadı.", "warn");
    }
  } catch (e) {
    showToast("Geri alma işlemi başarısız oldu.", "error");
  }
}

async function deleteTx(id) {
  const needConfirm = !window.appPrefs || window.appPrefs.confirmDeleteTx !== false;
  if (needConfirm) {
    const ok = await showAppConfirm({
      title: 'İşlemi Sil',
      message: 'Bu işlem silindiğinde ilgili hesap bakiyesi ve fon payları otomatik olarak önceki haline döndürülecektir. Devam edilsin mi?',
      confirmText: 'Sil ve Geri Al',
      tone: 'danger'
    });
    if (!ok) return;
  }
  await fetch('/api/transactions/' + id, {method: 'DELETE'});
  recordDeletedVaultTx(id);
  refreshAllData(false, { silentToast: true });
  showToast("İşlem silindi ve bakiye geri alındı.");
}

function setSplitPct(pct) {
  const slider = document.getElementById('slider-invest-pct');
  if (slider) {
    slider.value = pct;
    calculateSplit();
  }
}

window.__splitWeights = { ppf: 45, altin: 35, growth: 20 };

function setSplitProfile(ppfPct, altinPct, growthPct, btn) {
  window.__splitWeights = { ppf: ppfPct, altin: altinPct, growth: growthPct };
  document.querySelectorAll('.split-prof-btn').forEach(b => {
    b.className = 'split-prof-btn px-2.5 py-1 rounded-lg bg-[#070c1e] hover:bg-indigo-500/20 border border-white/[0.08] text-slate-200 transition';
  });
  if (btn) {
    btn.className = 'split-prof-btn px-2.5 py-1 rounded-lg bg-indigo-500/20 border border-indigo-400/40 text-indigo-200 font-bold transition';
  }
  calculateSplit();
}

function calculateSplit() {
  const rent = parseFloat(document.getElementById('split-rent').value) || 0;
  const kyk = parseFloat(document.getElementById('split-kyk').value) || 0;
  const totalIncome = rent + kyk;

  const w = window.__splitWeights || { ppf: 45, altin: 35, growth: 20 };
  const investPct = parseInt(document.getElementById('slider-invest-pct').value) || 35;
  document.getElementById('split-invest-pct-text').innerText = `%${investPct} (₺${Math.round(totalIncome * (investPct / 100)).toLocaleString('tr-TR')})`;

  const segP = document.getElementById('split-seg-ppf');
  const segA = document.getElementById('split-seg-altin');
  const segG = document.getElementById('split-seg-growth');
  if (segP) segP.style.width = w.ppf + '%';
  if (segA) segA.style.width = w.altin + '%';
  if (segG) segG.style.width = w.growth + '%';
  const lblP = document.getElementById('split-pct-ppf-lbl');
  const lblA = document.getElementById('split-pct-altin-lbl');
  const lblG = document.getElementById('split-pct-growth-lbl');
  if (lblP) lblP.innerText = `%${w.ppf}`;
  if (lblA) lblA.innerText = `%${w.altin}`;
  if (lblG) lblG.innerText = `%${w.growth}`;

  const totalInvest = totalIncome * (investPct / 100);
  const living = totalIncome - totalInvest;

  const tzlAmount = Math.round(totalInvest * (w.ppf / 100));
  const tcaAmount = Math.round(totalInvest * (w.altin / 100));
  const aftAmount = Math.round(totalInvest * (w.growth / 100));

  document.getElementById('split-tzl-amount').innerText = '₺' + tzlAmount.toLocaleString('tr-TR');
  document.getElementById('split-tca-amount').innerText = '₺' + tcaAmount.toLocaleString('tr-TR');
  document.getElementById('split-aft-amount').innerText = '₺' + aftAmount.toLocaleString('tr-TR');
  document.getElementById('split-living-amount').innerText = '₺' + Math.round(living).toLocaleString('tr-TR');

  const ppfCode = (document.getElementById('split-code-ppf')?.value || 'ZBJ').toUpperCase();
  const altinCode = (document.getElementById('split-code-altin')?.value || 'TCA').toUpperCase();
  const growthCode = (document.getElementById('split-code-growth')?.value || 'AFT').toUpperCase();
  const zbjP = Number(globalMarket?.funds?.[ppfCode]?.price || 5.63);
  const tcaP = Number(globalMarket?.funds?.[altinCode]?.price || 0.7556);
  const aftP = Number(globalMarket?.funds?.[growthCode]?.price || 0.8993);
  const zbjEst = document.getElementById('split-tzl-shares');
  const tcaEst = document.getElementById('split-tca-shares');
  const aftEst = document.getElementById('split-aft-shares');
  const lotSnap = (amt, px, code) => {
    if (px <= 0 || amt <= 0) return 0;
    const step = getFundLotStep(code);
    return Math.floor(Math.floor((amt + 1e-9) / px) / step) * step;
  };
  if (zbjEst && zbjP > 0) zbjEst.innerText = `~${lotSnap(tzlAmount, zbjP, ppfCode).toLocaleString('tr-TR')} pay ${ppfCode}`;
  if (tcaEst && tcaP > 0) tcaEst.innerText = `~${lotSnap(tcaAmount, tcaP, altinCode).toLocaleString('tr-TR')} pay ${altinCode}`;
  if (aftEst && aftP > 0) aftEst.innerText = `~${lotSnap(aftAmount, aftP, growthCode).toLocaleString('tr-TR')} pay ${growthCode}`;

  const yEl = document.getElementById('splitter-yield-val');
  if (yEl) {
    const m1 = Number(globalMarket?.funds?.[ppfCode]?.monthly_yield_pct || 3.4);
    const m2 = Number(globalMarket?.funds?.[altinCode]?.monthly_yield_pct || 4.0);
    const m3 = Number(globalMarket?.funds?.[growthCode]?.monthly_yield_pct || 4.5);
    const wMonthlyPct = (m1 * w.ppf + m2 * w.altin + m3 * w.growth) / 100;
    const estTryGain = totalInvest * (wMonthlyPct / 100);
    yEl.innerText = `${wMonthlyPct >= 0 ? '+' : ''}%${wMonthlyPct.toFixed(2)} / ay (~${estTryGain >= 0 ? '+' : ''}₺${Math.round(estTryGain).toLocaleString('tr-TR')})`;
  }
}

async function commitSplitToLedger() {
  const rent = parseFloat(document.getElementById('split-rent').value) || 0;
  const kyk = parseFloat(document.getElementById('split-kyk').value) || 0;
  const totalIncome = rent + kyk;
  const investPct = parseInt(document.getElementById('slider-invest-pct').value) || 35;
  const totalInvest = Math.round(totalIncome * (investPct / 100));
  const livingReserve = Math.max(0, Math.round(totalIncome - totalInvest));
  const recordIncome = Boolean(document.getElementById('split-record-income')?.checked);

  const w = window.__splitWeights || { ppf: 45, altin: 35, growth: 20 };
  const ppfCode = (document.getElementById('split-code-ppf')?.value || 'ZBJ').toUpperCase();
  const altinCode = (document.getElementById('split-code-altin')?.value || 'TCA').toUpperCase();
  const growthCode = (document.getElementById('split-code-growth')?.value || 'AFT').toUpperCase();
  const weights = [
    { code: ppfCode, pct: w.ppf, label: `PPF (${ppfCode})` },
    { code: altinCode, pct: w.altin, label: `Altın (${altinCode})` },
    { code: growthCode, pct: w.growth, label: `Büyüme (${growthCode})` },
  ];

  const okSplit = await showAppConfirm({
    title: 'Akıllı Dağıtımı Deftere İşle',
    message:
      `Toplam Gelir: ₺${totalIncome.toLocaleString('tr-TR')} | ` +
      `5001 Rezerv: ₺${livingReserve.toLocaleString('tr-TR')} | ` +
      `5004 Yatırım: ₺${totalInvest.toLocaleString('tr-TR')} (${ppfCode} %${w.ppf}, ${altinCode} %${w.altin}, ${growthCode} %${w.growth}). ` +
      `Saat/valör kuralına göre ileri tarihli fonlar otomatik Valörde bekletilir.`,
    confirmText: 'Deftere İşle',
    tone: 'emerald'
  });
  if (!okSplit) return;

  const res = await fetch('/api/allocator/execute', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      income_try: totalIncome,
      living_reserve_try: livingReserve,
      weights,
      record_income: recordIncome,
      source_note: 'KYK / Gelir Akıllı Dağıtım'
    })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || "Dağıtım işlenemedi.");
    return;
  }

  const poCnt = (data.pending_orders_created || []).length;
  const instCnt = (data.instant_buys || []).length;
  showToast(`Dağıtım uygulandı! ${instCnt} fon anlık alındı, ${poCnt} fon valör kuyruğuna eklendi.`);
  switchTab('terminal');
  refreshAllData();
}


function quickAction(actionType) {
  openTxModal();
  if (actionType === 'rent') {
    document.getElementById('tx-type').value = 'GİRİŞ';
    adjustTxFields();
    document.getElementById('tx-amount').value = 11000;
    document.getElementById('tx-desc').value = "Kasım Depo Kirası Geliri";
    if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5001';
  } else if (actionType === 'kyk') {
    document.getElementById('tx-type').value = 'GİRİŞ';
    adjustTxFields();
    document.getElementById('tx-amount').value = 4000;
    document.getElementById('tx-desc').value = "Aylık KYK Kredisi (Sıfır Faizli)";
    if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5001';
  } else if (actionType === 'kyk8k') {
    document.getElementById('tx-type').value = 'GİRİŞ';
    adjustTxFields();
    document.getElementById('tx-amount').value = 8000;
    document.getElementById('tx-desc').value = "İlk 2 Aylık Toplu KYK Kredisi (8K)";
    if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5001';
  } else if (actionType === 'virman') {
    document.getElementById('tx-type').value = 'VIRMAN';
    adjustTxFields();
    document.getElementById('tx-amount').value = 10000;
    document.getElementById('tx-desc').value = "5001 Ana Hesaptan 5004 Yatırım Hesabına Virman";
    if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5001';
    if (document.getElementById('tx-to-account')) document.getElementById('tx-to-account').value = '5004';
  }
}


function openTxModal() {
  showTxModalError('');
  const submitBtn = document.getElementById('tx-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.innerText = 'Kaydet ve Kasayı Güncelle';
  }
  updateAccountDropdowns(globalAccounts);
  adjustTxFields();
  updateTxSelectedAccBalance();
  updateTxLivePreview();
  document.getElementById('tx-modal').classList.remove('hidden');
}
function closeTxModal() {
  showTxModalError('');
  document.getElementById('tx-modal').classList.add('hidden');
}

function adjustTxFields() {
  const type = document.getElementById('tx-type').value;
  const codeWrap = document.getElementById('tx-code-wrap');
  const sharesWrap = document.getElementById('tx-shares-wrap');
  const toAccWrap = document.getElementById('tx-to-account-wrap');
  const accLabel = document.getElementById('tx-account-label');
  const pendWrap = document.getElementById('tx-pending-wrap');
  const pendChk = document.getElementById('tx-is-pending');
  const swapBtn = document.getElementById('tx-swap-dir-btn');
  if (swapBtn) {
    swapBtn.classList.toggle('hidden', type !== 'ALIM' && type !== 'SATIM');
  }

  if (type === 'ALIM') {
    codeWrap.style.display = 'block';
    sharesWrap.style.display = 'block';
    toAccWrap.style.display = 'none';
    accLabel.innerText = "HANGİ HESAPTAN ALINDI? (ÖRN: 5004 VADESİZ)";
    if (pendWrap) pendWrap.style.display = 'flex';
    syncTxPendingDefault();
  } else if (type === 'SATIM') {
    codeWrap.style.display = 'block';
    sharesWrap.style.display = 'block';
    toAccWrap.style.display = 'none';
    accLabel.innerText = "PARANIN GEÇECEĞİ HESAP (ÖRN: 5004 VADESİZ)";
    if (pendWrap) pendWrap.style.display = 'none';
  } else if (type === 'VIRMAN') {
    codeWrap.style.display = 'none';
    sharesWrap.style.display = 'none';
    toAccWrap.style.display = 'block';
    accLabel.innerText = "KAYNAK HESAP (PARANIN ÇIKACAĞI HESAP)";
    if (pendWrap) pendWrap.style.display = 'none';
  } else if (type === 'GİRİŞ') {
    codeWrap.style.display = 'none';
    sharesWrap.style.display = 'none';
    toAccWrap.style.display = 'none';
    accLabel.innerText = "GİRİŞ YAPILAN HESAP (ÖRN: 5001 ANA HESAP)";
    if (pendWrap) pendWrap.style.display = 'none';
  } else if (type === 'ÇIKIŞ') {
    codeWrap.style.display = 'none';
    sharesWrap.style.display = 'none';
    toAccWrap.style.display = 'none';
    accLabel.innerText = "HARCAMA YAPILAN HESAP (ÖRN: 5001 VEYA CÜZDAN)";
    if (pendWrap) pendWrap.style.display = 'none';
  }
  if (typeof updateTxSelectedAccBalance === 'function') updateTxSelectedAccBalance();
  updateTxLivePreview();
}

async function handleNewTx(e) {
  e.preventDefault();
  showTxModalError('');
  const type = document.getElementById('tx-type').value;
  const code = getSelectedTxFundCode();
  if ((type === 'ALIM' || type === 'SATIM') && !code) {
    const msg = 'Lütfen geçerli bir TEFAS fon kodu girin (ör. MAC, TI3).';
    showTxModalError(msg);
    showToast(msg, 'error');
    return;
  }
  const amount = parseTrNumber(document.getElementById('tx-amount').value);
  if (amount <= 0) {
    const msg = 'Lütfen 0’dan büyük geçerli bir işlem tutarı (TL) girin.';
    showTxModalError(msg);
    showToast(msg, 'error');
    document.getElementById('tx-amount')?.focus();
    return;
  }
  const shares = parseTrNumber(document.getElementById('tx-shares').value);
  const accountId = document.getElementById('tx-account').value || globalAccounts?.[0]?.id || '5001';
  const toAccountId = document.getElementById('tx-to-account').value || '5004';
  const rawDesc = document.getElementById('tx-desc').value.trim();
  const catSel = document.getElementById('tx-category')?.value || 'AUTO';
  const autoCat = type === 'ALIM' || type === 'SATIM' ? 'Fon İşlemi' : (type === 'VIRMAN' ? 'Kurum İçi Virman' : 'Nakit Hareketi');
  const finalCat = catSel === 'AUTO' ? autoCat : catSel;
  const desc = rawDesc || (type === 'ALIM' || type === 'SATIM' ? `${code} Fon ${type === 'ALIM' ? 'Alımı' : 'Satışı'}` : (type === 'VIRMAN' ? `${accountId} → ${toAccountId} Virman` : `${finalCat} (${type})`));
  const isPending = (type === 'ALIM') && Boolean(document.getElementById('tx-is-pending')?.checked);

  const submitBtn = document.getElementById('tx-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerText = 'Kaydediliyor...';
  }

  try {
    const resp = await fetch('/api/transactions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        type: type,
        category: finalCat,
        amount_try: amount,
        shares: shares,
        code: type === 'ALIM' || type === 'SATIM' ? code : 'TRY',
        account_id: accountId,
        to_account_id: toAccountId,
        description: desc,
        is_pending: isPending
      })
    });
    if (resp.status === 401) {
      const errMsg = 'Oturum süresi doldu. Lütfen PIN şifresiyle tekrar giriş yapın.';
      showTxModalError(errMsg);
      showToast(errMsg, 'error');
      if (typeof checkAuthStatus === 'function') checkAuthStatus();
      return;
    }
    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      const errMsg = extractApiErrorMsg(errData, 'İşlem kaydedilemedi!');
      showTxModalError(errMsg);
      showToast(errMsg, 'error');
      return;
    }

    const resData = await resp.json().catch(() => ({}));
    closeTxModal();
    document.getElementById('tx-amount').value = '';
    document.getElementById('tx-shares').value = '';
    document.getElementById('tx-desc').value = '';
    if (document.getElementById('tx-custom-code')) document.getElementById('tx-custom-code').value = '';
    refreshAllData(false, { silentToast: true });
    const leftoverTry = Number(resData.leftover_try || 0);
    if (isPending) {
      showToast(leftoverTry >= 0.01 ? `Emir valörde kaydedildi · Kalan ₺${leftoverTry.toFixed(2)} kasada.` : "Fon alım emri valörde kaydedildi. Valör tamamlanana kadar kâr/zarar nötr kalır.");
    } else if (leftoverTry >= 0.01) {
      showToast(`İşlem kaydedildi · Artan ₺${leftoverTry.toFixed(2)} hesabında kaldı.`);
    } else {
      showToast("İşlem deftere kaydedildi ve kasa senkronize edildi.");
    }
  } catch (err) {
    const errMsg = 'Sunucuya bağlanılamadı. Bağlantınızı kontrol edip tekrar deneyin.';
    showTxModalError(errMsg);
    showToast(errMsg, 'error');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerText = 'Kaydet ve Kasayı Güncelle';
    }
  }
}

async function settlePendingOrder(orderId, code, amountTry, livePrice) {
  const dlgRes = await showAppPrompt({
    title: `${code} Valör Emrini Onayla`,
    message: `${code} emri hesabınıza geçtiyse gerçekleşen birim fiyatı doğrulayın (boş bırakılırsa canlı ₺${Number(livePrice || 0).toFixed(4)} esas alınır).`,
    confirmText: 'Portföye İşle',
    fields: [
      { name: 'price', label: 'Gerçekleşen Birim Fiyat (₺)', value: Number(livePrice || 0).toFixed(6), inputmode: 'decimal' }
    ]
  });
  if (!dlgRes) return;
  const parsedPrice = parseFloat(String(dlgRes.price || '').replace(',', '.'));
  const payload = { order_id: orderId };
  if (!isNaN(parsedPrice) && parsedPrice > 0) payload.settled_price = parsedPrice;
  const res = await fetch('/api/pending-orders/settle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || "Emir onaylanamadı.", "error");
    return;
  }
  refreshAllData();
  showToast(`${code} valörden çıktı: ${Math.round(data.settled_shares || 0)} pay aktif fona eklendi.`);
}

async function cancelPendingOrder(orderId) {
  const ok = await showAppConfirm({
    title: 'Valör Emrini İptal Et',
    message: 'Valördeki bu fon emrini iptal edip ayrılan tutarı ilgili hesaba iade etmek istiyor musunuz?',
    confirmText: 'Emri İptal Et',
    tone: 'danger'
  });
  if (!ok) return;
  const res = await fetch('/api/pending-orders/cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ order_id: orderId })
  });
  if (res.ok) {
    refreshAllData();
    showToast("Bekleyen emir iptal edildi ve bakiye hesaba iade edildi.");
  }
}

async function moveFundToPending(code) {
  const ok = await showAppConfirm({
    title: `${code} Pozisyonunu Valöre Al`,
    message: `${code} pozisyonu henüz işleme girmediği için valördeki emirlere çekilsin mi? İşlem gerçekleşene kadar kâr/zarar sıfır, tutar bloke olarak korunur.`,
    confirmText: 'Valöre Çek',
    tone: 'warning'
  });
  if (!ok) return;
  const res = await fetch('/api/pending-orders/move-from-fund', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ code })
  });
  if (res.ok) {
    refreshAllData();
    showToast(`${code} valöre çekildi — işleme girene kadar sahte zarar gösterilmeyecek.`);
  } else {
    const d = await res.json().catch(() => ({}));
    showToast(d.detail || "İşlem yapılamadı.");
  }
}

async function saveValorConfig(mode) {
  try {
    const res = await fetch('/api/valor/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mode })
    });
    if (res.status === 401) {
      showToast('Oturum kilitlendi. Lütfen tekrar giriş yapın.', 'warn');
      if (typeof checkAuthStatus === 'function') checkAuthStatus();
      return;
    }
    if (res.ok) {
      const label = mode === 'auto' ? '14:30 Otopilot (TEFAS fiyatı çıkınca otomatik işler)' : (mode === 'ask' ? 'TEFAS Çıkınca Telegram/Web Onay Sorar' : 'Manuel');
      showToast(`Valör modu güncellendi: ${label}`);
      if (mode === 'auto') {
        checkValorNow(true);
      }
    }
  } catch (err) {
    showToast('Valör modu kaydedilemedi.', 'error');
  }
}

async function checkValorNow(silentPending = false) {
  const btn = document.getElementById('valor-check-btn');
  const icon = document.getElementById('valor-check-icon');
  const lbl = document.getElementById('valor-check-label');
  const banner = document.getElementById('valor-check-status-banner');
  if (btn) btn.disabled = true;
  if (icon) icon.className = 'fa-solid fa-rotate fa-spin';
  if (lbl) lbl.innerText = 'TEFAS Sorgulanıyor...';
  try {
    const resp = await fetch('/api/valor/check', { method: 'POST' });
    if (resp.status === 401) {
      showToast('Oturum kilitlendi. Lütfen tekrar giriş yapın.', 'warn');
      if (typeof checkAuthStatus === 'function') checkAuthStatus();
      return;
    }
    const res = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      showToast(res.detail || 'TEFAS kontrolü sırasında hata oluştu.', 'error');
      return;
    }

    const details = Array.isArray(res.details) ? res.details : [];
    const settled = Array.isArray(res.auto_settled) ? res.auto_settled : [];
    const readyOrders = Array.isArray(res.ready_orders) ? res.ready_orders : [];
    const notif = Array.isArray(res.notified) ? res.notified : [];

    if (banner) {
      const liveTag = res.tefas_refreshed
        ? `<span class="text-emerald-300 font-semibold"><i class="fa-solid fa-circle-check mr-1"></i>Canlı TEFAS Güncellendi (${escapeHtml(res.checked_at || '')})</span>`
        : `<span class="text-sky-300">Önbellek Kontrolü (${escapeHtml(res.checked_at || '')})</span>`;
      const summaryTxt = details.map(d =>
        `${escapeHtml(d.code)}: ${escapeHtml(d.reason || d.stage_badge || '')}`
      ).join(' · ') || 'Bekleyen emir yok';
      banner.classList.remove('hidden');
      banner.innerHTML = `<span>${liveTag}</span><span class="text-slate-300">${summaryTxt}</span>`;
    }

    window.__lastPendingSig = null;
    await refreshAllData(false, { silentToast: true });

    if (settled.length > 0) {
      showToast(`${settled.length} adet emir güncel TEFAS fiyatıyla portföye geçirildi.`);
    } else if (readyOrders.length > 0 && !silentPending) {
      const first = readyOrders[0];
      const askTxt = `${first.code} için fiyat kesinleşti (₺${Number(first.confirmed_price || 0).toFixed(4)} → ${Math.round(first.projected_shares || 0).toLocaleString('tr-TR')} pay). Hemen portföye işlensin mi?`;
      const okSettle = await showAppConfirm({
        title: `${first.code} Valör Fiyatı Hazır`,
        message: askTxt,
        confirmText: 'Portföye İşle',
        tone: 'emerald'
      });
      if (okSettle) {
        const setResp = await fetch('/api/pending-orders/settle', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            order_id: first.order_id,
            settled_price: first.confirmed_price,
            settled_shares: first.projected_shares
          })
        });
        if (setResp.ok) {
          window.__lastPendingSig = null;
          await refreshAllData(false, { silentToast: true });
          showToast(`${first.code} emri portföye geçirildi.`);
        }
      } else if (notif.length > 0) {
        showToast("TEFAS fiyatı yayınlandı — Telegram'a onay butonu gönderildi.");
      }
    } else if (!silentPending) {
      const firstDet = details[0];
      const extra = firstDet ? ` (${firstDet.reason})` : '';
      showToast(`TEFAS kontrol edildi (${res.checked || 0} emir)${extra}`, 'info');
    }
  } catch (err) {
    showToast('TEFAS kontrolüne bağlanılamadı.', 'error');
  } finally {
    if (btn) btn.disabled = false;
    if (icon) icon.className = 'fa-solid fa-rotate';
    if (lbl) lbl.innerText = 'Kontrol Et';
  }
}

function renderTaxBreakdown(taxSummary) {
  if (!taxSummary) return;
  const listEl = document.getElementById('tax-breakdown-list');
  const badgeEl = document.getElementById('tax-benchmark-badge');
  const rates = taxSummary.rates || {};
  const dynWrap = document.getElementById('tax-rates-dynamic-wrap');
  if (dynWrap) {
    const rateCodes = Array.from(new Set([
      ...Object.keys(rates),
      ...(taxSummary.funds || []).map(f => f.code),
      ...((globalPortfolio && globalPortfolio.funds) || []).map(f => f.code)
    ].map(c => String(c || '').toUpperCase().trim()).filter(Boolean)));
    const bucketColor = (c) => {
      const b = classifyFundBucket(c, globalMarket?.funds?.[c]);
      return b === 'ppf' ? 'text-emerald-300' : (b === 'altin' ? 'text-amber-300' : 'text-cyan-300');
    };
    const knownIdMap = { ZBJ: 'tax-rate-zbj', TCA: 'tax-rate-tca', AFT: 'tax-rate-aft' };
    dynWrap.innerHTML = rateCodes.map(c => {
      const val = rates[c] !== undefined ? Number(rates[c]) : (classifyFundBucket(c, globalMarket?.funds?.[c]) === 'ppf' ? 17.5 : 15);
      const idAttr = knownIdMap[c] ? ` id="${knownIdMap[c]}"` : ` id="tax-rate-${escapeHtml(c.toLowerCase())}"`;
      return `<label class="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.08] ${bucketColor(c)}"><span class="font-bold">${escapeHtml(c)}</span><input data-tax-code="${escapeHtml(c)}"${idAttr} type="number" step="0.5" value="${escapeHtml(String(val))}" class="tax-rate-input w-12 h-7 px-1 rounded bg-[#0c111c] border border-white/10 text-white text-center font-bold focus:outline-none"></label>`;
    }).join('');
  } else {
    if (document.getElementById('tax-rate-zbj') && rates.ZBJ !== undefined) document.getElementById('tax-rate-zbj').value = rates.ZBJ;
    if (document.getElementById('tax-rate-tca') && rates.TCA !== undefined) document.getElementById('tax-rate-tca').value = rates.TCA;
    if (document.getElementById('tax-rate-aft') && rates.AFT !== undefined) document.getElementById('tax-rate-aft').value = rates.AFT;
  }

  if (badgeEl && taxSummary.benchmarks) {
    const bm = taxSummary.benchmarks;
    badgeEl.innerText = `30G Net PPF: +${bm.ppf_30d_net_pct}% vs USD ${bm.usd_30d_pct >= 0 ? '+' : ''}${bm.usd_30d_pct}%`;
    badgeEl.className = `text-xs px-2.5 py-1 rounded-full border font-medium ${bm.ppf_beats_usd ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : 'bg-amber-500/15 text-amber-300 border-amber-500/30'}`;
  }

  if (!listEl) return;
  const activeFunds = (taxSummary.funds || []).filter(f => f.gross_value_try > 0);
  if (!activeFunds.length) {
    listEl.innerHTML = `<div class="text-slate-400 text-xs py-1.5">Aktif fon pozisyonu yok.</div>`;
    return;
  }
  const tot = taxSummary.totals || {};
  listEl.innerHTML = activeFunds.map(f => `
    <div class="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 py-2 border-b border-white/[0.05] text-xs">
      <span class="text-slate-200 font-bold">${escapeHtml(f.code)} <span class="text-slate-400 font-normal">(%${f.stopaj_pct})</span></span>
      <span class="text-slate-300 font-mono text-[11px]">Brüt ₺${Number(f.gross_value_try).toFixed(0)} · Vergi <span class="text-rose-300">-₺${Number(f.estimated_tax_try).toFixed(2)}</span> · Net <span class="text-emerald-300 font-bold">₺${Number(f.net_exit_value_try).toFixed(2)}</span></span>
    </div>
  `).join('') + `
    <div class="flex items-center justify-between pt-2 text-sm font-bold">
      <span class="text-white">Toplam Net Çıkış (${tot.net_value_usd ? '$' + tot.net_value_usd : ''})</span>
      <span class="text-emerald-400 font-mono">₺${Number(tot.net_exit_value_try || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2})}</span>
    </div>
  `;
}

async function saveTaxRates() {
  const payload = {};
  document.querySelectorAll('.tax-rate-input[data-tax-code]').forEach(inp => {
    const code = String(inp.dataset.taxCode || '').toUpperCase().trim();
    const val = parseFloat(inp.value);
    if (code && !Number.isNaN(val)) payload[code] = val;
  });
  if (Object.keys(payload).length === 0) {
    payload.ZBJ = parseFloat(document.getElementById('tax-rate-zbj')?.value || 15);
    payload.TZL = payload.ZBJ;
    payload.TCA = parseFloat(document.getElementById('tax-rate-tca')?.value || 15);
    payload.AFT = parseFloat(document.getElementById('tax-rate-aft')?.value || 15);
  }
  const res = await fetch('/api/tax/rates', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (res.ok) {
    refreshAllData();
    showToast("Stopaj oranları güncellendi & Net kârlar yeniden hesaplandı!");
  }
}


function getFundLotStep(code) {
  const c = String(code || '').toUpperCase().trim();
  const mStep = Number(globalMarket?.funds?.[c]?.lot_step || 0);
  if (mStep >= 1) return Math.round(mStep);
  if (c === 'TZL') return 100;
  if (c === 'TCA') return 500;
  return 1;
}

function updateTxLivePreview() {
  const box = document.getElementById('tx-live-preview');
  if (!box) return;
  const type = document.getElementById('tx-type')?.value;
  const amount = parseTrNumber(document.getElementById('tx-amount')?.value);

  if (type === 'ÇIKIŞ') {
    if (amount <= 0) {
      box.classList.add('hidden');
      return;
    }
    const futureVal = Math.round(amount * Math.pow(1.35, 4));
    box.classList.remove('hidden');
    box.innerHTML = `
      <div class="text-slate-300 leading-relaxed">
        <span class="text-amber-300 font-semibold">Küçük Hatırlatma:</span>
        Bugün harcanan <span class="font-mono text-white">₺${amount.toLocaleString('tr-TR', {maximumFractionDigits: 2})}</span> fonda 4 yıl bekleseydi yıllık ~%35 getiriyle yaklaşık <span class="font-mono text-emerald-300 font-semibold">₺${futureVal.toLocaleString('tr-TR')}</span> olabilirdi.
      </div>
    `;
    return;
  }

  if (type !== 'ALIM' && type !== 'SATIM') {
    box.classList.add('hidden');
    return;
  }
  const code = getSelectedTxFundCode() || 'ZBJ';
  const manualSh = parseTrNumber(document.getElementById('tx-shares')?.value);
  const unitP = Number(globalMarket?.funds?.[code]?.price || 0);
  if (amount <= 0 || unitP <= 0) {
    box.classList.add('hidden');
    return;
  }
  const lotStep = getFundLotStep(code);
  let fullShares = 0;
  let usedAmt = 0;
  let effectiveUnitP = unitP;
  if (manualSh > 0) {
    fullShares = manualSh;
    usedAmt = amount;
    effectiveUnitP = amount / manualSh;
  } else {
    const rawUnits = Math.floor((amount + 1e-9) / unitP);
    fullShares = Math.floor(rawUnits / lotStep) * lotStep;
    usedAmt = Math.round(fullShares * unitP * 100) / 100;
  }
  const leftover = Math.max(0, amount - usedAmt);
  box.classList.remove('hidden');
  if (type === 'ALIM' && fullShares < lotStep && manualSh <= 0) {
    const minReq = lotStep * unitP;
    box.innerHTML = `<span class="text-amber-300 font-semibold">${escapeHtml(code)} minimum ${lotStep.toLocaleString('tr-TR')} pay ve katları şeklinde alınabilir (1 lot ≈ ₺${minReq.toFixed(2)}, birim ₺${unitP.toFixed(4)}).</span>`;
    return;
  }
  box.innerHTML = `
    <div class="flex justify-between"><span class="text-slate-400">Canlı Fiyat (${escapeHtml(code)} · Katı: ${lotStep}):</span><span class="text-white font-semibold">₺${effectiveUnitP.toFixed(4)}</span></div>
    <div class="flex justify-between"><span class="text-slate-400">${manualSh > 0 ? 'Girilen Adet:' : 'Ziraat Tam Lot Adet:'}</span><span class="text-emerald-400 font-bold">${fullShares.toLocaleString('tr-TR', {maximumFractionDigits: 4})} pay</span></div>
    <div class="flex justify-between"><span class="text-slate-400">Düşülecek / Kasada Kalan:</span><span class="text-indigo-300">₺${usedAmt.toFixed(2)} • Kalan ₺${leftover.toFixed(2)}</span></div>
  `;
}

function openEditTxModal(txId) {
  const tx = (globalTransactions || []).find(t => t.id === txId);
  if (!tx) return;
  document.getElementById('edit-tx-id').value = tx.id;
  document.getElementById('edit-tx-date').value = tx.date || '';
  document.getElementById('edit-tx-category').value = tx.category || '';
  document.getElementById('edit-tx-amount').value = tx.amount_try || 0;
  document.getElementById('edit-tx-shares').value = tx.shares || 0;
  document.getElementById('edit-tx-desc').value = tx.description || '';
  const shWrap = document.getElementById('edit-tx-shares-wrap');
  if (shWrap) shWrap.style.display = (tx.type === 'ALIM' || tx.type === 'SATIM') ? 'block' : 'none';
  document.getElementById('edit-tx-modal').classList.remove('hidden');
}

function closeEditTxModal() {
  const m = document.getElementById('edit-tx-modal');
  if (m) m.classList.add('hidden');
}

async function handleEditTx(e) {
  e.preventDefault();
  const id = document.getElementById('edit-tx-id').value;
  const payload = {
    date: document.getElementById('edit-tx-date').value.trim(),
    category: document.getElementById('edit-tx-category').value.trim(),
    amount_try: parseTrNumber(document.getElementById('edit-tx-amount').value),
    description: document.getElementById('edit-tx-desc').value.trim()
  };
  const shWrap = document.getElementById('edit-tx-shares-wrap');
  if (shWrap && shWrap.style.display !== 'none') {
    payload.shares = parseTrNumber(document.getElementById('edit-tx-shares').value);
  }
  const res = await fetch('/api/transactions/' + encodeURIComponent(id), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (res.status === 401) {
    showToast('Oturum süresi doldu. Lütfen tekrar giriş yapın.', 'error');
    if (typeof checkAuthStatus === 'function') checkAuthStatus();
    return;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    showToast(extractApiErrorMsg(err, 'Güncelleme başarısız.'), 'error');
    return;
  }
  closeEditTxModal();
  refreshAllData(false, { silentToast: true });
  showToast('İşlem güncellendi ve kasa bakiyeleri eşitlendi.');
}


async function fetchCashflowAnalytics(month = '') {
  try {
    const url = month ? `/api/analytics/cashflow?month=${encodeURIComponent(month)}` : '/api/analytics/cashflow';
    const res = await fetch(url).then(r => r.json());
    const sel = document.getElementById('cashflow-month-select');
    if (sel && res.available_months) {
      const prev = month || res.month;
      sel.innerHTML = '<option value="ALL">Tüm Aylar</option>' + res.available_months.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('');
      sel.value = prev;
    }
    const totalInflow = Number(res.total_inflow || 0);
    const totalOutflow = Number(res.total_outflow || 0);
    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
    setTxt('cf-inflow', '₺' + totalInflow.toLocaleString('tr-TR', {maximumFractionDigits: 0}));
    setTxt('cf-outflow', '₺' + totalOutflow.toLocaleString('tr-TR', {maximumFractionDigits: 0}));
    setTxt('cf-invested', '₺' + Number(res.total_invested || 0).toLocaleString('tr-TR', {maximumFractionDigits: 0}));
    setTxt('cf-savings-rate', `%${Number(res.savings_rate_pct || 0).toFixed(1)}`);

    const cats = res.expense_by_category || [];
    const dColors = cats.length ? ['#f43f5e', '#fbbf24', '#818cf8', '#38bdf8', '#10b981', '#c084fc'] : ['#1e293b'];
    const listEl = document.getElementById('cf-category-list');
    if (listEl) {
      listEl.innerHTML = cats.length === 0
        ? '<div class="text-xs text-slate-400 py-1.5">Seçili dönemde harcama kaydı yok.</div>'
        : cats.slice(0, 6).map((c, idx) => `
          <div data-cat-idx="${idx}" class="cf-cat-row flex justify-between items-center py-1.5 px-2 rounded-lg hover:bg-white/[0.05] cursor-pointer transition border-b border-white/[0.05] text-xs">
            <span class="text-slate-200 truncate flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full flex-shrink-0" style="background:${dColors[idx % dColors.length]}"></span>
              <span>${escapeHtml(c.category)}</span>
            </span>
            <span class="text-rose-300 font-semibold font-mono privacy-mask">₺${Number(c.amount).toLocaleString('tr-TR')} <span class="text-slate-400">(%${c.pct})</span></span>
          </div>
        `).join('');
      listEl.querySelectorAll('.cf-cat-row').forEach(row => {
        row.addEventListener('mouseenter', () => {
          const idx = parseInt(row.dataset.catIdx, 10);
          if (categoryDonutInstance && Number.isFinite(idx)) {
            categoryDonutInstance.setActiveElements([{ datasetIndex: 0, index: idx }]);
            categoryDonutInstance.$hoveredIndex = idx;
            categoryDonutInstance.update('none');
          }
        });
        row.addEventListener('mouseleave', () => {
          if (categoryDonutInstance) {
            categoryDonutInstance.setActiveElements([]);
            categoryDonutInstance.$hoveredIndex = null;
            categoryDonutInstance.update('none');
          }
        });
      });
    }
    const canvas = document.getElementById('categoryDonutChart');
    if (!canvas) return;
    const dLabels = cats.length ? cats.map(c => c.category) : ['Harcama Yok'];
    const dVals = cats.length ? cats.map(c => c.amount) : [1];
    if (categoryDonutInstance) {
      categoryDonutInstance.$cfCats = cats;
      categoryDonutInstance.$cfTotalOut = totalOutflow;
      categoryDonutInstance.$cfTotalIn = totalInflow;
      categoryDonutInstance.data.labels = dLabels;
      categoryDonutInstance.data.datasets[0].data = dVals;
      categoryDonutInstance.data.datasets[0].backgroundColor = dColors;
      categoryDonutInstance.update('none');
    } else {
      const donutCenterPlugin = {
        id: 'donutCenterPlugin',
        afterDraw(chart) {
          const meta = chart.getDatasetMeta(0);
          if (!meta || !meta.data || !meta.data.length) return;
          const { x, y } = meta.data[0];
          const c = chart.ctx;
          const cfCats = chart.$cfCats || [];
          const activeEls = chart.getActiveElements ? chart.getActiveElements() : [];
          const hIdx = activeEls.length ? activeEls[0].index : chart.$hoveredIndex;
          c.save();
          c.textAlign = 'center';
          c.textBaseline = 'middle';
          if (hIdx !== null && hIdx !== undefined && cfCats[hIdx]) {
            const it = cfCats[hIdx];
            c.fillStyle = '#94a3b8';
            c.font = '600 10px Inter, sans-serif';
            c.fillText(String(it.category || '').slice(0, 14), x, y - 10);
            c.fillStyle = '#f8fafc';
            c.font = '700 13px JetBrains Mono, monospace';
            c.fillText('₺' + Math.round(Number(it.amount || 0)).toLocaleString('tr-TR'), x, y + 6);
            c.fillStyle = '#fb7185';
            c.font = '600 10px JetBrains Mono, monospace';
            c.fillText(`%${it.pct}`, x, y + 20);
          } else {
            c.fillStyle = '#94a3b8';
            c.font = '600 10px Inter, sans-serif';
            c.fillText('TOPLAM ÇIKIŞ', x, y - 9);
            c.fillStyle = '#f8fafc';
            c.font = '700 14px JetBrains Mono, monospace';
            c.fillText('₺' + Math.round(Number(chart.$cfTotalOut || 0)).toLocaleString('tr-TR'), x, y + 9);
          }
          c.restore();
        }
      };

      categoryDonutInstance = new Chart(canvas.getContext('2d'), {
        type: 'doughnut',
        plugins: [donutCenterPlugin],
        data: { labels: dLabels, datasets: [{ data: dVals, backgroundColor: dColors, borderWidth: 0, hoverOffset: 8 }] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '68%',
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: 'rgba(8, 12, 24, 0.97)',
              titleColor: '#f8fafc',
              bodyColor: '#e2e8f0',
              borderColor: 'rgba(244, 63, 94, 0.4)',
              borderWidth: 1,
              padding: 12,
              cornerRadius: 12,
              titleFont: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 13, weight: '700' },
              bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
              callbacks: {
                label: function(ctx) {
                  const cfCats = categoryDonutInstance?.$cfCats || [];
                  const item = cfCats[ctx.dataIndex];
                  if (!item) return ' Harcama kaydı yok';
                  const totIn = Number(categoryDonutInstance?.$cfTotalIn || 0);
                  const incPct = totIn > 0 ? ((Number(item.amount) / totIn) * 100).toFixed(1) : '0.0';
                  return [
                    ` Tutar: ₺${Number(item.amount).toLocaleString('tr-TR', {minimumFractionDigits: 2})}`,
                    ` Gider Payı: %${item.pct} • Gelire Oran: %${incPct}`
                  ];
                }
              }
            }
          }
        }
      });
      categoryDonutInstance.$cfCats = cats;
      categoryDonutInstance.$cfTotalOut = totalOutflow;
      categoryDonutInstance.$cfTotalIn = totalInflow;
    }
  } catch (e) { console.error('Cashflow error:', e); }
}

async function fetchRecurringRules() {
  const box = document.getElementById('recurring-rules-list');
  if (!box) return;
  try {
    const res = await fetch('/api/recurring').then(r => r.json());
    const rules = res.rules || [];
    if (rules.length === 0) {
      box.innerHTML = '<div class="text-xs text-slate-400 py-4 text-center">Henüz sabit akış kuralı tanımlanmadı (örn. Ayın 6\'sı KYK).</div>';
      return;
    }
    box.innerHTML = rules.map(r => `
      <div class="p-3 rounded-xl bg-[#070c1e] border border-white/[0.08] flex items-center justify-between gap-3">
        <div class="min-w-0">
          <div class="text-sm text-white font-bold truncate">${escapeHtml(r.title)} <span class="text-xs font-semibold ${r.type === 'GİRİŞ' ? 'text-emerald-400' : 'text-rose-400'}">(${escapeHtml(r.type)})</span></div>
          <div class="text-xs text-slate-300 mt-0.5 privacy-mask">Her ayın ${r.day_of_month}. günü • ₺${Number(r.amount_try).toLocaleString('tr-TR')} • Hesap ${escapeHtml(r.account_id)}</div>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <button data-rule-id="${escapeHtml(r.id)}" onclick="executeRecurringRule(this.dataset.ruleId)" class="px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 text-xs font-bold transition">İşle</button>
          <button data-rule-id="${escapeHtml(r.id)}" onclick="deleteRecurringRule(this.dataset.ruleId)" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 text-xs transition" title="Sil"><i class="fa-solid fa-trash-can"></i></button>
        </div>
      </div>
    `).join('');
  } catch (e) { console.error('Recurring rules error:', e); }
}

async function saveRecurringRule(e) {
  e.preventDefault();
  const title = document.getElementById('rec-title').value.trim();
  const type = document.getElementById('rec-type').value;
  const amount_try = parseFloat(document.getElementById('rec-amount').value) || 0;
  const day_of_month = parseInt(document.getElementById('rec-day').value, 10) || 1;
  await fetch('/api/recurring', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ title, type, category: title, amount_try, account_id: '5001', day_of_month })
  });
  document.getElementById('rec-title').value = '';
  document.getElementById('rec-amount').value = '';
  fetchRecurringRules();
  showToast('Sabit akış kuralı kaydedildi! Telegram hatırlatıcısı aktif.');
}

async function executeRecurringRule(id) {
  const res = await fetch(`/api/recurring/${encodeURIComponent(id)}/execute`, { method: 'POST' }).then(r => r.json());
  showToast(res.message || 'Sabit akış deftere işlendi!');
  refreshAllData();
}

async function deleteRecurringRule(id) {
  await fetch(`/api/recurring/${encodeURIComponent(id)}`, { method: 'DELETE' });
  fetchRecurringRules();
  showToast('Sabit akış kuralı silindi.');
}


function renderHelpersSummary(data) {
  if (!data) return;
  const vc = data.valor_clock || {};
  startValorCountdown(vc.seconds_to_cutoff, vc.cutoff_state);

  const schedGrid = document.getElementById('valor-schedules-grid');
  if (schedGrid && Array.isArray(vc.schedules)) {
    schedGrid.innerHTML = vc.schedules.map(sc => {
      const isInstant = sc.t_badge === 'T+0';
      const badgeCls = isInstant
        ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
        : 'bg-amber-500/15 text-amber-300 border-amber-500/30';
      return `
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.08] space-y-1.5">
          <div class="flex items-center justify-between gap-2">
            <span class="font-bold text-white font-mono text-sm">${escapeHtml(sc.code)} <span class="text-xs font-normal text-slate-400">(${escapeHtml(sc.cutoff)})</span></span>
            <span class="px-2.5 py-0.5 rounded-md border text-xs font-mono font-bold ${badgeCls}">${escapeHtml(sc.t_badge)} · ${escapeHtml(sc.settle_label)}</span>
          </div>
          <div class="text-xs text-slate-300 leading-relaxed">${escapeHtml(sc.nema_note)}</div>
        </div>
      `;
    }).join('');
  }

  const mb = data.monthly_budget || {};
  const fmtTry = (v) => '₺' + Number(v || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 });
  const bSub = document.getElementById('budget-month-subtitle');
  if (bSub) {
    bSub.innerText = mb.has_recorded_income
      ? `${mb.month_label || 'Bu Ay'} • Ayın ${mb.day_of_month}. günü (${mb.days_left} gün kaldı)`
      : `${mb.month_label || 'Bu Ay'} • Giriş girilmediği için ₺15.000 (11K+4K) referans alındı`;
  }
  if (document.getElementById('budget-income-val')) document.getElementById('budget-income-val').innerText = fmtTry(mb.income_try);
  if (document.getElementById('budget-expense-val')) document.getElementById('budget-expense-val').innerText = fmtTry(mb.expense_try);
  if (document.getElementById('budget-invest-val')) document.getElementById('budget-invest-val').innerText = fmtTry(mb.net_invested_try);

  const rateBadge = document.getElementById('budget-rate-badge');
  const ratePct = Number(mb.savings_rate_pct || 0);
  if (rateBadge) {
    rateBadge.innerText = `Tasarruf: %${ratePct.toFixed(1)}`;
    rateBadge.className = ratePct >= 35
      ? 'px-3 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-mono font-bold'
      : 'px-3 py-1 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 text-xs font-mono font-bold';
  }
  if (document.getElementById('budget-target-progress')) {
    document.getElementById('budget-target-progress').innerText = `${fmtTry(mb.net_invested_try)} / ${fmtTry(mb.target_invest_try)}`;
  }
  const sBar = document.getElementById('budget-savings-bar');
  if (sBar) {
    const barPct = mb.target_invest_try > 0 ? Math.min(100, (mb.net_invested_try / mb.target_invest_try) * 100) : 0;
    sBar.style.width = `${barPct.toFixed(1)}%`;
  }
  if (document.getElementById('budget-remaining-val')) document.getElementById('budget-remaining-val').innerText = fmtTry(mb.remaining_free_try);
  if (document.getElementById('budget-daily-allowance')) document.getElementById('budget-daily-allowance').innerText = `Günlük hak: ${fmtTry(mb.daily_allowance_try)}/gün`;

  const catBox = document.getElementById('budget-top-cats');
  if (catBox) {
    const cats = mb.top_categories || [];
    catBox.innerHTML = cats.length
      ? cats.map(c => `<span class="px-3 py-1 rounded-lg bg-[#070c1e] border border-white/[0.08] text-slate-200 text-xs">${escapeHtml(c.category)}: <b class="text-rose-300">${fmtTry(c.amount_try)}</b></span>`).join('')
      : '<span class="text-slate-400 text-xs">Bu ay henüz harcama (ÇIKIŞ) kaydı eklenmedi.</span>';
  }

  const kl = data.kyk_leverage || {};
  const kCfg = kl.config || {};
  const coverPct = Number(kl.nema_installment_cover_pct || 0);
  if (document.getElementById('kyk-cover-badge')) document.getElementById('kyk-cover-badge').innerText = `Taksit Gücü: %${coverPct.toFixed(1)}`;
  if (document.getElementById('kyk-received-val')) document.getElementById('kyk-received-val').innerText = fmtTry(kl.total_kyk_received_try);
  if (document.getElementById('kyk-recorded-sub')) document.getElementById('kyk-recorded-sub').innerText = `Defterde: ${fmtTry(kl.recorded_kyk_try)} (${kl.recorded_kyk_count || 0} kayıt)`;
  if (document.getElementById('kyk-nema-val')) document.getElementById('kyk-nema-val').innerText = `${fmtTry(kl.monthly_ppf_nema_try)}/ay`;
  if (document.getElementById('kyk-debt-ratio')) {
    document.getElementById('kyk-debt-ratio').innerText = kl.total_kyk_received_try > 0 ? `%${Number(kl.debt_covered_pct || 0).toFixed(0)}` : 'Borç Yok';
  }
  if (document.getElementById('kyk-target-ppf-sub')) {
    document.getElementById('kyk-target-ppf-sub').innerText = `Bedava Taksit PPF: ${fmtTry(kl.target_ppf_for_free_kyk_try)}`;
  }
  if (document.getElementById('kyk-bar-pct')) document.getElementById('kyk-bar-pct').innerText = `%${coverPct.toFixed(1)}`;
  const kBar = document.getElementById('kyk-leverage-bar');
  if (kBar) kBar.style.width = `${Math.min(100, coverPct).toFixed(1)}%`;
  const pIn = document.getElementById('kyk-prior-input');
  const mIn = document.getElementById('kyk-monthly-input');
  if (pIn && document.activeElement !== pIn) pIn.value = Number(kCfg.prior_kyk_try || 0);
  if (mIn && document.activeElement !== mIn) mIn.value = Number(kCfg.monthly_kyk_try || 4000);
}

async function fetchHelpersSummary() {
  try {
    const res = await fetch('/api/helpers/summary');
    if (!res.ok) return;
    const data = await res.json();
    renderHelpersSummary(data);
  } catch (e) {
    console.error('Helpers summary error:', e);
  }
}

async function saveKykHelperConfig(e) {
  e.preventDefault();
  const priorVal = parseFloat(document.getElementById('kyk-prior-input')?.value || '0') || 0;
  const monthlyVal = parseFloat(document.getElementById('kyk-monthly-input')?.value || '4000') || 4000;
  try {
    const res = await fetch('/api/helpers/kyk-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prior_kyk_try: priorVal, monthly_kyk_try: monthlyVal })
    });
    if (!res.ok) throw new Error('KYK ayarı kaydedilemedi.');
    await fetchHelpersSummary();
    showToast('KYK kaldıraç takibi güncellendi.');
  } catch (err) {
    showToast(err.message || 'Hata oluştu.', true);
  }
}

// Sağ Dock Kayan Liquid Pill Gösterge

function updateTxSelectedAccBalance() {
  const balEl = document.getElementById('tx-selected-acc-bal');
  const accSel = document.getElementById('tx-account');
  if (!balEl || !accSel) return;
  const acc = (globalAccounts || []).find(a => String(a.id) === String(accSel.value));
  if (acc) {
    balEl.innerText = `Bakiye: ₺${Number(acc.balance || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  } else {
    balEl.innerText = '';
  }
}

function bumpTxAmount(delta) {
  const inp = document.getElementById('tx-amount');
  if (!inp) return;
  const cur = parseTrNumber(inp.value);
  inp.value = Math.round((cur + delta) * 100) / 100;
  updateTxLivePreview();
}

function fillMaxTxBalance() {
  const accSel = document.getElementById('tx-account');
  const inp = document.getElementById('tx-amount');
  if (!accSel || !inp) return;
  const acc = (globalAccounts || []).find(a => String(a.id) === String(accSel.value));
  if (!acc) return;
  inp.value = Math.max(0, Number(acc.balance || 0)).toFixed(2);
  updateTxLivePreview();
}

function swapTxDirection() {
  const sel = document.getElementById('tx-type');
  if (!sel) return;
  if (sel.value === 'ALIM') sel.value = 'SATIM';
  else if (sel.value === 'SATIM') sel.value = 'ALIM';
  adjustTxFields();
}

function swapVirmanAccounts() {
  const fromEl = document.getElementById('tx-account');
  const toEl = document.getElementById('tx-to-account');
  if (!fromEl || !toEl) return;
  const tmp = fromEl.value;
  fromEl.value = toEl.value;
  toEl.value = tmp;
  updateTxSelectedAccBalance();
}

function quickTransferFromAccount(accId) {
  openTxModal();
  const t = document.getElementById('tx-type');
  if (t) t.value = 'VIRMAN';
  adjustTxFields();
  const fromEl = document.getElementById('tx-account');
  const toEl = document.getElementById('tx-to-account');
  if (fromEl) fromEl.value = accId;
  if (toEl && toEl.value === accId && toEl.options.length > 1) {
    const other = Array.from(toEl.options).find(o => o.value !== accId);
    if (other) toEl.value = other.value;
  }
  updateTxSelectedAccBalance();
}

function quickTxForAccount(accId) {
  openTxModal();
  const t = document.getElementById('tx-type');
  if (t) t.value = 'ÇIKIŞ';
  adjustTxFields();
  const fromEl = document.getElementById('tx-account');
  if (fromEl) fromEl.value = accId;
  updateTxSelectedAccBalance();
}

function copyFilteredLedgerSummary() {
  const list = window.__lastFilteredTransactions || globalTransactions || [];
  if (!list.length) {
    showToast('Kopyalanacak işlem kaydı bulunamadı.', 'warn');
    return;
  }
  const lines = [
    `Finans Terminal — İşlem Defteri Özeti (${list.length} kayıt)`,
    ...list.slice(0, 40).map(t => `- ${t.date} | ${t.type} | ${t.category || t.code} | ₺${Number(t.amount_try || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2 })} | ${t.description || ''}`)
  ];
  navigator.clipboard.writeText(lines.join('\n')).then(() => {
    showToast(`${Math.min(40, list.length)} kayıt panoya kopyalandı!`);
  }).catch(() => showToast('Kopyalama başarısız.', 'warn'));
}


function runRealReturnCalc() {
  const code = document.getElementById('real-fund-select')?.value || 'ZBJ';
  const infl = parseFloat(document.getElementById('real-infl-input')?.value) || 2.8;
  const fMap = globalMarket?.funds || {};
  const sel = document.getElementById('real-fund-select');
  if (sel && Object.keys(fMap).length) {
    const cur = sel.value;
    const existing = Array.from(sel.options).map(o => o.value);
    getCatalogFundCodes(fMap, globalPortfolio?.funds || []).forEach(c => {
      if (!existing.includes(c)) {
        const o = document.createElement('option');
        o.value = c;
        o.text = `${c} · ${fMap[c]?.name || 'TEFAS Fonu'}`;
        sel.appendChild(o);
      }
    });
    if (cur) sel.value = cur;
  }
  const nom = Number(fMap[code]?.monthly_yield_pct || 0);
  const usdChg = Number(globalMarket?.macro?.usd_chg_pct || 0);
  const realInfl = ((1 + nom / 100) / (1 + infl / 100) - 1) * 100;
  const realUsd = nom - usdChg;
  const nomEl = document.getElementById('real-nom-val');
  const infEl = document.getElementById('real-infl-diff');
  const usdEl = document.getElementById('real-usd-diff');
  const vEl = document.getElementById('real-return-verdict');
  if (nomEl) nomEl.innerText = `${nom >= 0 ? '+' : ''}%${nom.toFixed(2)}`;
  if (infEl) {
    infEl.className = `text-base font-bold mt-1 ${realInfl >= 0 ? 'text-emerald-300' : 'text-rose-300'}`;
    infEl.innerText = `${realInfl >= 0 ? '+' : ''}%${realInfl.toFixed(2)}`;
  }
  if (usdEl) {
    usdEl.className = `text-base font-bold mt-1 ${realUsd >= 0 ? 'text-indigo-300' : 'text-rose-300'}`;
    usdEl.innerText = `${realUsd >= 0 ? '+' : ''}%${realUsd.toFixed(2)}`;
  }
  if (vEl) {
    vEl.innerText = realInfl >= 0
      ? `${code} son 30 günde aylık %${infl.toFixed(1)} enflasyon varsayımının +${realInfl.toFixed(2)} puan üzerinde reel getiri sağlamış.`
      : `${code} son 30 günde %${infl.toFixed(1)} aylık enflasyon eşiğinin ${Math.abs(realInfl).toFixed(2)} puan altında kalmış; uzun vadeli sepet dengesi önerilir.`;
  }
}


function applyRebalancePreset(ppf, gold, tech, label) {
  const pEl = document.getElementById('rebalance-pct-ppf');
  const gEl = document.getElementById('rebalance-pct-gold');
  const tEl = document.getElementById('rebalance-pct-tech');
  if (pEl) pEl.value = ppf;
  if (gEl) gEl.value = gold;
  if (tEl) tEl.value = tech;
  runPortfolioRebalance();
  if (typeof showToast === 'function' && label) {
    showToast(`${label} (${ppf}/${gold}/${tech}) hedef dağılımı uygulandı.`);
  }
}

function setRebalanceCash(val) {
  const inp = document.getElementById('rebalance-cash-input');
  if (!inp) return;
  if (val === 'free') {
    const freeCash = Number(globalPortfolio?.cash_try || 0);
    inp.value = Math.max(0, Math.round(freeCash));
  } else {
    inp.value = Math.max(0, Math.round(Number(val) || 0));
  }
  runPortfolioRebalance();
}

function runPortfolioRebalance() {
  const grid = document.getElementById('rebalance-cards-grid');
  const sumEl = document.getElementById('rebalance-summary-bar');
  const driftBadge = document.getElementById('rebalance-drift-badge');
  const sumBadge = document.getElementById('rebalance-sum-badge');
  if (!grid) return;

  const rawPpf = Math.max(0, parseFloat(document.getElementById('rebalance-pct-ppf')?.value) || 0);
  const rawGold = Math.max(0, parseFloat(document.getElementById('rebalance-pct-gold')?.value) || 0);
  const rawTech = Math.max(0, parseFloat(document.getElementById('rebalance-pct-tech')?.value) || 0);
  const pctSum = rawPpf + rawGold + rawTech;

  if (sumBadge) {
    const ok = Math.abs(pctSum - 100) < 0.01;
    sumBadge.className = `text-xs px-2.5 py-0.5 rounded-full border font-mono font-bold ${ok ? 'bg-emerald-500/15 border-emerald-400/30 text-emerald-200' : 'bg-rose-500/15 border-rose-400/30 text-rose-200'}`;
    sumBadge.innerText = ok ? 'Hedef: %100 (Uyumlu)' : `Toplam: %${pctSum.toFixed(0)} (!=100)`;
  }

  const normSum = pctSum > 0 ? pctSum : 100;
  const newCash = Math.max(0, parseFloat(document.getElementById('rebalance-cash-input')?.value) || 0);

  const holdings = (globalPortfolio && globalPortfolio.funds) || [];
  const fMap = (globalMarket && globalMarket.funds) || {};
  const valOf = (codes) => holdings
    .filter(h => codes.includes(String(h.code || '').toUpperCase()))
    .reduce((acc, h) => acc + Number(h.current_value || h.current_value_try || 0), 0);

  const sleeves = [
    {
      id: 'ppf',
      code: 'ZBJ',
      title: 'Para Piyasası (ZBJ / TZL)',
      valor: 'T+0 Aynı Gün',
      targetPct: (pctSum > 0 ? rawPpf : 35) / normSum,
      currentVal: valOf(['ZBJ', 'TZL']),
      unitPrice: Number(fMap.ZBJ?.price || fMap.TZL?.price || 0),
      accent: 'emerald'
    },
    {
      id: 'gold',
      code: 'TCA',
      title: 'Altın Fonu (TCA)',
      valor: 'T+1 · %0 Stopaj*',
      targetPct: (pctSum > 0 ? rawGold : 35) / normSum,
      currentVal: valOf(['TCA']),
      unitPrice: Number(fMap.TCA?.price || 0),
      accent: 'amber'
    },
    {
      id: 'tech',
      code: 'AFT',
      title: 'Teknoloji (AFT)',
      valor: 'T+1 Alış / T+2 Satış',
      targetPct: (pctSum > 0 ? rawTech : 30) / normSum,
      currentVal: valOf(['AFT']),
      unitPrice: Number(fMap.AFT?.price || 0),
      accent: 'sky'
    }
  ];

  const curFundTotal = sleeves.reduce((s, x) => s + x.currentVal, 0);
  const postTotal = curFundTotal + newCash;

  // Water-filling / eksik ağırlık öncelikli dağıtım (sadece alım yaparak sapmayı en aza indirir)
  const deficits = sleeves.map(s => Math.max(0, (postTotal * s.targetPct) - s.currentVal));
  const defTotal = deficits.reduce((a, b) => a + b, 0);

  let totalAllocated = 0;
  let totalLeftoverKurus = 0;
  let maxDrift = 0;

  const fmtTry = (v) => '₺' + Number(v || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const toneMap = {
    emerald: { border: 'border-emerald-500/30', badge: 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30', bar: 'from-emerald-500 to-teal-400', btn: 'bg-emerald-600 hover:bg-emerald-500' },
    amber: { border: 'border-amber-500/30', badge: 'bg-amber-500/15 text-amber-300 border-amber-400/30', bar: 'from-amber-500 to-yellow-400', btn: 'bg-amber-600 hover:bg-amber-500' },
    sky: { border: 'border-sky-500/30', badge: 'bg-sky-500/15 text-sky-300 border-sky-400/30', bar: 'from-sky-500 to-indigo-400', btn: 'bg-sky-600 hover:bg-sky-500' }
  };

  const cardsHtml = sleeves.map((s, idx) => {
    const curPct = curFundTotal > 0 ? (s.currentVal / curFundTotal) * 100 : 0;
    const tgtPct = s.targetPct * 100;
    const drift = Math.abs(curPct - tgtPct);
    if (drift > maxDrift) maxDrift = drift;

    let allocAmt = 0;
    if (newCash > 0) {
      allocAmt = defTotal > 0 ? newCash * (deficits[idx] / defTotal) : newCash * s.targetPct;
    }
    const rawDeltaNoCash = (curFundTotal * s.targetPct) - s.currentVal;
    const postVal = s.currentVal + allocAmt;
    const postPct = postTotal > 0 ? (postVal / postTotal) * 100 : 0;

    const lotStep = getFundLotStep(s.code);
    const rawUnits = (s.unitPrice > 0 && allocAmt > 0) ? Math.floor((allocAmt + 1e-9) / s.unitPrice) : 0;
    const exactShares = Math.floor(rawUnits / lotStep) * lotStep;
    const exactSpend = exactShares > 0 ? Math.round(exactShares * s.unitPrice * 100) / 100 : 0;
    const leftover = Math.max(0, allocAmt - exactSpend);
    totalAllocated += allocAmt;
    totalLeftoverKurus += leftover;

    const tone = toneMap[s.accent];
    const statusText = newCash > 0
      ? (exactShares > 0 ? `+${fmtTry(exactSpend)} Ekle` : (allocAmt >= 1 ? `1 Lot Altı (${lotStep} pay)` : 'Hedef Üstü (Bekle)'))
      : (rawDeltaNoCash >= 50 ? `+${fmtTry(rawDeltaNoCash)} Eksik` : (rawDeltaNoCash <= -50 ? `${fmtTry(Math.abs(rawDeltaNoCash))} Fazla` : 'Dengede'));

    const sharesNote = (s.unitPrice > 0 && exactShares > 0)
      ? `${exactShares.toLocaleString('tr-TR')} Pay (${lotStep}'li) · Birim ₺${s.unitPrice.toFixed(4)} · Kalan ₺${leftover.toFixed(2)}`
      : (s.unitPrice > 0 ? `1 Lot (${lotStep} pay) ≈ ₺${(lotStep * s.unitPrice).toFixed(2)}` : 'Canlı fiyat bekleniyor');

    return `
      <div class="p-4 rounded-2xl bg-[#070c1e]/95 border ${tone.border} flex flex-col justify-between gap-3.5">
        <div class="flex items-start justify-between gap-2">
          <div>
            <div class="text-sm font-extrabold text-white">${escapeHtml(s.title)}</div>
            <div class="text-xs text-slate-400 mt-0.5">${escapeHtml(s.valor)}</div>
          </div>
          <span class="text-xs px-2.5 py-0.5 rounded-full border font-mono font-bold ${tone.badge}">${escapeHtml(statusText)}</span>
        </div>

        <div class="space-y-1.5 text-xs">
          <div class="flex justify-between font-mono text-slate-300">
            <span>Mevcut: <b class="text-white privacy-mask">${fmtTry(s.currentVal)}</b> (%${curPct.toFixed(1)})</span>
            <span>Hedef: <b class="text-white">%${tgtPct.toFixed(0)}</b></span>
          </div>
          <div class="h-2.5 w-full bg-slate-900 rounded-full overflow-hidden border border-white/[0.08] relative">
            <div class="h-full bg-gradient-to-r ${tone.bar} rounded-full transition-all duration-300" style="width: ${Math.min(100, postPct).toFixed(1)}%"></div>
          </div>
          <div class="flex justify-between font-mono text-slate-400">
            <span>İşlem Sonrası: <b class="text-slate-200 privacy-mask">${fmtTry(postVal)}</b></span>
            <span class="text-slate-200 font-bold">%${postPct.toFixed(1)}</span>
          </div>
        </div>

        <div class="pt-2.5 border-t border-white/[0.07] flex items-center justify-between gap-2">
          <div class="text-xs font-mono text-slate-300 leading-snug">${escapeHtml(sharesNote)}</div>
          ${allocAmt >= 1 ? `<button type="button" onclick="quickBuyFromRadar('${s.code}', ${Math.round(allocAmt)})" class="h-8 px-3 rounded-xl ${tone.btn} text-white text-xs font-bold whitespace-nowrap transition active:scale-95 shrink-0">Emre Aktar →</button>` : ''}
        </div>
      </div>
    `;
  }).join('');

  grid.innerHTML = cardsHtml;

  if (driftBadge) {
    const cls = maxDrift <= 5
      ? 'bg-emerald-500/15 border-emerald-400/30 text-emerald-200'
      : (maxDrift <= 12 ? 'bg-amber-500/15 border-amber-400/30 text-amber-200' : 'bg-rose-500/15 border-rose-400/30 text-rose-200');
    driftBadge.className = `text-xs px-2.5 py-0.5 rounded-full border font-mono font-bold ${cls}`;
    driftBadge.innerText = `Sapma: %${maxDrift.toFixed(1)} ${maxDrift <= 5 ? '(Dengeli)' : ''}`;
  }

  if (sumEl) {
    sumEl.innerHTML = `
      <span>Mevcut Fon Sepeti: <strong class="text-white">${fmtTry(curFundTotal)}</strong> · Yeni Eklenen: <strong class="text-teal-300">${fmtTry(totalAllocated)}</strong> · İşlem Sonrası Toplam: <strong class="text-emerald-300">${fmtTry(postTotal)}</strong></span>
      <span class="text-slate-400">Tam pay yuvarlama sonrası tahmini nakit artığı: <strong class="text-indigo-200">${fmtTry(totalLeftoverKurus)}</strong></span>
    `;
  }
}

// Hafif Çalışma Tercihleri (Arka planda çalışan yardımcı varsayılanlar)
