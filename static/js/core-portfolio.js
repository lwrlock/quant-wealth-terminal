function getGoalTargetTry() {
  const custom = Number(window.appPrefs && window.appPrefs.goalTargetTry);
  return Number.isFinite(custom) && custom >= 10000 ? custom : GRADUATION_TARGET_TRY;
}

function computeCompoundGoalEtaMonths(currentTotal, targetTotal, monthlyContribution, annualYieldPct) {
  const cur = Math.max(0, Number(currentTotal || 0));
  const tgt = Math.max(1000, Number(targetTotal || getGoalTargetTry()));
  if (cur >= tgt) return 0;
  const contrib = Math.max(100, Number(monthlyContribution || 5500));
  const ann = Math.min(200, Math.max(0, Number(annualYieldPct !== undefined ? annualYieldPct : 35)));
  const monthlyRate = Math.pow(1 + ann / 100, 1 / 12) - 1;
  let bal = cur;
  let months = 0;
  while (bal < tgt && months < 360) {
    bal = bal * (1 + monthlyRate) + contrib;
    months++;
  }
  return months;
}

function updateGoalEtaBadge(overrideMonthly) {
  const goalEtaEl = document.getElementById('goal-eta-badge');
  const tgtPill = document.getElementById('goal-target-pill');
  const tgt = getGoalTargetTry();
  if (tgtPill) {
    tgtPill.innerText = '₺' + Math.round(tgt).toLocaleString('tr-TR');
  }
  if (!goalEtaEl) return;
  const total = Number(globalPortfolio?.total_value_try || 0);
  const sliderEl = document.getElementById('slider-deposit');
  const monthlyContrib = Number(
    overrideMonthly !== undefined
      ? overrideMonthly
      : (sliderEl ? sliderEl.value : (window.appPrefs?.defaultMonthlyDeposit || 5500))
  ) || 5500;
  const annYield = Number(window.appPrefs?.goalAnnualYieldPct ?? 35);
  const estMonths = computeCompoundGoalEtaMonths(total, tgt, monthlyContrib, annYield);
  if (estMonths <= 0) {
    goalEtaEl.innerText = 'Hedef Tamam!';
    return;
  }
  const mode = (window.appPrefs && window.appPrefs.etaDisplayMode) || 'compound';
  const targetDate = new Date();
  targetDate.setMonth(targetDate.getMonth() + estMonths);
  const dateShort = targetDate.toLocaleDateString('tr-TR', { month: 'short', year: 'numeric' });
  if (mode === 'date') {
    goalEtaEl.innerText = dateShort;
  } else if (mode === 'both') {
    goalEtaEl.innerText = `~${estMonths} Ay · ${dateShort}`;
  } else {
    goalEtaEl.innerText = `~${estMonths} Ay Kaldı`;
  }
  goalEtaEl.title = `Bileşik Yıllık %${annYield} getiri + Aylık ₺${monthlyContrib.toLocaleString('tr-TR')} katkı varsayımıyla (${dateShort})`;
}


function toggleSidebarPin(forceState) {
  const sb = document.getElementById('sidebar-right');
  if (!sb) return;
  const shouldPin = typeof forceState === 'boolean' ? forceState : !sb.classList.contains('pinned');
  sb.classList.toggle('pinned', shouldPin);
  localStorage.setItem('fin_dock_pinned', shouldPin ? '1' : '0');
  const pinBtn = document.getElementById('btn-pin-sidebar');
  const pinTxt = document.getElementById('text-pin');
  if (pinBtn) {
    pinBtn.classList.toggle('text-indigo-400', shouldPin);
    pinBtn.classList.toggle('border-indigo-500/40', shouldPin);
  }
  if (pinTxt) pinTxt.innerText = shouldPin ? 'Menü Sabitlendi' : 'Menüyü Sabitle';
}


function animateViewEntrance(targetView) {
  if (!targetView) return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (typeof gsap === 'undefined') return;
  const items = targetView.querySelectorAll(':scope > .cosmic-card, :scope > .grid > div > .cosmic-card, :scope > .space-y-6 > .cosmic-card');
  if (!items.length) return;
  gsap.killTweensOf(items);
  gsap.fromTo(
    items,
    { y: 10, opacity: 0 },
    { y: 0, opacity: 1, duration: 0.30, stagger: 0.032, ease: 'power3.out', clearProps: 'transform,opacity' }
  );
}

const __tabLastFetchAt = {};
function switchTab(viewId, clickedBtn) {
  if (viewId === 'goals') viewId = 'terminal';
  if (viewId === 'splitter') viewId = 'tools';
  const targetView = document.getElementById('view-' + viewId);
  if (!targetView) return;
  const alreadyActive = targetView.classList.contains('active');

  document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.sidebar-btn').forEach(b => {
    b.classList.remove('active');
    b.removeAttribute('aria-current');
  });
  document.querySelectorAll('.mobile-nav-btn').forEach(b => {
    b.classList.remove('active', 'text-emerald-300', 'bg-emerald-500/15', 'text-indigo-400', 'bg-indigo-500/15');
    b.classList.add('text-slate-300');
    b.removeAttribute('aria-current');
  });

  targetView.classList.add('active');
  let activeDockBtn = null;
  if (clickedBtn && clickedBtn.classList.contains('sidebar-btn')) {
    clickedBtn.classList.add('active');
    clickedBtn.setAttribute('aria-current', 'page');
    activeDockBtn = clickedBtn;
  } else {
    const dockIdx = ['terminal', 'ledger', 'radar', 'tools', 'council'].indexOf(viewId);
    const dockBtns = document.querySelectorAll('#sidebar-right nav .sidebar-btn');
    if (dockIdx >= 0 && dockBtns[dockIdx]) {
      dockBtns[dockIdx].classList.add('active');
      dockBtns[dockIdx].setAttribute('aria-current', 'page');
      activeDockBtn = dockBtns[dockIdx];
    }
  }

  const mobBtn = document.querySelector(`.mobile-nav-btn[data-tab="${viewId}"]`);
  if (mobBtn) {
    mobBtn.classList.remove('text-slate-300');
    mobBtn.classList.add('active', 'text-indigo-400', 'bg-indigo-500/15');
    mobBtn.setAttribute('aria-current', 'page');
  }

  const titleMap = {
    'terminal': 'Portföy Özeti & Hedef',
    'ledger': 'İşlem ve Kasa Defteri',
    'radar': 'TEFAS Fon Radarı',
    'tools': 'Araçlar, Plan & Stopaj',
    'council': 'AI Konsey & Canlı Haberler',
    'telegram': 'Telegram Asistanı'
  };
  const iconMap = {
    'terminal': 'fa-solid fa-chart-line text-sm',
    'ledger': 'fa-solid fa-book-bookmark text-sm text-cyan-300',
    'radar': 'fa-solid fa-satellite-dish text-sm text-purple-300',
    'tools': 'fa-solid fa-wand-magic-sparkles text-sm text-teal-300',
    'council': 'fa-solid fa-brain text-sm text-indigo-300',
    'telegram': 'fa-brands fa-telegram text-sm text-sky-300'
  };
  const worldNameMap = {
    'terminal': 'Akıllı Finansal Komuta',
    'ledger': 'Kayıt & Muhasebe Defteri',
    'radar': 'TEFAS Fon Analizi',
    'tools': 'Yatırım Araçları & Vergi',
    'council': 'Yapay Zekâ Konseyi',
    'telegram': 'Bildirim & Otomasyon'
  };
  const topTitle = document.getElementById('top-title');
  if (topTitle) topTitle.innerText = titleMap[viewId] || 'Finans Paneli';
  const topIcon = document.getElementById('top-title-icon');
  if (topIcon && iconMap[viewId]) topIcon.className = iconMap[viewId];
  const worldNameEl = document.getElementById('active-world-name');
  if (worldNameEl && worldNameMap[viewId]) worldNameEl.innerText = worldNameMap[viewId];
  try {
    localStorage.setItem('fin_last_tab', viewId);
    if (window.location.hash !== '#' + viewId) {
      history.replaceState(null, '', '#' + viewId);
    }
  } catch (_) {}
  if (typeof updateBrowserTabTitle === 'function') updateBrowserTabTitle(viewId);

  if (typeof setSkyMood === 'function') setSkyMood(viewId);
  if (typeof syncDockLiquidPill === 'function') syncDockLiquidPill(activeDockBtn);

  if (!alreadyActive && window.scrollY > 24) {
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduced ? 'auto' : 'smooth' });
  }
  if (!alreadyActive) animateViewEntrance(targetView);

  // Veri isteklerini ilk boyama karesinin (frame) sonrasına ertele — 120fps menü geçişi için
  requestAnimationFrame(() => {
    const now = Date.now();
    const isStale = (key, ttlMs = 8000) => {
      if (!__tabLastFetchAt[key] || (now - __tabLastFetchAt[key]) > ttlMs) {
        __tabLastFetchAt[key] = now;
        return true;
      }
      return false;
    };
    if (viewId === 'terminal') {
      if (isStale('terminal', 12000)) {
        const nwDays = Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30;
        fetchNetWorthHistory(nwDays);
        fetchOpportunityRadar();
      }
      updateGoalsView();
    }
    if (viewId === 'ledger' && isStale('ledger', 6000)) {
      fetchTransactions();
      fetchCashflowAnalytics();
    }
    if (viewId === 'radar') {
      if (isStale('radar', 15000)) fetchOpportunityRadar();
      populateRadar();
    }
    if (viewId === 'tools') {
      if (isStale('tools', 8000)) {
        fetchHelpersSummary();
        fetchPriceAlerts();
      }
      runWeekendNemaCalc();
      calculateSplit();
      if (typeof runRealReturnCalc === 'function') runRealReturnCalc();
      if (typeof runPortfolioRebalance === 'function') runPortfolioRebalance();
    }
    if (viewId === 'council' && isStale('council', 15000)) {
      fetchLiveCouncilVerdict();
      fetchLiveNews();
    }
    if (viewId === 'telegram' && isStale('telegram', 10000)) {
      fetchTelegramStatus();
      fetchPersistenceStatus();
    }
  });
}


function updateGoalsView() {
  const total = globalPortfolio?.total_value_try || 0;
  const zbjVal = (globalPortfolio?.funds?.find(f => f.code === 'ZBJ')?.current_value) || 0;
  const tzlVal = (globalPortfolio?.funds?.find(f => f.code === 'TZL')?.current_value) || 0;
  const pendingPpf = (globalPortfolio?.pending_orders || []).reduce((acc, po) => {
    return (po.code === 'ZBJ' || po.code === 'TZL') ? acc + Number(po.amount_try || 0) : acc;
  }, 0);
  const ppfVal = zbjVal + tzlVal + pendingPpf;
  
  // 1. Acil Durum Kalkanı (30.000 TL)
  const emergTarget = 30000;
  const emergPct = Math.min(100, (ppfVal / emergTarget) * 100);
  const emergBar = document.getElementById('goal-emerg-bar');
  const emergText = document.getElementById('goal-emerg-text');
  if (emergBar) emergBar.style.width = Math.max(0.5, emergPct) + '%';
  if (emergText) emergText.innerText = `₺${ppfVal.toFixed(2)} / ₺${emergTarget.toLocaleString()} (%${emergPct.toFixed(1)})`;

  // 2. Mühendislik Donanımı / Workstation (55.000 TL)
  const devTarget = 55000;
  const devPct = Math.min(100, (total / devTarget) * 100);
  const devBar = document.getElementById('goal-dev-bar');
  const devText = document.getElementById('goal-dev-text');
  if (devBar) devBar.style.width = Math.max(0.5, devPct) + '%';
  if (devText) devText.innerText = `₺${total.toFixed(2)} / ₺${devTarget.toLocaleString()} (%${devPct.toFixed(1)})`;

  // 3. Mezuniyet Arabası / Master Portföy (650.000 TL)
  const gradTarget = 650000;
  const gradPct = Math.min(100, (total / gradTarget) * 100);
  const gradBar = document.getElementById('goal-grad-bar');
  const gradText = document.getElementById('goal-grad-text');
  if (gradBar) gradBar.style.width = Math.max(0.5, gradPct) + '%';
  if (gradText) gradText.innerText = `₺${total.toFixed(2)} / ₺${gradTarget.toLocaleString()} (%${gradPct.toFixed(2)})`;

  runWeekendNemaCalc();
}

function runWeekendNemaCalc() {
  const amt = parseFloat(document.getElementById('weekend-calc-amt')?.value) || 10000;
  const liveYieldPct = Number(globalMarket?.funds?.ZBJ?.daily_yield_pct || 0);
  const grossDaily = liveYieldPct > 0 ? (liveYieldPct / 100.0) : 0.0013;
  const rawRate = Number(globalPortfolio?.tax_summary?.rates?.ZBJ);
  const stopajPct = Number.isFinite(rawRate) ? rawRate : 15.0;
  const netDaily = grossDaily * (1 - (stopajPct / 100.0));
  const net3Day = amt * netDaily * 3;
  const gross3Day = amt * grossDaily * 3;
  const taxWithheld = gross3Day - net3Day;

  const resEl = document.getElementById('weekend-calc-res');
  const subEl = document.getElementById('weekend-calc-sub');
  if (resEl) resEl.innerText = `₺${net3Day.toFixed(2)}`;
  if (subEl) subEl.innerText = `Brüt: ₺${gross3Day.toFixed(2)} (Günlük %${(grossDaily*100).toFixed(2)}) • Stopaj (%${stopajPct}): ₺${taxWithheld.toFixed(2)}`;
}

function setWeekendAmt(val) {
  const inp = document.getElementById('weekend-calc-amt');
  if (inp) inp.value = val;
  runWeekendNemaCalc();
}

function quickAddGoalTx(goalTitle) {
  openTxModal();
  document.getElementById('tx-type').value = 'ALIM';
  adjustTxFields();
  document.getElementById('tx-desc').value = `Kumbara Hedefi: ${goalTitle}`;
  if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5004';
}

// 4 LLM Verilerini Arayüze ve Arena'ya Bas

async function refreshAllData(forceMarketRefresh = false, opts = {}) {
  const syncIcon = document.getElementById('dock-sync-icon');
  if (syncIcon) syncIcon.classList.add('fa-spin');
  try {
    const nwDays = Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30;
    const [portOk, , txsRes] = await Promise.all([
      fetchData(forceMarketRefresh),
      fetchNetWorthHistory(nwDays),
      fetchTransactions(),
      fetchOpportunityRadar(),
      fetchPersistenceStatus()
    ]);
    if (portOk && globalPortfolio) {
      const recovered = await checkAndReconcileEphemeralVault(globalPortfolio, txsRes || globalTransactions);
      if (recovered) {
        await Promise.all([fetchData(false), fetchTransactions(), fetchNetWorthHistory(nwDays)]);
      }
    }
    scheduleBackgroundWarmup();
    if (!opts || !opts.silentToast) {
      showToast(forceMarketRefresh ? "TEFAS ve canlı veriler sıfırdan yenilendi." : "Tüm veriler ve canlı grafikler senkronize edildi.");
    }
  } finally {
    if (syncIcon) syncIcon.classList.remove('fa-spin');
  }
}

async function bootstrapTerminal(forceRefresh = false) {
  const startT = performance.now();
  const nwDays = Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30;
  const q = `?days=${encodeURIComponent(nwDays)}${forceRefresh ? '&refresh=1' : ''}`;
  try {
    const resp = await fetch('/api/bootstrap' + q);
    if (resp.status === 401) {
      checkAuthStatus();
      return false;
    }
    if (!resp.ok) {
      await refreshAllData(forceRefresh, { silentToast: true });
      return false;
    }
    const b = await resp.json();
    const elapsed = Math.round(performance.now() - startT);
    if (b.preferences && typeof applyAppPreferences === 'function') {
      let localPrefs = {};
      try { localPrefs = JSON.parse(localStorage.getItem(FIN_PREFS_STORAGE_KEY) || '{}') || {}; } catch (_) {}
      applyAppPreferences(Object.assign({}, FIN_PREFS_DEFAULTS, localPrefs, b.preferences));
    }
    const ok = applyMarketAndPortfolioPayload(b.market, b.portfolio, elapsed);
    if (Array.isArray(b.transactions)) {
      globalTransactions = b.transactions;
      window.__lastFilteredTransactions = b.transactions;
      if (typeof renderFilteredLedger === 'function') renderFilteredLedger();
    }
    if (b.history && typeof renderNetWorthChartFromData === 'function') {
      window.__lastNwData = b.history;
      renderNetWorthChartFromData(b.history);
    }
    if (b.opportunity_radar && typeof applyOpportunityRadarPayload === 'function') {
      applyOpportunityRadarPayload(b.opportunity_radar);
    } else if (typeof fetchOpportunityRadar === 'function') {
      fetchOpportunityRadar();
    }
    if (b.alpha_summary && typeof renderTerminalAlphaStrip === 'function') {
      renderTerminalAlphaStrip(b.alpha_summary);
    } else if (typeof fetchTerminalAlphaStrip === 'function') {
      fetchTerminalAlphaStrip();
    }
    if (b.persistence && typeof applyPersistenceStatusPayload === 'function') {
      applyPersistenceStatusPayload(b.persistence);
    }
    if (ok && globalPortfolio) {
      const recovered = await checkAndReconcileEphemeralVault(globalPortfolio, b.transactions || globalTransactions);
      if (recovered) {
        await refreshAllData(false, { silentToast: true });
      }
    }
    scheduleBackgroundWarmup();
    return ok;
  } catch (err) {
    console.warn('Bootstrap fallback:', err);
    await refreshAllData(forceRefresh, { silentToast: true });
    return false;
  }
}

async function fetchData(forceMarketRefresh = false) {
  const startT = performance.now();
  const q = forceMarketRefresh ? '?refresh=1' : '';
  try {
    const [rawMarket, rawPort] = await Promise.all([
      fetch('/api/market' + q),
      fetch('/api/portfolio' + q)
    ]);
    if (rawMarket.status === 401 || rawPort.status === 401) {
      checkAuthStatus();
      return false;
    }
    const [resMarket, resPort] = await Promise.all([
      rawMarket.json(),
      rawPort.json()
    ]);
    const elapsed = Math.round(performance.now() - startT);
    return applyMarketAndPortfolioPayload(resMarket, resPort, elapsed);
  } catch (err) {
    console.error("Fetch error:", err);
    return false;
  }
}

function applyMarketAndPortfolioPayload(resMarket, resPort, elapsed = 0) {
  if (!resMarket || !resPort) return false;
  try {
    globalMarket = resMarket;
    globalPortfolio = resPort;
    window.__lastDataUpdatedAt = Date.now();
    if (typeof updateFreshnessBadge === 'function') updateFreshnessBadge();
    updateGoalsView();
    const latEl = document.getElementById('latency-val');
    if (latEl) latEl.innerText = `Gecikme: ${elapsed}ms`;

    // Seans Bilgisi
    if (resMarket.session) {
      const s = resMarket.session;
      const sPill = document.getElementById('session-pill');
      const sText = document.getElementById('session-text');
      sText.innerText = s.badge;
      if (s.is_tefas_open) {
        sPill.className = "sidebar-btn w-full flex items-center gap-3.5 px-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs font-semibold text-emerald-300";
        sPill.querySelector('span').className = "w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0 shadow-[0_0_8px_rgba(16,185,129,0.5)]";
      } else if (s.is_tzl_open) {
        sPill.className = "sidebar-btn w-full flex items-center gap-3.5 px-3.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-xs font-semibold text-cyan-300";
        sPill.querySelector('span').className = "w-2.5 h-2.5 rounded-full bg-cyan-400 flex-shrink-0 shadow-[0_0_8px_rgba(56,189,248,0.5)]";
      } else {
        sPill.className = "sidebar-btn w-full flex items-center gap-3.5 px-3.5 rounded-xl bg-[#070c1e] border border-white/[0.08] text-xs font-semibold text-slate-300";
        sPill.querySelector('span').className = "w-2.5 h-2.5 rounded-full bg-slate-500 flex-shrink-0";
      }
    }

    if (document.getElementById('price-zbj')) document.getElementById('price-zbj').innerText = '₺' + (resMarket.funds.ZBJ?.price || 5.6127).toFixed(4);
    document.getElementById('price-tzl').innerText = '₺' + (resMarket.funds.TZL?.price || 0).toFixed(4);
    document.getElementById('price-tca').innerText = '₺' + (resMarket.funds.TCA?.price || 0).toFixed(4);
    document.getElementById('price-aft').innerText = '₺' + (resMarket.funds.AFT?.price || 0).toFixed(4);
    document.getElementById('price-gold').innerText = '₺' + Math.round(resMarket.macro.gram_altin_try).toLocaleString();
    document.getElementById('price-ons').innerText = '$' + resMarket.macro.ons_altin_usd;
    document.getElementById('price-usd').innerText = '₺' + resMarket.macro.usd_try.toFixed(2);

    const total = resPort.total_value_try;
    const cash = resPort.cash_try;
    const moneyDec = Number(window.appPrefs?.moneyDecimals ?? 2);
    animateNumberValue(
      document.getElementById('total-val'),
      total,
      v => '₺' + v.toLocaleString('tr-TR', {minimumFractionDigits: moneyDec, maximumFractionDigits: moneyDec}),
      'totalVal'
    );
    animateNumberValue(
      document.getElementById('cash-val'),
      cash,
      v => '₺' + v.toLocaleString('tr-TR', {minimumFractionDigits: moneyDec, maximumFractionDigits: moneyDec}),
      'cashVal'
    );

    const accountsCashPill = document.getElementById('accounts-total-cash-pill');
    if (accountsCashPill) {
      accountsCashPill.innerText = `Banka Nakit: ₺${Number(cash || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }
    const accountsInvPill = document.getElementById('accounts-invested-pill');
    if (accountsInvPill) {
      const pendSum = (resPort.pending_orders || []).reduce((s, p) => s + Number(p.amount_try || 0), 0);
      const invVal = Number(resPort.funds_value_try || Math.max(0, total - cash - pendSum));
      accountsInvPill.innerText = `Fon + Valör: ₺${(invVal + pendSum).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }

    // Reel Varlık Karşılığı (Dolar, Euro & Gram Altın)
    const realEqEl = document.getElementById('total-real-eq');
    if (realEqEl) {
      const usdTry = Number(resMarket.macro?.usd_try || 0);
      const eurTry = Number(resMarket.macro?.eur_try || 0);
      const goldTry = Number(resMarket.macro?.gram_altin_try || 0);
      const eqUsd = usdTry > 0 ? (total / usdTry) : 0;
      const eqEur = eurTry > 0 ? (total / eurTry) : 0;
      const eqGold = goldTry > 0 ? (total / goldTry) : 0;
      const eurPart = eurTry > 0 ? ` <span class="text-slate-600">·</span> <span class="text-sky-200 font-semibold">€${eqEur.toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>` : '';
      realEqEl.innerHTML = `<span class="text-slate-400">Reel Karşılık:</span> <span class="text-slate-200 font-semibold">$${eqUsd.toLocaleString('tr-TR', {maximumFractionDigits: 0})}</span>${eurPart} <span class="text-slate-600">·</span> <span class="text-amber-300 font-semibold">${eqGold.toFixed(1)} gr Altın</span>`;
    }

    // Oturum Başından Beri Canlı Fark Rozeti (#session-delta-pill)
    if (typeof window.__sessionStartTotal === 'undefined' && total > 0) {
      window.__sessionStartTotal = total;
    }
    const sessPill = document.getElementById('session-delta-pill');
    if (sessPill && typeof window.__sessionStartTotal === 'number') {
      const sDiff = total - window.__sessionStartTotal;
      if (Math.abs(sDiff) >= 0.5) {
        sessPill.classList.remove('hidden');
        const pos = sDiff >= 0;
        sessPill.className = pos
          ? 'privacy-mask text-xs font-mono font-semibold px-2.5 py-1 rounded-full bg-emerald-500/15 border border-emerald-400/30 text-emerald-300'
          : 'privacy-mask text-xs font-mono font-semibold px-2.5 py-1 rounded-full bg-rose-500/15 border border-rose-400/30 text-rose-300';
        sessPill.innerText = `${pos ? '+' : '-'}₺${Math.abs(sDiff).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} oturum`;
      }
    }

    // Bugünkü Fonların Ağırlıklı Tahmini Günlük Kârı (#daily-est-pnl-pill)
    const dailyEstPill = document.getElementById('daily-est-pnl-pill');
    if (dailyEstPill) {
      let dayEstTry = 0;
      let fundSumTry = 0;
      (resPort.funds || []).forEach(f => {
        const val = Number(f.current_value || 0);
        const chg = Number(resMarket.funds?.[f.code]?.daily_yield_pct || 0);
        if (val > 0) {
          fundSumTry += val;
          dayEstTry += val * (chg / 100);
        }
      });
      const dayEstPct = fundSumTry > 0 ? (dayEstTry / fundSumTry) * 100 : 0;
      const pos = dayEstTry >= 0;
      dailyEstPill.className = pos
        ? 'privacy-mask text-xs font-mono px-2.5 py-1 rounded-full bg-emerald-500/12 border border-emerald-400/25 text-emerald-300'
        : 'privacy-mask text-xs font-mono px-2.5 py-1 rounded-full bg-rose-500/12 border border-rose-400/25 text-rose-300';
      dailyEstPill.innerText = `Bugün Fonlar: ${pos ? '+' : '-'}₺${Math.abs(dayEstTry).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} (${pos ? '+' : ''}%${dayEstPct.toFixed(2)})`;
    }

    // Mikro Sparkline SVG & Günlük Getiri Rozetleri
    updateTickerSparklines(resMarket);

    // Toplam Net Kâr / Zarar Özeti (ağırlıklı maliyet vs canlı değer + akıcı sayaç)
    let totalNetPnl = 0;
    let totalCostBasis = 0;
    (resPort.funds || []).forEach(f => {
      const shares = Number(f.shares || 0);
      if (shares > 0) {
        const cost = shares * Number(f.buy_price || 0);
        const val = Number(f.current_value || 0);
        const pnlTry = f.profit_try !== undefined && f.profit_try !== null ? Number(f.profit_try) : (val - cost);
        totalNetPnl += pnlTry;
        totalCostBasis += cost;
      }
    });
    const totalPnlPct = totalCostBasis > 0 ? (totalNetPnl / totalCostBasis) * 100 : 0;
    const pnlBadge = document.getElementById('total-pnl-badge');
    if (pnlBadge) {
      pnlBadge.className = totalNetPnl > 0.005
        ? 'privacy-mask text-xs font-mono font-semibold px-3 py-1 rounded-full bg-emerald-500/12 border border-emerald-500/30 text-emerald-300 inline-flex items-center gap-1.5'
        : (totalNetPnl < -0.005
          ? 'privacy-mask text-xs font-mono font-semibold px-3 py-1 rounded-full bg-rose-500/12 border border-rose-500/30 text-rose-300 inline-flex items-center gap-1.5'
          : 'privacy-mask text-xs font-mono font-semibold px-3 py-1 rounded-full bg-white/[0.05] border border-white/[0.09] text-slate-300 inline-flex items-center gap-1.5');
      animateNumberValue(
        pnlBadge,
        totalNetPnl,
        v => {
          const s = v >= 0 ? '+' : '-';
          const absStr = Math.abs(v).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2});
          const ps = totalPnlPct >= 0 ? '+' : '';
          return `<span class="opacity-75 font-sans font-medium">Toplam Kâr:</span> <span>${s}₺${absStr}</span> <span class="opacity-85">(${ps}%${totalPnlPct.toFixed(2)})</span>`;
        },
        'netPnl',
        true
      );
    }

    // 4 Yıllık Mezuniyet Hedefi & Kilometre Taşı Parıltısı
    const goalTgt = getGoalTargetTry();
    const pct = Math.min(100, (total / goalTgt * 100));
    document.getElementById('goal-progress-text').innerText = `Mevcut İlerleme: ₺${total.toLocaleString('tr-TR', {minimumFractionDigits: moneyDec, maximumFractionDigits: moneyDec})} / ₺${goalTgt.toLocaleString('tr-TR')} (%${pct.toFixed(2)})`;
    document.getElementById('goal-progress-bar').style.width = Math.max(0.5, pct) + '%';
    checkGoalMilestones(total, pct);

    // TZL & ZBJ Nema Pasif Getiri Metresi (Bileşik PPF)
    const tzlShares = resPort.funds.find(f => f.code === 'TZL')?.shares || 0;
    const tzlPrice = resMarket.funds.TZL?.price || 0.149674;
    const zbjShares = resPort.funds.find(f => f.code === 'ZBJ')?.shares || 0;
    const zbjPrice = resMarket.funds.ZBJ?.price || 5.612738;
    const totalPpfVal = (tzlShares * tzlPrice) + (zbjShares * zbjPrice);
    const ppfRate = Number(resPort.ppf_daily_rate || 0.0011);
    const dailyNema = totalPpfVal * ppfRate;
    const monthlyNema = dailyNema * 30.5;
    document.getElementById('nema-meter').innerText = `₺${dailyNema.toFixed(2)} / gün (₺${monthlyNema.toFixed(2)} / ay)`;

    // Hayatta Kalma / İdare Süresi & Likit Sağlık Rozeti
    const runwayEl = document.getElementById('runway-val');
    const runwayPill = document.getElementById('runway-pill');
    const liqHealthPill = document.getElementById('liquidity-health-pill');
    if (runwayEl) {
      const liquidTry = Number(cash || 0) + totalPpfVal;
      const hasBurn = Boolean(resPort.has_burn_history && resPort.monthly_burn_try > 100);
      const monthlyBurn = hasBurn ? Number(resPort.monthly_burn_try) : 4000;
      const months = monthlyBurn > 0 ? (liquidTry / monthlyBurn) : 0;
      runwayEl.innerText = `~${months.toFixed(1)} Ay`;
      if (runwayPill) {
        runwayPill.title = hasBurn
          ? `Son harcamalarına göre (aylık ~₺${Math.round(monthlyBurn).toLocaleString('tr-TR')}) nakit ve günlük faizdeki paran seni ~${months.toFixed(1)} ay idare eder.`
          : `Henüz harcama kaydın az olduğu için aylık ₺4.000 gider baz alınarak hesaplandı (~${months.toFixed(1)} ay).`;
      }
      if (liqHealthPill && total > 0) {
        const liqRatio = (liquidTry / total) * 100;
        if (months >= 3) {
          liqHealthPill.className = 'text-xs font-sans px-2.5 py-1 rounded-full bg-emerald-500/12 border border-emerald-500/25 text-emerald-300';
          liqHealthPill.innerText = `Likit Zırh: Güçlü (%${liqRatio.toFixed(0)})`;
        } else if (months >= 1) {
          liqHealthPill.className = 'text-xs font-sans px-2.5 py-1 rounded-full bg-amber-500/12 border border-amber-500/25 text-amber-300';
          liqHealthPill.innerText = `Likit Tampon: Orta (%${liqRatio.toFixed(0)})`;
        } else {
          liqHealthPill.className = 'text-xs font-sans px-2.5 py-1 rounded-full bg-rose-500/12 border border-rose-500/25 text-rose-300';
          liqHealthPill.innerText = `Nakit Tamponu Düşük (%${liqRatio.toFixed(0)})`;
        }
      }
    }
    updateGoalEtaBadge();

    // Hesaplar (Yalnızca değişiklik varsa DOM yeniden çizilir)
    globalAccounts = resPort.accounts || [];
    const accSig = JSON.stringify(globalAccounts.map(a => [a.id, a.name, a.balance]));
    if (window.__lastAccSig !== accSig) {
      window.__lastAccSig = accSig;
      renderAccountsGrid(globalAccounts);
      updateAccountDropdowns(globalAccounts);
    }

    const pendingOrders = resPort.pending_orders || [];
    const pendDockBadge = document.getElementById('dock-badge-pending');
    if (pendDockBadge) {
      pendDockBadge.classList.toggle('hidden', pendingOrders.length === 0);
      pendDockBadge.innerText = String(pendingOrders.length);
    }
    let ppfVal = 0, altinVal = 0, growthVal = 0;
    const fundsList = resPort.funds || [];
    const marketFundsMap = (globalMarket && globalMarket.funds) || {};
    fundsList.forEach(f => {
      const code = String(f.code || '').toUpperCase();
      const meta = marketFundsMap[code] || f;
      const bucket = classifyFundBucket(code, meta);
      const val = Number(f.current_value || 0);
      if (bucket === 'ppf') ppfVal += val;
      else if (bucket === 'altin') altinVal += val;
      else growthVal += val;
    });

    window.__portfolioSort = window.__portfolioSort || { col: 'value', dir: 'desc' };
    window.sortPortfolioBy = function (col) {
      const st = window.__portfolioSort || { col: 'value', dir: 'desc' };
      if (st.col === col) {
        st.dir = st.dir === 'asc' ? 'desc' : 'asc';
      } else {
        st.col = col;
        st.dir = col === 'code' ? 'asc' : 'desc';
      }
      window.__portfolioSort = st;
      window.__lastHoldingsSig = null;
      if (globalMarket && globalPortfolio) {
        applyMarketAndPortfolioPayload(globalMarket, globalPortfolio, 0);
      }
    };

    const pSort = window.__portfolioSort;
    document.querySelectorAll('th[data-port-sort]').forEach(th => {
      const c = th.dataset.portSort;
      const arrow = th.querySelector('.sort-arrow');
      if (c === pSort.col) {
        th.setAttribute('aria-sort', pSort.dir === 'asc' ? 'ascending' : 'descending');
        if (arrow) arrow.innerText = pSort.dir === 'asc' ? '▲' : '▼';
      } else {
        th.setAttribute('aria-sort', 'none');
        if (arrow) arrow.innerText = '↕';
      }
    });

    const sortedFunds = [...fundsList].sort((a, b) => {
      const m = pSort.dir === 'asc' ? 1 : -1;
      switch (pSort.col) {
        case 'code': return m * String(a.code || '').localeCompare(String(b.code || ''), 'tr');
        case 'shares': return m * (Number(a.shares || 0) - Number(b.shares || 0));
        case 'price': return m * (Number(a.current_price || 0) - Number(b.current_price || 0));
        case 'profit': return m * (Number(a.profit_try || 0) - Number(b.profit_try || 0));
        default: return m * (Number(a.current_value || 0) - Number(b.current_value || 0));
      }
    });

    const shareDec = Number(window.appPrefs?.fundShareDecimals ?? 0);
    const holdingsSig = JSON.stringify([pSort.col, pSort.dir, shareDec, moneyDec, sortedFunds.map(f => [f.code, f.shares, f.current_price, f.buy_price, f.profit_try, f.net_profit_try, f.stopaj_pct, f.accrued_price_estimated])]);
    if (window.__lastHoldingsSig !== holdingsSig) {
      window.__lastHoldingsSig = holdingsSig;
      const tbody = document.getElementById('holdings-table');
      const frag = document.createDocumentFragment();
      sortedFunds.forEach(f => {
        const row = document.createElement('tr');
        row.className = 'hover:bg-slate-800/35 transition-colors';
        const shareNum = Number(f.shares || 0);
        const shareFormatted = shareDec === 0
          ? Math.round(shareNum).toLocaleString('tr-TR')
          : shareNum.toLocaleString('tr-TR', { minimumFractionDigits: shareDec, maximumFractionDigits: shareDec });
        const pnl = Number(f.profit_try || 0);
        const pnlPct = Number(f.profit_pct || 0);
        const pnlSign = pnl >= 0 ? '+' : '';
        const pnlCls = pnl > 0 ? 'text-emerald-400' : (pnl < 0 ? 'text-rose-400' : 'text-slate-400');
        const netExit = Number(f.net_exit_value_try ?? f.current_value ?? 0);
        const stopajPct = Number(f.stopaj_pct ?? 15);
        const taxCut = Number(f.estimated_tax_try || 0);
        const estBadge = f.accrued_price_estimated
          ? `<span title="TEFAS son resmi tarih: ${escapeHtml(f.tefas_date || '')} (₺${Number(f.official_price || 0).toFixed(4)}). Ziraat akşam/hafta sonu +${Number(f.accrued_gap_days || 1)}G valör neması dahil edildi." class="ml-1.5 px-2 py-0.5 rounded-md bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 text-xs font-sans font-medium">+${Number(f.accrued_gap_days || 1)}G Getiri</span>`
          : '';

        const rivalMap = { 'TCA': 'TTA', 'AFT': 'AFA', 'TZL': 'PPN', 'ZBJ': 'PPN' };
        const rivalCode = rivalMap[String(f.code || '').toUpperCase()] || 'ZBJ';
        const curValRound = Math.max(50, Math.round(Number(f.current_value || 0)));
        const curProfitRound = Math.max(0, Math.round(Number(f.profit_try || 0)));

        row.innerHTML = `
          <td class="py-4 font-bold text-white">
            <div class="flex items-center gap-2.5">
              <span class="w-2.5 h-2.5 rounded-full ${f.shares > 0 ? 'bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 'bg-slate-600'}"></span>
              <span>${escapeHtml(f.code)}</span>
              <span class="text-xs text-slate-400 font-normal hidden sm:inline">(${escapeHtml(f.name)})</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 mt-1.5 font-sans">
              <button type="button" onclick="openInlineTefasKarne('${escapeHtml(f.code)}')" class="px-2 py-0.5 rounded-md bg-indigo-500/15 hover:bg-indigo-500/30 text-indigo-200 border border-indigo-400/30 text-[11px] font-semibold transition inline-flex items-center gap-1" title="${escapeHtml(f.code)} fonunun 1 yıllık ZBJ faiz kıyası ve stopaj karnesi"><i class="fa-solid fa-chart-column text-[10px]"></i><span>1Y Karne</span></button>
              ${f.shares > 0 ? `<button type="button" onclick="openInlineSwitchSim('${escapeHtml(f.code)}','${rivalCode}',${curValRound},${curProfitRound})" class="px-2 py-0.5 rounded-md bg-emerald-500/15 hover:bg-emerald-500/30 text-emerald-200 border border-emerald-400/30 text-[11px] font-semibold transition inline-flex items-center gap-1" title="Elindeki ₺${curValRound} ${escapeHtml(f.code)} fonunu satıp ${rivalCode} fonuna geçersen kaç günde amorti eder?"><i class="fa-solid fa-arrows-rotate text-[10px]"></i><span>${rivalCode} Amorti</span></button>` : ''}
            </div>
          </td>
          <td class="py-4 text-slate-200 font-mono privacy-mask">${shareFormatted} Pay</td>
          <td class="py-4 font-mono">
            <div class="text-slate-100 flex items-center flex-wrap">₺${Number(f.current_price || 0).toFixed(4)}${estBadge}</div>
            <div class="text-xs text-slate-400 mt-0.5">Alış Ort: ₺${Number(f.buy_price || 0).toFixed(4)}</div>
          </td>
          <td class="py-4 font-mono privacy-mask">
            <div class="font-bold text-white">₺${Number(f.current_value || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
            ${f.shares > 0 && taxCut > 0 ? `<div class="text-xs text-slate-400 mt-0.5">Net Çıkış: ₺${netExit.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} (-%${stopajPct})</div>` : ''}
          </td>
          <td class="py-4 text-right font-mono privacy-mask">
            ${f.shares > 0 ? `<div class="inline-flex items-center justify-end gap-2.5"><span class="${pnlCls} font-bold text-[13px]">${pnlSign}₺${pnl.toFixed(2)} (${pnlSign}%${pnlPct.toFixed(2)})</span><button type="button" onclick="moveFundToPending('${escapeHtml(f.code)}')" title="Henüz işleme girmediyse bekleme listesine çek" class="px-2.5 py-1 rounded-lg bg-amber-500/12 hover:bg-amber-500/25 text-amber-300 border border-amber-500/25 text-xs font-sans font-medium">Valör</button></div>` : `<span class="px-3 py-1 rounded-full text-xs font-semibold bg-[#070c1e] text-slate-400">Yok</span>`}
          </td>
        `;
        frag.appendChild(row);
      });
      tbody.replaceChildren(frag);
    }

    // Stopaj özet şeridi & Dağıtıcı Tax Paneli (Modül 2)
    if (resPort.tax_summary && resPort.tax_summary.totals) {
      const tt = resPort.tax_summary.totals;
      const netEl = document.getElementById('tax-net-exit-val');
      const cutEl = document.getElementById('tax-est-cut');
      if (netEl) netEl.innerText = `₺${Number(tt.net_exit_value_try || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (cutEl) cutEl.innerText = `(Tahmini Stopaj Kesintisi: -₺${Number(tt.estimated_tax_try || 0).toFixed(2)} • ${tt.net_profit_try >= 0 ? '+' : ''}₺${Number(tt.net_profit_try || 0).toFixed(2)} Net Kâr)`;
      if (typeof renderTaxBreakdown === 'function') renderTaxBreakdown(resPort.tax_summary);
    }

    if (typeof updateExecutiveBentoStrip === 'function') {
      updateExecutiveBentoStrip(resMarket, resPort);
    }

    const vModeEl = document.getElementById('valor-mode-select');
    if (vModeEl && resPort.valor_config && resPort.valor_config.mode) {
      vModeEl.value = resPort.valor_config.mode;
    }

    // Valördeki bekleyen emirleri TEFAS canlı takvim bilgisiyle render et (Modül 1)
    const pendingPanel = document.getElementById('pending-orders-panel');
    const pendingListEl = document.getElementById('pending-orders-list');
    const pendingSig = JSON.stringify(pendingOrders.map(po => [po.id, po.code, po.amount_try, po.estimated_shares, po.live_price, po.valor_schedule?.confirmed_price, po.valor_schedule?.stage_badge]));
    if (pendingPanel && pendingListEl && window.__lastPendingSig !== pendingSig) {
      window.__lastPendingSig = pendingSig;
      if (!pendingOrders.length) {
        pendingPanel.classList.add('hidden');
        pendingListEl.innerHTML = '';
      } else {
        pendingPanel.classList.remove('hidden');
        pendingListEl.innerHTML = pendingOrders.map(po => {
          const vs = po.valor_schedule || {};
          const amt = Number(po.amount_try || 0);
          const liveP = Number(vs.confirmed_price || po.live_price || po.estimated_price || 0);
          const projSh = Math.round(Number(vs.projected_shares || po.estimated_shares || 0)).toLocaleString('tr-TR');
          const isReady = Boolean(vs.ready_to_settle);
          const boxCls = isReady ? 'bg-emerald-500/[0.08] border-emerald-500/35' : 'bg-amber-500/[0.06] border-amber-500/25';
          const tagCls = isReady ? 'bg-emerald-500/25 text-emerald-300' : 'bg-amber-500/20 text-amber-300';
          const tagTxt = isReady ? 'Onay Saatinde' : (vs.t_badge ? `Bekliyor (${vs.t_badge})` : 'Valörde');
          const priceLabel = vs.confirmed_price ? `Kesinleşen TEFAS (${vs.confirmed_date}): ₺${vs.confirmed_price.toFixed(6)}` : `Piyasa: ₺${liveP.toFixed(4)} • Geçiş: ${vs.expected_text || po.expected_settlement || 'Bekleniyor'}`;
          return `
            <div class="p-3.5 rounded-xl ${boxCls} flex flex-wrap items-center justify-between gap-3 text-[13px]">
              <div class="space-y-1.5 flex-1 min-w-[220px]">
                <div class="font-bold text-white flex flex-wrap items-center gap-2">
                  <span class="px-2.5 py-0.5 rounded-full ${tagCls} text-xs font-mono">${escapeHtml(tagTxt)}</span>
                  <span>${escapeHtml(po.code)} · ${escapeHtml(po.fund_name || po.code)}</span>
                  <span class="text-xs text-slate-400 font-mono">(${escapeHtml(vs.stage_badge || '')})</span>
                </div>
                <div class="text-[13px] text-slate-200 font-mono">
                  Ayrılan: ₺${amt.toLocaleString('tr-TR', {minimumFractionDigits: 2})} → ~${projSh} pay · ${escapeHtml(priceLabel)} · <span class="text-slate-400">Kâr/Zarar: ₺0.00</span>
                </div>
                <div class="h-1.5 w-full max-w-[320px] bg-slate-900 rounded-full overflow-hidden">
                  <div class="h-full ${isReady ? 'bg-emerald-400' : 'bg-amber-400'} transition-all" style="width:${Number(vs.progress_pct || 35)}%"></div>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button type="button" onclick="settlePendingOrder('${escapeHtml(po.id)}', '${escapeHtml(po.code)}', ${amt}, ${liveP})" class="h-9 px-3.5 rounded-xl bg-emerald-600/90 hover:bg-emerald-500 text-white font-semibold text-xs transition active:scale-95"><i class="fa-solid fa-check mr-1"></i>İşleme Girdi</button>
                <button type="button" onclick="cancelPendingOrder('${escapeHtml(po.id)}')" class="h-9 px-3 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/30 text-xs font-medium transition">İptal</button>
              </div>
            </div>
          `;
        }).join('');
      }
    }

    pendingOrders.forEach(po => {
      const pa = Number(po.amount_try || 0);
      const code = String(po.code || '').toUpperCase();
      const bucket = classifyFundBucket(code, marketFundsMap[code] || po);
      if (bucket === 'ppf') ppfVal += pa;
      else if (bucket === 'altin') altinVal += pa;
      else growthVal += pa;
    });

    if (total > 0) {
      const ppfPct = ((ppfVal / total) * 100).toFixed(1);
      const altinPct = ((altinVal / total) * 100).toFixed(1);
      const growthPct = ((growthVal / total) * 100).toFixed(1);
      const cashPct = ((cash / total) * 100).toFixed(1);

      document.getElementById('bar-tzl').style.width = ppfPct + '%';
      document.getElementById('bar-tca').style.width = altinPct + '%';
      document.getElementById('bar-aft').style.width = growthPct + '%';
      document.getElementById('bar-cash').style.width = cashPct + '%';
      document.getElementById('alloc-text').innerText = `PPF: %${ppfPct} • Altın: %${altinPct} • Hisse/Büyüme: %${growthPct} • Nakit: %${cashPct}`;

      const investedSum = ppfVal + altinVal + growthVal;
      const cmpEl = document.getElementById('alloc-target-compare');
      if (cmpEl && investedSum > 0) {
        const pInv = (ppfVal / investedSum) * 100;
        const aInv = (altinVal / investedSum) * 100;
        const gInv = (growthVal / investedSum) * 100;
        const mkChip = (lbl, cur, tgt, clr) => {
          const diff = cur - tgt;
          const sign = diff >= 0 ? '+' : '';
          return `<span class="px-2.5 py-0.5 rounded-full bg-white/[0.05] border border-white/[0.08] ${clr}">${lbl}: %${cur.toFixed(0)} <span class="text-slate-400">(Hedef %${tgt} · ${sign}${diff.toFixed(0)}p)</span></span>`;
        };
        cmpEl.innerHTML = `<span class="text-slate-400 font-sans">Fon Sepeti Hedef Sapma:</span>` +
          mkChip('PPF', pInv, 45, 'text-emerald-300') +
          mkChip('Altın', aInv, 35, 'text-amber-300') +
          mkChip('Büyüme', gInv, 20, 'text-indigo-300');
      }
    }

    syncDynamicFundSelects(marketFundsMap, fundsList);
    if (typeof runPortfolioRebalance === 'function') runPortfolioRebalance();
    return true;

  } catch (err) {
    console.error("Fetch error:", err);
    return false;
  }
}


function renderAccountsGrid(accounts) {
  const container = document.getElementById('accounts-grid');
  container.innerHTML = '';

  accounts.forEach(acc => {
    const div = document.createElement('div');
    div.className = 'bg-[#070c1e] border border-white/[0.08] p-3 sm:p-4 rounded-xl flex flex-col justify-between hover:border-indigo-500/40 transition group shadow-inner';
    div.innerHTML = `
      <div>
        <div class="flex items-center justify-between text-[13px] text-slate-300">
          <span class="truncate pr-2 font-semibold text-white">${escapeHtml(acc.name)}</span>
          <button data-acc-id="${escapeHtml(acc.id)}" onclick="openEditAccountModal(this.dataset.accId)" class="w-8 h-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-indigo-300 hover:bg-white/[0.06] transition" title="Bakiyeyi Güncelle">
            <i class="fa-solid fa-pen text-xs"></i>
          </button>
        </div>
        <div class="text-xl sm:text-2xl font-bold text-white mt-1 sm:mt-1.5 font-mono privacy-mask">₺${Number(acc.balance || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2})}</div>
      </div>
      <div class="text-xs text-slate-400 mt-2.5 sm:mt-3 flex items-center justify-between gap-2 border-t border-white/[0.06] pt-2 sm:pt-2.5">
        <span class="text-slate-400 font-medium truncate">${escapeHtml(acc.bank || 'Hesap')}</span>
        <div class="flex items-center gap-1.5 shrink-0">
          <button type="button" data-acc-id="${escapeHtml(acc.id)}" onclick="quickTransferFromAccount(this.dataset.accId)" class="px-2 py-1 rounded-lg bg-cyan-500/12 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/25 font-semibold transition" title="Bu hesaptan virman başlat">
            <i class="fa-solid fa-arrow-right-arrow-left text-xs"></i> Virman
          </button>
          <button type="button" data-acc-id="${escapeHtml(acc.id)}" onclick="quickTxForAccount(this.dataset.accId)" class="px-2 py-1 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/30 text-indigo-200 border border-indigo-400/30 font-semibold transition" title="Bu hesapla hızlı işlem aç">
            + İşlem
          </button>
        </div>
      </div>
    `;
    container.appendChild(div);
  });
}

function updateAccountDropdowns(accounts) {
  const selFrom = document.getElementById('tx-account');
  const selTo = document.getElementById('tx-to-account');
  if (!selFrom || !selTo) return;

  const currentFrom = selFrom.value;
  const currentTo = selTo.value;

  selFrom.innerHTML = '';
  selTo.innerHTML = '';

  accounts.forEach(a => {
    const opt1 = document.createElement('option');
    opt1.value = a.id;
    opt1.innerText = `${a.name} (₺${a.balance.toFixed(2)})`;
    selFrom.appendChild(opt1);

    const opt2 = document.createElement('option');
    opt2.value = a.id;
    opt2.innerText = `${a.name} (₺${a.balance.toFixed(2)})`;
    selTo.appendChild(opt2);
  });

  if (currentFrom) selFrom.value = currentFrom;
  if (currentTo) selTo.value = currentTo;
  else if (accounts.length > 1) selTo.selectedIndex = 1;
}

function openEditAccountModal(id) {
  const acc = (globalAccounts || []).find(a => a.id === id);
  if (!acc) return;
  document.getElementById('acc-modal-id').value = acc.id;
  document.getElementById('acc-modal-name').value = acc.name;
  document.getElementById('acc-modal-balance').value = String(Number(acc.balance || 0).toFixed(2));
  const errEl = document.getElementById('acc-modal-error');
  if (errEl) {
    errEl.classList.add('hidden');
    errEl.innerText = '';
  }
  document.getElementById('account-modal').classList.remove('hidden');
}

async function openNewAccountPrompt() {
  const res = await showAppPrompt({
    title: 'Yeni Hesap Tanımla',
    message: 'Takip etmek istediğiniz banka veya nakit hesabın bilgilerini girin.',
    confirmText: 'Hesabı Ekle',
    fields: [
      { name: 'id', label: 'Hesap Kodu / ID (Örn: papara, garanti, kyk)', placeholder: 'papara', required: true },
      { name: 'name', label: 'Hesap Adı', placeholder: 'Papara Nakit Kartı', value: '', required: true },
      { name: 'balance', label: 'Mevcut Bakiye (₺)', placeholder: '0,00', value: '0', inputmode: 'decimal' }
    ]
  });
  if (!res) return;
  const accId = res.id.trim();
  const accName = (res.name || accId).trim();
  const balance = Math.max(0, parseTrNumber(res.balance));
  saveAccountData(accId, accName, balance);
}

function closeAccountModal() {
  document.getElementById('account-modal').classList.add('hidden');
}

async function saveAccountBalance() {
  const id = document.getElementById('acc-modal-id').value;
  const name = String(document.getElementById('acc-modal-name').value || '').trim();
  const rawBal = String(document.getElementById('acc-modal-balance').value || '').trim();
  const balance = Math.max(0, parseTrNumber(rawBal));
  const errEl = document.getElementById('acc-modal-error');
  const showAccErr = (msg) => {
    if (errEl) {
      errEl.innerText = msg;
      errEl.classList.remove('hidden');
    } else {
      showToast(msg, 'error');
    }
  };
  if (!name) {
    showAccErr('Lütfen geçerli bir hesap adı girin.');
    return;
  }
  if (!rawBal || Number.isNaN(balance)) {
    showAccErr('Lütfen geçerli bir tutar girin (Örn: 500 veya 250,50).');
    return;
  }

  const ok = await saveAccountData(id, name, balance);
  if (ok) {
    closeAccountModal();
    showToast(`${name} bakiyesi ₺${balance.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} olarak güncellendi.`);
  } else {
    showAccErr('Hesap güncellenemedi. Lütfen oturumu ve bağlantıyı kontrol edin.');
  }
}

async function recalculateAccountsFromLedger() {
  try {
    const resp = await fetch('/api/accounts/recalculate-from-ledger', { method: 'POST' });
    if (resp.status === 401) {
      showToast('Oturum kilitlendi. Lütfen şifre ile giriş yapın.', 'warn');
      if (typeof checkAuthStatus === 'function') checkAuthStatus();
      return;
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.status !== 'ok') {
      showToast(data.detail || 'Bakiyeler defterden hesaplanamadı.', 'error');
      return;
    }
    window.__lastAccSig = null;
    await refreshAllData(false, { silentToast: true });
    const b = data.balances || {};
    showToast(`Bakiyeler defterle eşitlendi (5001: ₺${Number(b['5001'] || 0).toFixed(2)} · 5004: ₺${Number(b['5004'] || 0).toFixed(2)}).`, 'success');
  } catch (e) {
    showToast('Bağlantı hatası: ' + e.message, 'error');
  }
}

async function saveAccountData(id, name, balance) {
  try {
    const resp = await fetch('/api/accounts/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, name, balance})
    });
    if (resp.status === 401) {
      showToast('Oturum kilitlendi. Lütfen şifre ile giriş yapın.', 'warn');
      if (typeof checkAuthStatus === 'function') checkAuthStatus();
      return false;
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.status !== 'ok') {
      showToast(data.message || 'Hesap bakiyesi kaydedilemedi.', 'error');
      return false;
    }
    window.__lastAccSig = null;
    await refreshAllData(false, { silentToast: true });
    return true;
  } catch (e) {
    showToast('Bağlantı hatası: ' + e.message, 'error');
    return false;
  }
}


function toggleAuthPasswordVisibility() {
  const inp = document.getElementById('auth-pin-input');
  const icon = document.getElementById('auth-eye-icon');
  if (!inp || !icon) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    icon.className = 'fa-solid fa-eye-slash text-xs text-indigo-400';
  } else {
    inp.type = 'password';
    icon.className = 'fa-solid fa-eye text-xs text-slate-400';
  }
}

function setShellLocked(locked) {
  const overlay = document.getElementById('auth-lock-overlay');
  if (locked) {
    document.body.classList.add('shell-locked');
    if (overlay) overlay.classList.remove('hidden');
  } else {
    document.body.classList.remove('shell-locked');
    if (overlay) overlay.classList.add('hidden');
    if (typeof syncDockLiquidPill === 'function') {
      requestAnimationFrame(() => syncDockLiquidPill(document.querySelector('#sidebar-right nav .sidebar-btn.active')));
    }
    if (!window.__shellUnlockedOnce) {
      window.__shellUnlockedOnce = true;
      const prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!prefersReduced && typeof window.gsap !== 'undefined') {
        const tickers = document.querySelectorAll('.ticker-card');
        if (tickers.length) {
          window.gsap.fromTo(
            tickers,
            { opacity: 0, y: 12 },
            { opacity: 1, y: 0, duration: 0.36, stagger: 0.04, ease: 'power2.out', clearProps: 'transform,opacity' }
          );
        }
        const activeView = document.querySelector('.view-section.active');
        if (activeView) {
          const cards = activeView.querySelectorAll('.cosmic-card');
          if (cards.length) {
            window.gsap.fromTo(
              cards,
              { opacity: 0, y: 14 },
              { opacity: 1, y: 0, duration: 0.38, stagger: 0.045, delay: 0.05, ease: 'power2.out', clearProps: 'transform,opacity' }
            );
          }
        }
      }
    }
  }
}

async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth/status').then(r => r.json());
    if (res.authenticated) {
      setShellLocked(false);
      return true;
    } else {
      setShellLocked(true);
      const inp = document.getElementById('auth-pin-input');
      if (inp) inp.focus();
      return false;
    }
  } catch (err) {
    console.error("Auth status error:", err);
    setShellLocked(true);
    return false;
  }
}

async function handlePinLogin(e) {
  if (e) e.preventDefault();
  const inp = document.getElementById('auth-pin-input');
  const errEl = document.getElementById('auth-error-msg');
  const pin = inp ? inp.value.trim() : '';

  if (!pin) {
    if (errEl) {
      errEl.innerText = "Lütfen şifrenizi giriniz.";
      errEl.classList.remove('hidden');
    }
    return;
  }

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ pin })
    }).then(r => r.json());

    if (res.status === 'ok') {
      setShellLocked(false);
      if (errEl) errEl.classList.add('hidden');
      showToast("Oturum doğrulandı. Terminal kilidi açıldı.");
      if (typeof bootstrapTerminal === 'function') {
        await bootstrapTerminal();
      } else {
        if (typeof loadAppPreferences === 'function') await loadAppPreferences();
        refreshAllData();
      }
      initProjectionChart(Number(window.appPrefs?.defaultMonthlyDeposit || 5500));
      fetchLiveNews();
      fetchTelegramStatus();
      fetchAiConfigStatus();
    } else {
      if (errEl) {
        errEl.innerText = res.message || "Hatalı şifre! Erişim reddedildi.";
        errEl.classList.remove('hidden');
      }
      if (inp) {
        inp.value = '';
        inp.focus();
      }
    }
  } catch (err) {
    if (errEl) {
      errEl.innerText = "Giriş hatası: " + err;
      errEl.classList.remove('hidden');
    }
  }
}

async function lockTerminal() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  setShellLocked(true);
  const inp = document.getElementById('auth-pin-input');
  if (inp) {
    inp.value = '';
    inp.focus();
  }
  showToast("Terminal güvenle kilitlendi.", "info");
}

async function revokeAllSessions() {
  const ok = await showAppConfirm({
    title: 'Tüm Oturumları Kapat',
    message: 'Bu cihaz dahil açık bırakılmış tüm cihazlardaki aktif terminal oturumları sonlandırılacak. Devam edilsin mi?',
    confirmText: 'Tümünü Kapat',
    tone: 'danger'
  });
  if (!ok) return;
  try {
    const res = await fetch('/api/auth/revoke-all', { method: 'POST' }).then(r => r.json());
    closeChangePinModal();
    setShellLocked(true);
    showToast(res.message || "Tüm cihaz oturumları kapatıldı.", "info");
  } catch (err) {
    showToast("Oturumlar kapatılamadı: " + err, "error");
  }
}

(function initAuthCapsLockIndicator() {
  const pinInput = document.getElementById('auth-pin-input');
  const capsPill = document.getElementById('auth-capslock-warn');
  if (!pinInput || !capsPill) return;
  const checkCaps = (ev) => {
    const on = Boolean(ev && typeof ev.getModifierState === 'function' && ev.getModifierState('CapsLock'));
    capsPill.classList.toggle('hidden', !on);
  };
  pinInput.addEventListener('keydown', checkCaps);
  pinInput.addEventListener('keyup', checkCaps);
  pinInput.addEventListener('blur', () => capsPill.classList.add('hidden'));
})();

(function initAutoLockIdleGuard() {
  let idleTimer = null;
  const IDLE_LIMIT_MS = 15 * 60 * 1000;
  const resetIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    const overlay = document.getElementById('auth-lock-overlay');
    if (!overlay || !overlay.classList.contains('hidden')) return;
    idleTimer = setTimeout(() => {
      const ov = document.getElementById('auth-lock-overlay');
      if (ov && ov.classList.contains('hidden')) {
        lockTerminal();
        showToast("15 dakika hareketsizlik nedeniyle terminal otomatik kilitlendi.", "info");
      }
    }, IDLE_LIMIT_MS);
  };
  ['mousemove', 'keydown', 'touchstart', 'click', 'scroll'].forEach(evt => {
    window.addEventListener(evt, resetIdle, { passive: true });
  });
})();

function updateHeaderClock() {
  const el = document.getElementById('header-live-date');
  if (!el) return;
  const now = new Date();
  try {
    const dateStr = new Intl.DateTimeFormat('tr-TR', {
      timeZone: 'Europe/Istanbul',
      day: '2-digit',
      month: 'short',
      weekday: 'short'
    }).format(now).replace(',', '');
    const timeStr = new Intl.DateTimeFormat('tr-TR', {
      timeZone: 'Europe/Istanbul',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    }).format(now);
    el.innerText = `${dateStr} · ${timeStr}`;
  } catch (_) {
    el.innerText = now.toLocaleDateString('tr-TR');
  }
}


function openChangePinModal() {
  const m = document.getElementById('change-pin-modal');
  const msgEl = document.getElementById('change-pin-msg');
  ['pin-old-input', 'pin-new-input', 'pin-confirm-input'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  if (msgEl) msgEl.classList.add('hidden');
  if (m) m.classList.remove('hidden');
  const oldInp = document.getElementById('pin-old-input');
  if (oldInp) oldInp.focus();
}

function closeChangePinModal() {
  const m = document.getElementById('change-pin-modal');
  if (m) m.classList.add('hidden');
}

async function handleChangePin(e) {
  e.preventDefault();
  const old_pin = (document.getElementById('pin-old-input')?.value || '').trim();
  const new_pin = (document.getElementById('pin-new-input')?.value || '').trim();
  const confirm_pin = (document.getElementById('pin-confirm-input')?.value || '').trim();
  const msgEl = document.getElementById('change-pin-msg');

  const showErr = (txt) => {
    if (!msgEl) return;
    msgEl.className = 'text-xs font-semibold text-rose-400';
    msgEl.innerText = txt;
    msgEl.classList.remove('hidden');
  };

  if (new_pin.length < 6) {
    showErr('Yeni şifre güvenlik için en az 6 karakter olmalıdır.');
    return;
  }
  if (new_pin !== confirm_pin) {
    showErr('Yeni şifreler birbiriyle uyuşmuyor!');
    return;
  }

  try {
    const res = await fetch('/api/auth/change-pin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_pin, new_pin })
    }).then(r => r.json());

    if (res.status === 'ok') {
      closeChangePinModal();
      showToast(res.message || 'Şifre başarıyla güncellendi.');
    } else {
      showErr(res.message || 'Şifre güncellenemedi.');
    }
  } catch (err) {
    showErr('Bağlantı hatası: ' + err);
  }
}

let netWorthChartInstance = null;
let categoryDonutInstance = null;

function applyPrivacyUI(enabled) {
  document.body.classList.toggle('privacy-mode', enabled);
  const icon = document.getElementById('privacy-toggle-icon');
  const btn = document.getElementById('privacy-toggle-btn');
  if (icon) icon.className = enabled ? 'fa-solid fa-eye-slash text-xs text-indigo-400' : 'fa-solid fa-eye text-xs';
  if (btn) btn.classList.toggle('border-indigo-500/40', enabled);
  if (typeof updateBrowserTabTitle === 'function') updateBrowserTabTitle();
}

function initPrivacyMode() {
  applyPrivacyUI(localStorage.getItem('finans_privacy_mode') === '1');
}

function togglePrivacyMode() {
  const next = !document.body.classList.contains('privacy-mode');
  localStorage.setItem('finans_privacy_mode', next ? '1' : '0');
  applyPrivacyUI(next);
  showToast(next ? 'Gizlilik modu açık (H ile kapatabilirsin)' : 'Gizlilik modu kapatıldı');
}


function syncDockLiquidPill(activeBtn) {
  const pill = document.getElementById('dock-liquid-pill');
  const btn = activeBtn || document.querySelector('#sidebar-right nav .sidebar-btn.active');
  if (!pill || !btn) return;
  pill.style.opacity = '1';
  pill.style.height = `${btn.offsetHeight}px`;
  pill.style.transform = `translate3d(0, ${btn.offsetTop}px, 0)`;
}

window.addEventListener('resize', () => {
  requestAnimationFrame(() => syncDockLiquidPill());
}, { passive: true });

// 2. Akıcı Sayaç Geçişi (GPU rAF Count-Up Tween)
const __animatedNumState = new Map();
function animateNumberValue(el, targetNum, formatter, key, useHtml = false) {
  if (!el || !Number.isFinite(targetNum)) return;
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const state = __animatedNumState.get(key) || { val: null, raf: null };
  if (state.raf) {
    cancelAnimationFrame(state.raf);
    state.raf = null;
  }
  if (state.val === null || reduced || Math.abs(state.val - targetNum) < 0.005) {
    state.val = targetNum;
    __animatedNumState.set(key, state);
    if (useHtml) el.innerHTML = formatter(targetNum);
    else el.innerText = formatter(targetNum);
    return;
  }
  const startVal = state.val;
  const delta = targetNum - startVal;
  const duration = 360;
  const t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    const cur = startVal + delta * eased;
    state.val = cur;
    if (useHtml) el.innerHTML = formatter(cur);
    else el.innerText = formatter(cur);
    if (t < 1) {
      state.raf = requestAnimationFrame(step);
    } else {
      state.val = targetNum;
      state.raf = null;
    }
  };
  __animatedNumState.set(key, state);
  state.raf = requestAnimationFrame(step);
}

// 3. Piyasa Kapsülleri İçin Gerçek 14G TEFAS Sparkline SVG Oluşturucu & Detaylı Hover Yardımcıları
const __sparkBuffers = {};
function updateTickerSparklines(resMarket) {
  if (!resMarket) return;
  const funds = resMarket.funds || {};
  const macro = resMarket.macro || {};
  const specs = [
    { id: 'zbj', chgEl: 'chg-zbj', fund: funds.ZBJ, series: null, price: funds.ZBJ && funds.ZBJ.price, pct: funds.ZBJ && funds.ZBJ.daily_yield_pct, upCol: '#34d399', dnCol: '#fb7185' },
    { id: 'tzl', chgEl: 'chg-tzl', fund: funds.TZL, series: null, price: funds.TZL && funds.TZL.price, pct: funds.TZL && funds.TZL.daily_yield_pct, upCol: '#2dd4bf', dnCol: '#fb7185' },
    { id: 'tca', chgEl: 'chg-tca', fund: funds.TCA, series: null, price: funds.TCA && funds.TCA.price, pct: funds.TCA && funds.TCA.daily_yield_pct, targetLbl: '%35', upCol: '#fbbf24', dnCol: '#fb7185' },
    { id: 'aft', chgEl: 'chg-aft', fund: funds.AFT, series: null, price: funds.AFT && funds.AFT.price, pct: funds.AFT && funds.AFT.daily_yield_pct, targetLbl: '%20', upCol: '#38bdf8', dnCol: '#fb7185' },
    { id: 'gold', chgEl: null, fund: null, series: macro.gram_altin_series, price: macro.gram_altin_try, pct: (macro.gram_altin_chg_pct != null ? macro.gram_altin_chg_pct : (funds.TCA && funds.TCA.daily_yield_pct)), upCol: '#facc15', dnCol: '#fb7185' },
    { id: 'usd', chgEl: 'chg-usd', fund: null, series: macro.usd_series, price: macro.usd_try, pct: (macro.usd_chg_pct != null ? macro.usd_chg_pct : 0), upCol: '#818cf8', dnCol: '#fb7185' },
  ];

  specs.forEach(s => {
    const val = Number(s.price || 0);
    const pct = Number(s.pct || 0);
    if (val <= 0) return;

    const rawSeries = (s.fund && Array.isArray(s.fund.sparkline_points) && s.fund.sparkline_points.length >= 3)
      ? s.fund.sparkline_points
      : (Array.isArray(s.series) && s.series.length >= 3 ? s.series : null);
    const realSeries = rawSeries
      ? rawSeries.map(pt => Number(pt.price || 0)).filter(n => n > 0)
      : null;

    if (realSeries && realSeries.length >= 3) {
      __sparkBuffers[s.id] = realSeries;
    } else if (!__sparkBuffers[s.id]) {
      const slope = Math.max(-0.04, Math.min(0.04, pct / 100));
      const base = val / (1 + slope);
      __sparkBuffers[s.id] = [base, base * (1 + slope * 0.25), base * (1 + slope * 0.55), base * (1 + slope * 0.8), val];
    } else {
      const buf = __sparkBuffers[s.id];
      if (Math.abs(buf[buf.length - 1] - val) > 1e-7) {
        buf.push(val);
        if (buf.length > 14) buf.shift();
      }
    }

    const svg = document.getElementById(`spark-${s.id}`);
    if (svg) {
      const pts = __sparkBuffers[s.id];
      const minV = Math.min(...pts);
      const maxV = Math.max(...pts);
      const span = Math.max(1e-9, maxV - minV);
      const w = 52;
      const h = 15;
      let lastX = 53, lastY = 9;
      const polyPts = pts.map((p, idx) => {
        const x = 2 + (idx / Math.max(1, pts.length - 1)) * w;
        const y = 2.5 + (1 - ((p - minV) / span)) * h;
        lastX = x;
        lastY = y;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      const strokeCol = pct < 0 ? s.dnCol : s.upCol;
      const poly = svg.querySelector('polyline');
      if (poly) {
        poly.setAttribute('points', polyPts);
        poly.setAttribute('stroke', strokeCol);
      }
      const dot = svg.querySelector('circle');
      if (dot) {
        dot.setAttribute('cx', lastX.toFixed(1));
        dot.setAttribute('cy', lastY.toFixed(1));
        dot.setAttribute('fill', strokeCol);
      }
    }

    if (s.chgEl) {
      const cEl = document.getElementById(s.chgEl);
      if (cEl) {
        const sign = pct >= 0 ? '+' : '';
        const cls = pct >= 0 ? 'text-emerald-400 font-mono font-semibold' : 'text-rose-400 font-mono font-semibold';
        if (s.targetLbl) {
          cEl.innerText = `${sign}%${pct.toFixed(2)} · ${s.targetLbl}`;
          cEl.className = cls + ' text-xs';
        } else {
          cEl.innerText = `${sign}%${pct.toFixed(2)}`;
          cEl.className = cls;
        }
      }
    }
  });
}

function bindRichHoverPopovers() {
  if (window.__hoverPopoversBound) return;
  window.__hoverPopoversBound = true;

  let tip = document.getElementById('rich-hover-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'rich-hover-tip';
    tip.className = 'fixed z-[120] pointer-events-none hidden w-[340px] max-w-[92vw] rounded-2xl bg-[#070b16]/96 border border-indigo-500/40 px-4 py-3.5 text-xs text-slate-200 shadow-2xl font-sans leading-relaxed';
    document.body.appendChild(tip);
  }

  const showTip = (e, htmlContent) => {
    if (!htmlContent) return;
    tip.innerHTML = htmlContent;
    tip.classList.remove('hidden');
    const pad = 14;
    const rect = tip.getBoundingClientRect();
    const cx = e.clientX || (e.currentTarget ? e.currentTarget.getBoundingClientRect().left + 40 : 100);
    const cy = e.clientY || (e.currentTarget ? e.currentTarget.getBoundingClientRect().bottom : 100);
    let left = cx + 14;
    let top = cy + 14;
    if (left + rect.width > window.innerWidth - pad) left = Math.max(pad, cx - rect.width - 14);
    if (top + rect.height > window.innerHeight - pad) top = Math.max(pad, cy - rect.height - 14);
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  };

  const hideTip = () => tip.classList.add('hidden');

  const roleHints = {
    ZBJ: 'Ziraat Başak (2958): Boştaki parayı her gece bileşik nemayla büyütür.',
    TZL: 'Ziraat Likit PPF: Acil nakit ihtiyacında T+0 aynı gün nakde döner.',
    TCA: 'Ziraat Altın Katılım: Makas ödemeden gram/ons altın yükselişine eşlik eder.',
    AFT: 'Ak Portföy Yeni Teknolojiler: Nasdaq teknoloji devleriyle dolar bazlı büyüme sunar.',
    GOLD: 'Serbest piyasa gram altın (Ons × Dolar / 31.1035).',
    USD: 'Serbest piyasa Dolar/TL kuru (AFT & Altın değerlemesini etkiler).'
  };

  const renderHoverChart = (rawPts, accentHex, priceDigits, idKey) => {
    const clean = (rawPts || [])
      .map(p => (typeof p === 'number' ? { date: '', price: p } : { date: String(p.date || ''), price: Number(p.price || 0) }))
      .filter(p => p.price > 0);
    if (clean.length < 2) return '';
    const vals = clean.map(p => p.price);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const span = Math.max(1e-9, maxV - minV);
    const firstV = vals[0];
    const lastV = vals[vals.length - 1];
    const rangePct = firstV > 0 ? ((lastV - firstV) / firstV) * 100 : 0;
    const col = rangePct < 0 ? '#fb7185' : accentHex;
    const W = 280, H = 64, px = 4, py = 6;
    const coords = clean.map((p, idx) => {
      const x = px + (idx / Math.max(1, clean.length - 1)) * (W - px * 2);
      const y = py + (1 - ((p.price - minV) / span)) * (H - py * 2);
      return { x, y };
    });
    const polyPts = coords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
    const areaPts = `${coords[0].x.toFixed(1)},${(H - py + 3).toFixed(1)} ${polyPts} ${coords[coords.length - 1].x.toFixed(1)},${(H - py + 3).toFixed(1)}`;
    const lastC = coords[coords.length - 1];
    const baseY = (py + (1 - ((firstV - minV) / span)) * (H - py * 2)).toFixed(1);
    const gradId = `tipG_${String(idKey || 'x').replace(/[^a-zA-Z0-9]/g, '')}`;
    const fmtP = (v) => v >= 100 ? `₺${v.toLocaleString('tr-TR', { maximumFractionDigits: priceDigits })}` : `₺${v.toFixed(priceDigits)}`;
    const fmtDate = (d) => d && d.length >= 10 ? `${d.slice(8, 10)}.${d.slice(5, 7)}` : '';
    const dStart = fmtDate(clean[0].date) || `${clean.length}g önce`;
    const dEnd = fmtDate(clean[clean.length - 1].date) || 'Bugün';
    const badgeCls = rangePct >= 0 ? 'text-emerald-300 bg-emerald-500/15 border-emerald-500/25' : 'text-rose-300 bg-rose-500/15 border-rose-500/25';

    return `
      <div class="my-2 rounded-xl bg-white/[0.03] border border-white/[0.08] p-2.5">
        <div class="flex items-center justify-between text-xs font-mono text-slate-300 mb-1.5">
          <span>${clean.length}G Zirve: <b class="text-white">${fmtP(maxV)}</b></span>
          <span class="px-2 py-0.5 rounded border ${badgeCls} font-bold">${rangePct >= 0 ? '+' : ''}%${rangePct.toFixed(2)} (${clean.length}G)</span>
        </div>
        <svg width="100%" height="64" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="overflow-visible">
          <defs>
            <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="${col}" stop-opacity="0.34"/>
              <stop offset="100%" stop-color="${col}" stop-opacity="0.01"/>
            </linearGradient>
          </defs>
          <line x1="${px}" y1="${baseY}" x2="${W - px}" y2="${baseY}" stroke="rgba(148,163,184,0.22)" stroke-width="1" stroke-dasharray="3,3"/>
          <polygon points="${areaPts}" fill="url(#${gradId})"/>
          <polyline fill="none" stroke="${col}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${polyPts}"/>
          <circle cx="${lastC.x.toFixed(1)}" cy="${lastC.y.toFixed(1)}" r="3.2" fill="${col}" stroke="#070b16" stroke-width="1.5"/>
        </svg>
        <div class="flex items-center justify-between text-xs font-mono text-slate-300 mt-1">
          <span>${escapeHtml(dStart)}</span>
          <span>Dip: <b class="text-white">${fmtP(minV)}</b></span>
          <span>${escapeHtml(dEnd)}</span>
        </div>
      </div>
    `;
  };

  const getHoldingSummary = (code, dayPct) => {
    const gp = globalPortfolio || {};
    const total = Math.max(1, Number(gp.total_value_try || 0));
    const pf = (gp.funds || []).find(item => String(item.code || '').toUpperCase() === code);
    const pend = (gp.pending_orders || [])
      .filter(po => String(po.code || '').toUpperCase() === code)
      .reduce((acc, po) => acc + Number(po.amount_try || 0), 0);
    const curVal = Number(pf ? pf.current_value : 0);
    const shares = Number(pf ? pf.shares : 0);
    const weight = ((curVal + pend) / total) * 100;
    const estDayTl = curVal * (Number(dayPct || 0) / 100);
    if (curVal <= 0 && pend <= 0) {
      return `<div class="mt-2 px-2.5 py-1.5 rounded-lg bg-white/[0.03] border border-white/[0.08] text-xs text-slate-300">Senin Portföyün: <b class="text-white">Pozisyon yok</b></div>`;
    }
    const daySign = estDayTl >= 0 ? '+' : '-';
    const dayCls = estDayTl >= 0 ? 'text-emerald-300' : 'text-rose-300';
    const pendHtml = pend > 0 ? `<div class="text-amber-300 mt-1">⏳ Valörde bekleyen: ₺${pend.toLocaleString('tr-TR', { minimumFractionDigits: 2 })}</div>` : '';
    return `
      <div class="mt-2 px-2.5 py-2 rounded-lg bg-indigo-500/10 border border-indigo-400/20 text-xs font-mono">
        <div class="flex items-center justify-between text-slate-200">
          <span>Portföyün (${shares.toLocaleString('tr-TR', { maximumFractionDigits: 0 })} pay)</span>
          <b class="text-white">₺${curVal.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (%${weight.toFixed(1)})</b>
        </div>
        <div class="flex items-center justify-between mt-1 text-slate-300">
          <span>Bugünkü Tahmini Etki</span>
          <b class="${dayCls}">${daySign}₺${Math.abs(estDayTl).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b>
        </div>
        ${pendHtml}
      </div>
    `;
  };

  const accentColors = { ZBJ: '#34d399', TZL: '#2dd4bf', TCA: '#fbbf24', AFT: '#38bdf8', GOLD: '#facc15', USD: '#818cf8' };

  document.querySelectorAll('[data-ticker]').forEach(card => {
    const buildTickerHtml = () => {
      const code = card.dataset.ticker;
      const m = globalMarket || {};
      const f = (m.funds || {})[code];
      const macro = m.macro || {};
      const fmtPct = (v) => {
        const n = Number(v || 0);
        const cls = n >= 0 ? 'text-emerald-400' : 'text-rose-400';
        return `<span class="${cls} font-mono font-bold">${n >= 0 ? '+' : ''}%${n.toFixed(2)}</span>`;
      };
      if (f) {
        const dd = Number(f.drawdown_from_high_pct || 0);
        const rank = f.category_rank && f.category_total ? ` • Sıra: #${f.category_rank}/${f.category_total}` : '';
        const pts = (Array.isArray(f.sparkline_points) && f.sparkline_points.length >= 2) ? f.sparkline_points : (__sparkBuffers[code.toLowerCase()] || []);
        const chartHtml = renderHoverChart(pts, accentColors[code] || '#818cf8', 4, code);
        const holdHtml = getHoldingSummary(code, f.daily_yield_pct);
        return `
          <div class="font-bold text-white text-sm flex items-center justify-between gap-2">
            <span>${escapeHtml(code)} · Canlı TEFAS</span>
            <span class="font-mono text-indigo-300">₺${Number(f.price || 0).toFixed(4)}</span>
          </div>
          ${chartHtml}
          <div class="font-mono text-xs space-y-1 text-slate-200">
            <div>1G: ${fmtPct(f.daily_yield_pct)} · 7G: ${fmtPct(f.weekly_yield_pct)} · 30G: ${fmtPct(f.monthly_yield_pct)}</div>
            <div>30G Zirve: ₺${Number(f.high_30d || 0).toFixed(4)} · Dip: ₺${Number(f.low_30d || 0).toFixed(4)}</div>
            <div>Zirveden Fark: <span class="text-amber-300">${dd >= 0 ? '+' : ''}%${dd.toFixed(2)}</span>${escapeHtml(rank)}</div>
          </div>
          ${holdHtml}
          <div class="mt-2 pt-2 border-t border-white/10 text-xs text-slate-300 leading-relaxed">${escapeHtml(roleHints[code] || '')}</div>
        `;
      }
      if (code === 'GOLD') {
        const gPts = (Array.isArray(macro.gram_altin_series) && macro.gram_altin_series.length >= 2) ? macro.gram_altin_series : (__sparkBuffers.gold || []);
        const chartHtml = renderHoverChart(gPts, accentColors.GOLD, 0, 'GOLD');
        const gChg = macro.gram_altin_chg_pct != null ? macro.gram_altin_chg_pct : macro.ons_chg_pct;
        const gWk = macro.gram_altin_weekly_pct != null ? macro.gram_altin_weekly_pct : macro.ons_weekly_pct;
        const tcaHold = ((globalPortfolio && globalPortfolio.funds) || []).find(x => x.code === 'TCA');
        const tcaNote = tcaHold && tcaHold.current_value > 0 ? `<div class="mt-1.5 px-2.5 py-1.5 rounded-lg bg-amber-500/10 border border-amber-400/20 text-xs font-mono text-amber-200">TCA Altın Pozisyonun: ₺${Number(tcaHold.current_value).toLocaleString('tr-TR', { minimumFractionDigits: 2 })}</div>` : '';
        return `
          <div class="font-bold text-amber-300 text-sm flex items-center justify-between gap-2">
            <span>Gram Altın · Canlı</span>
            <span class="font-mono text-white">₺${Number(macro.gram_altin_try || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 })}</span>
          </div>
          ${chartHtml}
          <div class="font-mono text-xs space-y-1 text-slate-200">
            <div>1G: ${fmtPct(gChg)} · 7G: ${fmtPct(gWk)}</div>
            <div>Ons Altın: <b class="text-white">$${Number(macro.ons_altin_usd || 0).toLocaleString('tr-TR')}</b> · 1G: ${fmtPct(macro.ons_chg_pct)}</div>
          </div>
          ${tcaNote}
          <div class="mt-2 pt-2 border-t border-white/10 text-xs text-slate-300 leading-relaxed">${escapeHtml(roleHints.GOLD)}</div>
        `;
      }
      const uPts = (Array.isArray(macro.usd_series) && macro.usd_series.length >= 2) ? macro.usd_series : (__sparkBuffers.usd || []);
      const chartHtml = renderHoverChart(uPts, accentColors.USD, 2, 'USD');
      const aftHold = ((globalPortfolio && globalPortfolio.funds) || []).find(x => x.code === 'AFT');
      const aftNote = aftHold && aftHold.current_value > 0 ? `<div class="mt-1.5 px-2.5 py-1.5 rounded-lg bg-sky-500/10 border border-sky-400/20 text-xs font-mono text-sky-200">Döviz Endeksli AFT Fonun: ₺${Number(aftHold.current_value).toLocaleString('tr-TR', { minimumFractionDigits: 2 })}</div>` : '';
      return `
        <div class="font-bold text-indigo-300 text-sm flex items-center justify-between gap-2">
          <span>USD / TRY · Canlı</span>
          <span class="font-mono text-white">₺${Number(macro.usd_try || 0).toFixed(2)}</span>
        </div>
        ${chartHtml}
        <div class="font-mono text-xs space-y-1 text-slate-200">
          <div>1G: ${fmtPct(macro.usd_chg_pct)} · 7G: ${fmtPct(macro.usd_weekly_pct)}</div>
          <div>EUR/TRY: <b class="text-white">₺${Number(macro.eur_try || 0).toFixed(2)}</b> · 1G: ${fmtPct(macro.eur_chg_pct)}</div>
        </div>
        ${aftNote}
        <div class="mt-2 pt-2 border-t border-white/10 text-xs text-slate-300 leading-relaxed">${escapeHtml(roleHints.USD)}</div>
      `;
    };
    card.addEventListener('mouseenter', (e) => showTip(e, buildTickerHtml()));
    card.addEventListener('mousemove', (e) => showTip(e, buildTickerHtml()));
    card.addEventListener('mouseleave', hideTip);
    card.addEventListener('click', (e) => {
      e.stopPropagation();
      showTip(e, buildTickerHtml());
    });
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('[data-ticker], [data-alloc-bucket], [data-chart-help]')) hideTip();
  });

  document.querySelectorAll('[data-alloc-bucket]').forEach(seg => {
    const buildAllocHtml = () => {
      const b = seg.dataset.allocBucket;
      const gp = globalPortfolio || {};
      const total = Math.max(1, Number(gp.total_value_try || 0));
      const cash = Number(gp.cash_try || 0);
      const fMap = {};
      (gp.funds || []).forEach(f => { fMap[f.code] = Number(f.current_value || 0); });
      const pendMap = {};
      (gp.pending_orders || []).forEach(po => {
        const c = String(po.code || '').toUpperCase();
        pendMap[c] = (pendMap[c] || 0) + Number(po.amount_try || 0);
      });
      let title = 'Vadesiz Nakit', val = cash, pend = 0, note = 'Anlık harcanabilir & fon alımına hazır bakiye.';
      if (b === 'PPF') {
        title = 'ZBJ / TZL (Günlük Faiz PPF)';
        val = (fMap.ZBJ || 0) + (fMap.TZL || 0) + (pendMap.ZBJ || 0) + (pendMap.TZL || 0);
        pend = (pendMap.ZBJ || 0) + (pendMap.TZL || 0);
        const dayNema = val * Number(gp.ppf_daily_rate || 0.0011);
        note = `Günlük beklenen nema: +₺${dayNema.toFixed(2)}/gün`;
      } else if (b === 'TCA') {
        title = 'TCA (Altın Katılım Fonu)';
        val = (fMap.TCA || 0) + (pendMap.TCA || 0);
        pend = pendMap.TCA || 0;
        note = 'Makassız altın & enflasyon kalkanı.';
      } else if (b === 'AFT') {
        title = 'AFT (Teknoloji Fonu)';
        val = (fMap.AFT || 0) + (pendMap.AFT || 0);
        pend = pendMap.AFT || 0;
        note = 'Yabancı teknoloji & döviz bazlı büyüme.';
      }
      const pct = ((val / total) * 100).toFixed(1);
      const pendLine = pend > 0 ? `<div class="text-amber-300">⏳ Valörde Bekleyen: ₺${pend.toLocaleString('tr-TR', {minimumFractionDigits: 2})}</div>` : '';
      return `
        <div class="font-bold text-white text-sm mb-1">${escapeHtml(title)}</div>
        <div class="font-mono text-xs text-emerald-300 font-semibold">Tutar: ₺${val.toLocaleString('tr-TR', {minimumFractionDigits: 2})} (%${pct})</div>
        ${pendLine}
        <div class="text-xs text-slate-300 mt-1.5 leading-relaxed">${escapeHtml(note)}</div>
      `;
    };
    seg.addEventListener('mousemove', (e) => showTip(e, buildAllocHtml()));
    seg.addEventListener('mouseleave', hideTip);
  });

  document.querySelectorAll('[data-chart-help]').forEach(btn => {
    const htmlHelp = `<div class="font-semibold text-indigo-300 mb-0.5 flex items-center gap-1.5"><i class="fa-solid fa-circle-info"></i><span>Grafik Okuma İpucu</span></div><div class="text-slate-200">${escapeHtml(btn.dataset.chartHelp)}</div>`;
    btn.addEventListener('mousemove', (e) => showTip(e, htmlHelp));
    btn.addEventListener('mouseenter', (e) => showTip(e, htmlHelp));
    btn.addEventListener('mouseleave', hideTip);
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      showTip(e, htmlHelp);
      setTimeout(hideTip, 4500);
    });
  });
}

// 5. Hedef Kilometre Taşı Mikro Geri Bildirimi
function checkGoalMilestones(total, pct) {
  const markers = [
    { id: 'ms-100', thr: 100000 },
    { id: 'ms-250', thr: 250000 },
    { id: 'ms-450', thr: 450000 },
    { id: 'ms-650', thr: 650000 },
  ];
  markers.forEach(m => {
    const el = document.getElementById(m.id);
    if (!el) return;
    if (total >= m.thr) {
      el.classList.add('text-emerald-300', 'bg-emerald-500/15', 'font-semibold');
    } else {
      el.classList.remove('text-emerald-300', 'bg-emerald-500/15', 'font-semibold');
    }
  });

  const tiers = [10, 25, 50, 75, 100];
  let reachedTier = 0;
  tiers.forEach(t => { if (pct >= t) reachedTier = t; });
  const prevSaved = localStorage.getItem('fin_goal_tier');
  if (reachedTier > 0) {
    const shimmer = document.getElementById('goal-shimmer');
    if (shimmer && !window.__goalShimmerPlayed) {
      window.__goalShimmerPlayed = true;
      shimmer.classList.add('goal-shimmer-active');
    }
    if (prevSaved !== null && Number(prevSaved) < reachedTier) {
      showToast(`Kilometre Taşı Aşıldı: Mezuniyet hedefin %${reachedTier} seviyesine ulaştı!`);
      if (shimmer) {
        shimmer.classList.remove('goal-shimmer-active');
        void shimmer.offsetWidth;
        shimmer.classList.add('goal-shimmer-active');
      }
    }
    localStorage.setItem('fin_goal_tier', String(reachedTier));
  }
}

// Hafifletilmiş HUD Dock Yardımcıları (Zero Main-Thread Scroll/Mousemove Overhead)
(function initDockHoverRefresh() {
  const sidebar = document.getElementById('sidebar-right');
  if (sidebar) {
    sidebar.addEventListener('transitionend', () => {
      if (typeof syncDockLiquidPill === 'function') syncDockLiquidPill();
    }, { passive: true });
  }
})();

// Dünyadan Dünyaya Yolculuk & Sıfır-Kasma Gökyüzü Sahnesi (Pure GPU Transform + Opacity, 0% Boşta Döngü)
(function initPlanetaryWorldTravel() {
  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const domeA = document.getElementById('sky-dome-a');
  const domeB = document.getElementById('sky-dome-b');
  const stageA = document.getElementById('planet-stage-a');
  const stageB = document.getElementById('planet-stage-b');
  const skyCanvas = document.getElementById('sky-canvas');

  const WORLD_THEMES = {
    terminal: {
      dome: 'radial-gradient(ellipse 75% 55% at 15% 0%, rgba(99, 102, 241, 0.14) 0%, transparent 60%), radial-gradient(ellipse 68% 52% at 86% 14%, rgba(147, 51, 234, 0.13) 0%, transparent 58%), linear-gradient(180deg, #010207 0%, #030510 55%, #050817 100%)',
      halo: 'radial-gradient(circle, rgba(139, 92, 246, 0.24) 0%, rgba(99, 102, 241, 0.09) 44%, transparent 72%)',
      orb: 'radial-gradient(circle at 25% 22%, rgba(221, 214, 254, 0.86) 0%, rgba(124, 58, 237, 0.68) 26%, rgba(49, 46, 129, 0.56) 52%, rgba(7, 8, 22, 0.96) 78%, rgba(1, 2, 7, 1) 100%)',
      rim: 'rgba(196, 181, 253, 0.36)',
      bandA: 'rgba(221, 214, 254, 0.11)',
      bandB: 'rgba(139, 92, 246, 0.15)',
      tilt: '-14deg',
      spin: '44s',
      ring: 'radial-gradient(ellipse at center, transparent 46%, rgba(167, 139, 250, 0.30) 51%, transparent 54.5%, rgba(129, 140, 248, 0.20) 57%, rgba(99, 102, 241, 0.08) 65%, transparent 72%)',
      hasRing: true,
      pillA: 'rgba(99, 102, 241, 0.24)',
      pillB: 'rgba(168, 85, 247, 0.12)',
      pillBorder: 'rgba(129, 140, 248, 0.42)',
      pillGlow: 'rgba(99, 102, 241, 0.36)'
    },
    council: {
      dome: 'radial-gradient(ellipse 78% 55% at 22% 4%, rgba(14, 165, 233, 0.13) 0%, transparent 60%), radial-gradient(ellipse 65% 50% at 82% 16%, rgba(20, 184, 166, 0.10) 0%, transparent 58%), linear-gradient(180deg, #010308 0%, #020813 55%, #040d1b 100%)',
      halo: 'radial-gradient(circle, rgba(56, 189, 248, 0.22) 0%, rgba(20, 184, 166, 0.09) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 26% 22%, rgba(224, 242, 254, 0.85) 0%, rgba(14, 165, 233, 0.64) 26%, rgba(15, 118, 110, 0.50) 52%, rgba(4, 14, 26, 0.96) 78%, rgba(1, 3, 8, 1) 100%)',
      rim: 'rgba(125, 211, 252, 0.34)',
      bandA: 'rgba(224, 242, 254, 0.12)',
      bandB: 'rgba(45, 212, 191, 0.13)',
      tilt: '10deg',
      spin: '38s',
      ring: 'none',
      hasRing: false,
      pillA: 'rgba(56, 189, 248, 0.24)',
      pillB: 'rgba(45, 212, 191, 0.12)',
      pillBorder: 'rgba(56, 189, 248, 0.42)',
      pillGlow: 'rgba(56, 189, 248, 0.34)'
    },
    ledger: {
      dome: 'radial-gradient(ellipse 76% 54% at 18% 0%, rgba(59, 130, 246, 0.13) 0%, transparent 60%), radial-gradient(ellipse 62% 50% at 84% 12%, rgba(30, 58, 138, 0.16) 0%, transparent 58%), linear-gradient(180deg, #010309 0%, #030714 55%, #050c20 100%)',
      halo: 'radial-gradient(circle, rgba(59, 130, 246, 0.21) 0%, rgba(30, 64, 175, 0.09) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 25% 22%, rgba(219, 234, 254, 0.84) 0%, rgba(37, 99, 235, 0.64) 26%, rgba(23, 37, 84, 0.54) 54%, rgba(5, 10, 26, 0.97) 78%, rgba(1, 3, 9, 1) 100%)',
      rim: 'rgba(147, 197, 253, 0.34)',
      bandA: 'rgba(219, 234, 254, 0.10)',
      bandB: 'rgba(59, 130, 246, 0.14)',
      tilt: '-9deg',
      spin: '48s',
      ring: 'none',
      hasRing: false,
      pillA: 'rgba(59, 130, 246, 0.24)',
      pillB: 'rgba(34, 211, 238, 0.10)',
      pillBorder: 'rgba(96, 165, 250, 0.42)',
      pillGlow: 'rgba(59, 130, 246, 0.34)'
    },
    goals: {
      dome: 'radial-gradient(ellipse 74% 52% at 16% 0%, rgba(109, 40, 217, 0.13) 0%, transparent 58%), radial-gradient(ellipse 80% 50% at 84% 18%, rgba(217, 119, 6, 0.12) 0%, transparent 60%), linear-gradient(180deg, #020208 0%, #070512 55%, #0c0716 100%)',
      halo: 'radial-gradient(circle, rgba(245, 158, 11, 0.22) 0%, rgba(190, 24, 93, 0.09) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 25% 22%, rgba(254, 249, 195, 0.86) 0%, rgba(217, 119, 6, 0.66) 26%, rgba(131, 24, 67, 0.52) 54%, rgba(16, 6, 22, 0.97) 78%, rgba(2, 2, 8, 1) 100%)',
      rim: 'rgba(253, 224, 71, 0.34)',
      bandA: 'rgba(254, 240, 138, 0.11)',
      bandB: 'rgba(245, 158, 11, 0.16)',
      tilt: '-16deg',
      spin: '40s',
      ring: 'radial-gradient(ellipse at center, transparent 46%, rgba(251, 191, 36, 0.30) 51%, transparent 54.5%, rgba(234, 88, 12, 0.18) 57%, rgba(217, 119, 6, 0.08) 65%, transparent 72%)',
      hasRing: true,
      pillA: 'rgba(244, 63, 94, 0.22)',
      pillB: 'rgba(245, 158, 11, 0.12)',
      pillBorder: 'rgba(251, 113, 133, 0.40)',
      pillGlow: 'rgba(244, 63, 94, 0.32)'
    },
    splitter: {
      dome: 'radial-gradient(ellipse 78% 54% at 14% 2%, rgba(16, 185, 129, 0.12) 0%, transparent 60%), radial-gradient(ellipse 68% 50% at 86% 12%, rgba(15, 118, 110, 0.12) 0%, transparent 58%), linear-gradient(180deg, #010508 0%, #030d12 55%, #05131a 100%)',
      halo: 'radial-gradient(circle, rgba(16, 185, 129, 0.21) 0%, rgba(13, 148, 136, 0.09) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 26% 22%, rgba(209, 250, 229, 0.84) 0%, rgba(5, 150, 105, 0.64) 28%, rgba(17, 94, 89, 0.52) 54%, rgba(4, 18, 20, 0.97) 78%, rgba(1, 5, 8, 1) 100%)',
      rim: 'rgba(110, 231, 183, 0.34)',
      bandA: 'rgba(167, 243, 208, 0.10)',
      bandB: 'rgba(16, 185, 129, 0.14)',
      tilt: '12deg',
      spin: '42s',
      ring: 'none',
      hasRing: false,
      pillA: 'rgba(16, 185, 129, 0.22)',
      pillB: 'rgba(20, 184, 166, 0.12)',
      pillBorder: 'rgba(52, 211, 153, 0.40)',
      pillGlow: 'rgba(16, 185, 129, 0.32)'
    },
    radar: {
      dome: 'radial-gradient(ellipse 76% 52% at 18% 0%, rgba(126, 34, 206, 0.14) 0%, transparent 60%), radial-gradient(ellipse 70% 50% at 86% 14%, rgba(225, 29, 72, 0.12) 0%, transparent 58%), linear-gradient(180deg, #030208 0%, #090512 55%, #100718 100%)',
      halo: 'radial-gradient(circle, rgba(225, 29, 72, 0.23) 0%, rgba(147, 51, 234, 0.10) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 25% 22%, rgba(255, 228, 230, 0.84) 0%, rgba(225, 29, 72, 0.64) 26%, rgba(107, 33, 168, 0.54) 54%, rgba(18, 5, 24, 0.97) 78%, rgba(3, 2, 8, 1) 100%)',
      rim: 'rgba(253, 164, 175, 0.34)',
      bandA: 'rgba(254, 205, 211, 0.10)',
      bandB: 'rgba(244, 63, 94, 0.16)',
      tilt: '-15deg',
      spin: '36s',
      ring: 'radial-gradient(ellipse at center, transparent 46%, rgba(244, 63, 94, 0.29) 51%, transparent 54.5%, rgba(168, 85, 247, 0.18) 57%, rgba(147, 51, 234, 0.08) 65%, transparent 72%)',
      hasRing: true,
      pillA: 'rgba(168, 85, 247, 0.24)',
      pillB: 'rgba(244, 63, 94, 0.12)',
      pillBorder: 'rgba(192, 132, 252, 0.42)',
      pillGlow: 'rgba(168, 85, 247, 0.34)'
    },
    tools: {
      dome: 'radial-gradient(ellipse 78% 54% at 15% 0%, rgba(14, 165, 233, 0.13) 0%, transparent 60%), radial-gradient(ellipse 64% 50% at 86% 12%, rgba(79, 70, 229, 0.13) 0%, transparent 58%), linear-gradient(180deg, #01040a 0%, #040a16 55%, #061020 100%)',
      halo: 'radial-gradient(circle, rgba(45, 212, 191, 0.21) 0%, rgba(99, 102, 241, 0.09) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 26% 22%, rgba(204, 251, 241, 0.84) 0%, rgba(13, 148, 136, 0.64) 28%, rgba(67, 56, 202, 0.52) 54%, rgba(6, 14, 30, 0.97) 78%, rgba(1, 4, 10, 1) 100%)',
      rim: 'rgba(94, 234, 212, 0.34)',
      bandA: 'rgba(204, 251, 241, 0.10)',
      bandB: 'rgba(99, 102, 241, 0.14)',
      tilt: '14deg',
      spin: '46s',
      ring: 'none',
      hasRing: false,
      pillA: 'rgba(20, 184, 166, 0.24)',
      pillB: 'rgba(56, 189, 248, 0.12)',
      pillBorder: 'rgba(45, 212, 191, 0.42)',
      pillGlow: 'rgba(20, 184, 166, 0.34)'
    },
    telegram: {
      dome: 'radial-gradient(ellipse 78% 54% at 20% 0%, rgba(56, 189, 248, 0.15) 0%, transparent 60%), radial-gradient(ellipse 64% 50% at 86% 14%, rgba(37, 99, 235, 0.12) 0%, transparent 58%), linear-gradient(180deg, #01040b 0%, #030a18 55%, #051024 100%)',
      halo: 'radial-gradient(circle, rgba(56, 189, 248, 0.23) 0%, rgba(99, 102, 241, 0.10) 45%, transparent 72%)',
      orb: 'radial-gradient(circle at 26% 22%, rgba(224, 242, 254, 0.86) 0%, rgba(2, 132, 199, 0.66) 28%, rgba(30, 64, 175, 0.54) 54%, rgba(5, 13, 34, 0.97) 78%, rgba(1, 4, 11, 1) 100%)',
      rim: 'rgba(125, 211, 252, 0.36)',
      bandA: 'rgba(224, 242, 254, 0.11)',
      bandB: 'rgba(56, 189, 248, 0.15)',
      tilt: '-11deg',
      spin: '40s',
      ring: 'none',
      hasRing: false,
      pillA: 'rgba(56, 189, 248, 0.24)',
      pillB: 'rgba(99, 102, 241, 0.12)',
      pillBorder: 'rgba(56, 189, 248, 0.44)',
      pillGlow: 'rgba(56, 189, 248, 0.34)'
    }
  };

  const applyPlanetTheme = (stage, theme) => {
    if (!stage) return;
    stage.style.top = '';
    stage.style.right = '';
    stage.style.setProperty('--planet-rim', theme.rim || 'rgba(167, 139, 250, 0.34)');
    stage.style.setProperty('--planet-band-a', theme.bandA || 'rgba(255, 255, 255, 0.09)');
    stage.style.setProperty('--planet-band-b', theme.bandB || 'rgba(129, 140, 248, 0.13)');
    stage.style.setProperty('--planet-tilt', theme.tilt || '-12deg');
    stage.style.setProperty('--planet-spin', theme.spin || '44s');
    stage.classList.toggle('has-ring', Boolean(theme.hasRing));
    const halo = stage.querySelector('.planet-halo');
    const orb = stage.querySelector('.planet-orb');
    const ring = stage.querySelector('.planet-ring');
    const ringBack = stage.querySelector('.planet-ring-back');
    if (halo) halo.style.background = theme.halo;
    if (orb) orb.style.background = theme.orb;
    if (ring) ring.style.background = theme.ring || 'none';
    if (ringBack) ringBack.style.background = theme.ring || 'none';
  };

  // Arka plan sekme gizlenince GPU animasyonlarını sıfıra indir
  document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('pref-tab-hidden', document.hidden);
  }, { passive: true });

  // Statik Çok Katmanlı Yıldız Sahnesi (Sadece açılışta & resize'da çizilir — 0% CPU döngüsü)
  const paintStaticStars = () => {
    if (!skyCanvas) return;
    const ctx = skyCanvas.getContext('2d', { alpha: true });
    if (!ctx) return;
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const width = Math.max(320, Math.floor((window.innerWidth || 1280) * 1.24));
    const height = Math.max(320, Math.floor((window.innerHeight || 720) * 1.24));
    skyCanvas.width = Math.floor(width * dpr);
    skyCanvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // Hafif Samanyolu toz kuşağı (statik tek çizim)
    ctx.save();
    ctx.translate(width * 0.52, height * 0.44);
    ctx.rotate(-0.34);
    const mwGrad = ctx.createLinearGradient(0, -height * 0.24, 0, height * 0.24);
    mwGrad.addColorStop(0, 'rgba(99, 102, 241, 0)');
    mwGrad.addColorStop(0.5, 'rgba(139, 92, 246, 0.065)');
    mwGrad.addColorStop(1, 'rgba(56, 189, 248, 0)');
    ctx.fillStyle = mwGrad;
    ctx.fillRect(-width, -height * 0.24, width * 2, height * 0.48);
    ctx.restore();

    // Deterministik PRNG: boyut değişiminde yıldızlar zıplamaz
    let seed = 133742;
    const rand = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed / 4294967296;
    };

    const count = Math.min(220, Math.max(95, Math.floor((width * height) / 10500)));
    for (let i = 0; i < count; i++) {
      const x = rand() * width;
      const y = rand() * height;
      const mag = rand();
      const r = mag > 0.94 ? (1.35 + rand() * 0.65) : (0.45 + rand() * 0.95);
      const alpha = (0.16 + rand() * 0.62).toFixed(2);
      ctx.fillStyle = i % 7 === 0
        ? `rgba(254, 240, 138, ${alpha})`
        : (i % 5 === 0
          ? `rgba(196, 181, 253, ${alpha})`
          : (i % 3 === 0 ? `rgba(186, 230, 253, ${alpha})` : `rgba(226, 232, 240, ${alpha})`));
      if (r < 1.15) {
        ctx.fillRect(x, y, r * 1.35, r * 1.35);
      } else {
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
        if (mag > 0.965) {
          ctx.fillRect(x - r * 2.6, y - 0.35, r * 5.2, 0.7);
          ctx.fillRect(x - 0.35, y - r * 2.6, 0.7, r * 5.2);
        }
      }
    }
  };

  paintStaticStars();
  let starResizeTid = null;
  window.addEventListener('resize', () => {
    clearTimeout(starResizeTid);
    starResizeTid = setTimeout(paintStaticStars, 180);
  }, { passive: true });

  const VIEW_ORDER = ['terminal', 'ledger', 'radar', 'tools', 'council'];
  let activeBuffer = 'A';
  let currentView = 'terminal';
  let activeTravelAnims = [];
  let turnTimer = null;
  let arriveTimer = null;

  if (domeA) domeA.style.background = WORLD_THEMES.terminal.dome;
  applyPlanetTheme(stageA, WORLD_THEMES.terminal);
  if (stageA) stageA.classList.add('is-active');

  const cancelActiveTravel = () => {
    if (turnTimer) {
      clearTimeout(turnTimer);
      turnTimer = null;
    }
    if (arriveTimer) {
      clearTimeout(arriveTimer);
      arriveTimer = null;
    }
    activeTravelAnims.forEach(a => {
      try { a.cancel(); } catch (_) {}
    });
    activeTravelAnims = [];
  };

  window.stepWorld = function (delta) {
    const idx = VIEW_ORDER.indexOf(currentView);
    const base = idx >= 0 ? idx : 0;
    const nextIdx = (base + delta + VIEW_ORDER.length) % VIEW_ORDER.length;
    if (typeof switchTab === 'function') switchTab(VIEW_ORDER[nextIdx]);
  };

  const SKY_DENSITY_LEVELS = [0.85, 0.55, 1.0];
  let skyDensityIdx = 0;
  window.cycleSpaceBackdropDensity = function () {
    skyDensityIdx = (skyDensityIdx + 1) % SKY_DENSITY_LEVELS.length;
    const op = SKY_DENSITY_LEVELS[skyDensityIdx];
    if (skyCanvas) skyCanvas.style.opacity = String(op);
    const labels = ['Normal Yıldız Parlaklığı', 'Derin Sakin Uzay', 'Parlak Nebula Görünümü'];
    if (typeof showToast === 'function') showToast(`Gökyüzü Modu: ${labels[skyDensityIdx]}`, 'info');
  };

  window.setSkyMood = function (viewId) {
    const theme = WORLD_THEMES[viewId] || WORLD_THEMES.terminal;
    const root = document.documentElement.style;
    root.setProperty('--sky-pill-a', theme.pillA);
    root.setProperty('--sky-pill-b', theme.pillB);
    root.setProperty('--sky-pill-border', theme.pillBorder);
    root.setProperty('--sky-pill-glow', theme.pillGlow);

    if (viewId === currentView) return;
    const prevIdx = VIEW_ORDER.indexOf(currentView);
    const nextIdx = VIEW_ORDER.indexOf(viewId);
    const dir = (prevIdx >= 0 && nextIdx >= 0 && nextIdx < prevIdx) ? -1 : 1;
    currentView = viewId;
    cancelActiveTravel();

    const outDome = activeBuffer === 'A' ? domeA : domeB;
    const inDome = activeBuffer === 'A' ? domeB : domeA;
    const outStage = activeBuffer === 'A' ? stageA : stageB;
    const inStage = activeBuffer === 'A' ? stageB : stageA;
    activeBuffer = activeBuffer === 'A' ? 'B' : 'A';

    if (!outStage || !inStage) return;

    if (reducedMotion || typeof outStage.animate !== 'function') {
      if (inDome && outDome) {
        inDome.style.background = theme.dome;
        inDome.style.opacity = '1';
        outDome.style.opacity = '0';
      }
      applyPlanetTheme(inStage, theme);
      outStage.classList.remove('is-active');
      outStage.style.opacity = '0';
      inStage.classList.add('is-active');
      inStage.style.opacity = '1';
      return;
    }

    // Başlangıç hazırlığı: Yeni gezegen kameranın döneceği yönde derinlikte bekler (soldan sağa geçen ek parıltı yok)
    const entryX = dir >= 0 ? '28vw' : '-28vw';
    const turnX = dir >= 0 ? '-14vw' : '14vw';
    const turnRot = dir >= 0 ? '-4.6deg' : '4.6deg';

    outStage.classList.remove('is-active');
    outStage.style.opacity = '1';
    outStage.style.transform = 'translate3d(0, 0, 0) scale(1)';

    inStage.classList.remove('is-active');
    applyPlanetTheme(inStage, theme);
    inStage.style.opacity = '0';
    inStage.style.transform = `translate3d(${entryX}, 1vh, 0) scale(0.24)`;

    // 1. AŞAMA (0 -> 320ms): Ekran mevcut gezegenden dümdüz geriye uzaklaşır
    const leaveAnim = outStage.animate(
      [
        { transform: 'translate3d(0, 0, 0) scale(1)', opacity: 1 },
        { transform: 'translate3d(0, 0, 0) scale(0.14)', opacity: 0 }
      ],
      { duration: 320, easing: 'cubic-bezier(0.42, 0, 0.9, 1)', fill: 'forwards' }
    );
    leaveAnim.onfinish = () => {
      outStage.style.opacity = '0';
      try { leaveAnim.cancel(); } catch (_) {}
    };
    activeTravelAnims.push(leaveAnim);

    // Yıldız sahnesi kamera dönüşü: Önce uzaklaş, sonra doğrudan yeni gezegene dön
    if (skyCanvas && typeof skyCanvas.animate === 'function') {
      const starAnim = skyCanvas.animate(
        [
          { transform: 'translate3d(0, 0, 0) scale(1) rotate(0deg)', offset: 0 },
          { transform: 'translate3d(0, 0, 0) scale(0.86) rotate(0deg)', offset: 0.30 },
          { transform: `translate3d(${turnX}, 0, 0) scale(0.96) rotate(${turnRot})`, offset: 0.65 },
          { transform: 'translate3d(0, 0, 0) scale(1) rotate(0deg)', offset: 1 }
        ],
        { duration: 960, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
      );
      activeTravelAnims.push(starAnim);
    }

    // 2. AŞAMA (240ms): Atmosfer kubbesi yumuşakça yeni gezegen rengine döner
    turnTimer = setTimeout(() => {
      turnTimer = null;
      if (inDome && outDome) {
        inDome.style.background = theme.dome;
        inDome.style.opacity = '1';
        outDome.style.opacity = '0';
      }
    }, 240);

    // 3. AŞAMA (400ms -> 980ms): Kamera döndükçe yeni gezegen merkez yörüngeye yaklaşır
    arriveTimer = setTimeout(() => {
      arriveTimer = null;
      const arriveAnim = inStage.animate(
        [
          { transform: `translate3d(${entryX}, 1vh, 0) scale(0.24)`, opacity: 0 },
          { transform: 'translate3d(0, 0, 0) scale(1)', opacity: 1 }
        ],
        { duration: 580, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' }
      );
      arriveAnim.onfinish = () => {
        inStage.style.opacity = '1';
        inStage.style.transform = 'translate3d(0, 0, 0) scale(1)';
        inStage.classList.add('is-active');
        try { arriveAnim.cancel(); } catch (_) {}
      };
      activeTravelAnims.push(arriveAnim);
    }, 400);
  };
})();

// ======================================================================
// KOZMİK TERMİNAL ERGONOMİ, FİNANSAL ZEKÂ & HIZLI AKSİYON YARDIMCILARI
// ======================================================================
function updateBrowserTabTitle(activeTab) {
  const privacyOn = document.body.classList.contains('privacy-mode');
  if (privacyOn) {
    document.title = 'Gizli Oturum — Kişisel Finans Paneli';
    return;
  }
  const tabLabels = {
    terminal: 'Ana Panel',
    council: 'Yatırım Konseyi',
    ledger: 'Defter & Kasa',
    goals: 'Kumbara & Hedef',
    splitter: 'Gelir Dağıtımı',
    radar: 'Fon Radarı',
    tools: 'Akıllı Araçlar',
    telegram: 'Telegram Bot'
  };
  const curTab = activeTab || (localStorage.getItem('fin_last_tab') || 'terminal');
  const lbl = tabLabels[curTab] || 'Finans Paneli';
  const showLive = !window.appPrefs || window.appPrefs.liveTitleNetWorth !== false;
  const totVal = Number(globalPortfolio?.total_value_try || 0);
  if (showLive && totVal > 0) {
    document.title = `₺${Math.round(totVal).toLocaleString('tr-TR')} · ${lbl}`;
  } else {
    document.title = `${lbl} — Finans Paneli`;
  }
}

function updateFreshnessBadge() {
  const el = document.getElementById('data-freshness-text');
  if (!el) return;
  const ts = Number(window.__lastDataUpdatedAt || 0);
  if (!ts) {
    el.innerText = '· Güncel';
    return;
  }
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 10) {
    el.innerText = '· Az önce';
    el.className = 'font-mono text-emerald-300/90 hidden sm:inline';
  } else if (sec < 60) {
    el.innerText = `· ${sec}sn önce`;
    el.className = 'font-mono text-emerald-300/90 hidden sm:inline';
  } else {
    const mn = Math.floor(sec / 60);
    el.innerText = `· ${mn}dk önce`;
    el.className = mn >= 3
      ? 'font-mono text-amber-300 hidden sm:inline'
      : 'font-mono text-slate-300 hidden sm:inline';
  }
}

function copyPortfolioSnapshot() {
  const p = globalPortfolio || {};
  const m = globalMarket || {};
  const total = Number(p.total_value_try || 0);
  const cash = Number(p.cash_try || 0);
  const usdTry = Number(m.macro?.usd_try || 0);
  const goldTry = Number(m.macro?.gram_altin_try || 0);
  const eqUsd = usdTry > 0 ? Math.round(total / usdTry) : 0;
  const eqGold = goldTry > 0 ? (total / goldTry).toFixed(1) : '—';
  const lines = [
    `Finans Terminal — Portföy Özeti (${new Date().toLocaleString('tr-TR')})`,
    `• Toplam Servet: ₺${total.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (~$${eqUsd.toLocaleString('tr-TR')} | ${eqGold} gr Altın)`,
    `• Banka Nakdi: ₺${cash.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
    `• Hedef İlerlemesi: %${Math.min(100, (total / GRADUATION_TARGET_TRY) * 100).toFixed(2)} (₺650.000)`,
    '',
    'Fon Pozisyonları:'
  ];
  (p.funds || []).filter(f => Number(f.shares || 0) > 0).forEach(f => {
    lines.push(`  - ${f.code}: ${Number(f.shares).toLocaleString('tr-TR', { maximumFractionDigits: 4 })} pay = ₺${Number(f.current_value || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2 })}`);
  });
  navigator.clipboard.writeText(lines.join('\n')).then(() => {
    const ic = document.getElementById('copy-portfolio-icon');
    if (ic) {
      ic.className = 'fa-solid fa-check text-emerald-400';
      setTimeout(() => { ic.className = 'fa-solid fa-copy'; }, 1400);
    }
    showToast('Portföy özeti panoya kopyalandı!');
  }).catch(() => showToast('Panoya kopyalanamadı.', 'warn'));
}

let __bentoDailyNemaTry = 0;
let __bentoSelectedBudget = 1000;
let __bentoTickerInterval = null;

function computeBentoQuickLots(amtTry) {
  if (amtTry !== undefined) __bentoSelectedBudget = Number(amtTry) || 1000;
  const budget = Math.max(100, __bentoSelectedBudget);
  const planEl = document.getElementById('bento-lot-plan');
  const leftEl = document.getElementById('bento-lot-leftover');
  if (!planEl) return;

  document.querySelectorAll('.bento-amt-chip').forEach(btn => {
    const txt = btn.innerText.trim();
    const matchVal = txt === '1K' ? 1000 : (txt === '4K' ? 4000 : (txt === '11K' ? 11000 : 500));
    const active = matchVal === budget;
    btn.className = active
      ? 'bento-amt-chip px-1.5 py-0.5 rounded bg-cyan-500/25 text-cyan-200 border border-cyan-400/40 font-bold transition'
      : 'bento-amt-chip px-1.5 py-0.5 rounded bg-white/[0.06] hover:bg-cyan-500/25 text-slate-200 border border-white/[0.1] transition';
  });

  const fMap = (globalMarket && globalMarket.funds) || {};
  const pZbj = Number(fMap.ZBJ?.price || 5.65);
  const pTca = Number(fMap.TCA?.price || 0.7306);
  const pAft = Number(fMap.AFT?.price || 0.8500);

  // 45% ZBJ (1 pay adım) · 35% TCA (100 pay adım veya bütçe küçükse 10 pay) · 20% AFT (1 pay adım)
  const rawZbj = budget * 0.45;
  const rawTca = budget * 0.35;
  const rawAft = budget * 0.20;

  const shZbj = Math.max(0, Math.floor(rawZbj / pZbj));
  const spentZbj = shZbj * pZbj;

  const tcaStep = budget >= 1000 ? 100 : 10;
  const shTca = Math.max(0, Math.floor((rawTca / pTca) / tcaStep) * tcaStep);
  const spentTca = shTca * pTca;

  const remForAft = Math.max(0, budget - spentZbj - spentTca);
  const shAft = Math.max(0, Math.floor(Math.min(rawAft + (rawTca - spentTca), remForAft) / pAft));
  const spentAft = shAft * pAft;

  const leftover = Math.max(0, budget - (spentZbj + spentTca + spentAft));

  planEl.innerHTML = `
    <div class="flex items-center justify-between"><span><strong class="text-emerald-300">ZBJ</strong> (%45 Faiz):</span><span>${shZbj.toLocaleString('tr-TR')} pay (₺${spentZbj.toFixed(2)})</span></div>
    <div class="flex items-center justify-between"><span><strong class="text-amber-300">TCA</strong> (%35 Altın):</span><span>${shTca.toLocaleString('tr-TR')} pay (₺${spentTca.toFixed(2)})</span></div>
    <div class="flex items-center justify-between"><span><strong class="text-indigo-300">AFT</strong> (%20 ABD):</span><span>${shAft.toLocaleString('tr-TR')} pay (₺${spentAft.toFixed(2)})</span></div>
  `;
  if (leftEl) leftEl.innerText = `5004'te Kalan Kuruş: ₺${leftover.toFixed(2)}`;
  if (amtTry !== undefined && typeof gsap !== 'undefined' && !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) {
    gsap.fromTo(planEl.children, { y: 5, opacity: 0.25 }, { y: 0, opacity: 1, duration: 0.22, stagger: 0.04, ease: 'power2.out' });
  }
}

function _tickBentoNemaAndCutoff() {
  const cutoffEl = document.getElementById('bento-tefas-cutoff-timer');
  const subEl = document.getElementById('bento-nema-micro-sub');
  const now = new Date();
  const trStr = now.toLocaleString('en-US', { timeZone: 'Europe/Istanbul' });
  const trDate = new Date(trStr);
  const h = trDate.getHours();
  const m = trDate.getMinutes();
  const s = trDate.getSeconds();
  const ms = now.getMilliseconds();

  if (cutoffEl) {
    const day = trDate.getDay();
    if (day === 0 || day === 6) {
      cutoffEl.innerText = 'Hafta Sonu · Pzt 13:30';
    } else {
      const targetSec = 13 * 3600 + 30 * 60;
      const curSec = h * 3600 + m * 60 + s;
      if (curSec < targetSec) {
        const diff = targetSec - curSec;
        const dh = Math.floor(diff / 3600);
        const dm = Math.floor((diff % 3600) / 60);
        cutoffEl.innerText = `13:30 Valör: ${dh}s ${dm}dk`;
      } else {
        cutoffEl.innerText = '13:30 Geçti (T+1 Valör)';
      }
    }
  }

  if (subEl && __bentoDailyNemaTry > 0) {
    const dayFraction = (h * 3600 + m * 60 + s + ms / 1000) / 86400;
    const accruedToday = __bentoDailyNemaTry * dayFraction;
    subEl.innerText = `Bugün şu ana kadar biriken: +₺${accruedToday.toFixed(5)}`;
  }
}

function updateExecutiveBentoStrip(resMarket, resPort) {
  if (!resPort) return;
  const total = Number(resPort.total_value_try || 0);
  const pnl = Number(resPort.total_profit_try || 0);
  const pnlPct = Number(resPort.total_profit_pct || 0);
  const usdTry = Number(resMarket?.macro?.usd_try || 42.0);
  const goldTry = Number(resMarket?.macro?.gram_altin_try || 6000.0);

  const bTotal = document.getElementById('bento-total-val');
  const bPnl = document.getElementById('bento-pnl-val');
  const bReal = document.getElementById('bento-real-val');
  if (bTotal) bTotal.innerText = `₺${total.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (bPnl) {
    const sign = pnl >= 0 ? '+' : '';
    bPnl.innerText = `${sign}₺${pnl.toFixed(2)} (${sign}%${pnlPct.toFixed(2)})`;
    bPnl.className = pnl >= 0
      ? 'text-xs font-mono font-bold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-400/30 privacy-mask'
      : 'text-xs font-mono font-bold px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-300 border border-rose-400/30 privacy-mask';
  }
  if (bReal) {
    const eqUsd = usdTry > 0 ? (total / usdTry).toFixed(2) : '—';
    const eqGold = goldTry > 0 ? (total / goldTry).toFixed(3) : '—';
    bReal.innerText = `$${eqUsd} USD · ${eqGold} gr Altın`;
  }

  // Gecelik Nema (ZBJ + TZL)
  const dailyNema = Number(resPort.daily_nema_try || 0);
  __bentoDailyNemaTry = dailyNema > 0 ? dailyNema : 1.78;
  const bNema = document.getElementById('bento-live-nema-stream');
  const bMonthNema = document.getElementById('bento-monthly-nema');
  if (bNema) bNema.innerText = `+₺${__bentoDailyNemaTry.toFixed(2)}`;
  if (bMonthNema) {
    const monthlyComp = __bentoDailyNemaTry * 31.4;
    bMonthNema.innerText = `Aylık Bileşik: ~+₺${monthlyComp.toFixed(2)}`;
  }

  // Stopaj sonrası net çıkış
  if (resPort.tax_summary && resPort.tax_summary.totals) {
    const tt = resPort.tax_summary.totals;
    const bNet = document.getElementById('bento-net-exit');
    const bTax = document.getElementById('bento-tax-cut');
    if (bNet) bNet.innerText = `₺${Number(tt.net_exit_value_try || total).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (bTax) bTax.innerText = `Stopaj Kesintisi: -₺${Number(tt.estimated_tax_try || 0).toFixed(2)} · Net Kâr: +₺${Number(tt.net_profit_try || 0).toFixed(2)}`;
  }

  computeBentoQuickLots(__bentoSelectedBudget);
  _tickBentoNemaAndCutoff();
  if (!__bentoTickerInterval) {
    __bentoTickerInterval = setInterval(_tickBentoNemaAndCutoff, 1000);
  }
}

// Spatial Design Cursor Spotlight + 3D Perspective Tilt + Magnetic CTA (RAF-throttled, zero layout shift)
(function initSpatialBentoSpotlight() {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let rafId = null;
  let activeCard = null;
  const resetActiveTilt = () => {
    if (activeCard) {
      activeCard.style.setProperty('--tilt-x', '0deg');
      activeCard.style.setProperty('--tilt-y', '0deg');
      activeCard = null;
    }
  };
  document.addEventListener('pointermove', (e) => {
    if (rafId) return;
    const target = e.target && e.target.closest ? e.target.closest('.bento-compartment, .ticker-card') : null;
    if (activeCard && activeCard !== target) {
      resetActiveTilt();
    }
    if (!target) return;
    activeCard = target;
    const clientX = e.clientX;
    const clientY = e.clientY;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      const rect = target.getBoundingClientRect();
      const relX = clientX - rect.left;
      const relY = clientY - rect.top;
      target.style.setProperty('--mouse-x', `${Math.round(relX)}px`);
      target.style.setProperty('--mouse-y', `${Math.round(relY)}px`);
      if (rect.width > 0 && rect.height > 0) {
        const normX = (relX / rect.width) - 0.5;
        const normY = (relY / rect.height) - 0.5;
        target.style.setProperty('--tilt-x', `${(-normY * 3.8).toFixed(2)}deg`);
        target.style.setProperty('--tilt-y', `${(normX * 3.8).toFixed(2)}deg`);
      }
    });
  }, { passive: true });
  document.addEventListener('mouseleave', resetActiveTilt, { passive: true });
  window.addEventListener('blur', resetActiveTilt, { passive: true });
})();

