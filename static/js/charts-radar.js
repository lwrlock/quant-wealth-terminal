function quickBuyFromRadar(code, amountTry = 0) {
  const upper = String(code || '').toUpperCase().trim();
  openTxModal();
  document.getElementById('tx-type').value = 'ALIM';
  const selCode = document.getElementById('tx-code');
  const customInp = document.getElementById('tx-custom-code');
  if (selCode) {
    const hasOpt = Array.from(selCode.options).some(o => o.value === upper);
    if (hasOpt) {
      selCode.value = upper;
    } else {
      selCode.value = '__CUSTOM__';
      if (customInp) customInp.value = upper;
    }
    onTxCodeSelectChange();
  }
  adjustTxFields();
  if (document.getElementById('tx-account')) document.getElementById('tx-account').value = '5004';
  updateTxSelectedAccBalance();
  const amtEl = document.getElementById('tx-amount');
  if (amtEl && Number(amountTry) > 0) {
    amtEl.value = String(Math.round(Number(amountTry)));
    document.getElementById('tx-desc').value = `${upper} Portföy Dengeleme Alımı`;
  } else {
    document.getElementById('tx-desc').value = `${upper} Hızlı Fon Alımı`;
  }
  updateTxLivePreview();
}

async function populateRadar(localOnly = false) {
  const res = (localOnly && globalMarket) ? globalMarket : await fetch('/api/market').then(r => r.json());
  if (!localOnly && res) globalMarket = res;
  const macro = (res && res.macro) || {};
  const dq = (res && res.data_quality) || {};
  const qBadge = document.getElementById('radar-quality-badge');
  if (qBadge && dq.source_status) {
    qBadge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span><span>${escapeHtml(dq.source_status)} (${escapeHtml(res.updated_at || '')})</span>`;
  }
  const setM = (id, text) => { const el = document.getElementById(id); if (el) el.innerText = text; };
  setM('rm-usd', `₺${Number(macro.usd_try || 0).toFixed(2)} (${(macro.usd_chg_pct || 0) >= 0 ? '+' : ''}${Number(macro.usd_chg_pct || 0).toFixed(2)}%)`);
  setM('rm-eur', `₺${Number(macro.eur_try || 0).toFixed(2)} (${(macro.eur_chg_pct || 0) >= 0 ? '+' : ''}${Number(macro.eur_chg_pct || 0).toFixed(2)}%)`);
  setM('rm-bist', `${Number(macro.bist100 || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 })} (${(macro.bist100_chg_pct || 0) >= 0 ? '+' : ''}${Number(macro.bist100_chg_pct || 0).toFixed(2)}%)`);
  setM('rm-ndx', `${Number(macro.nasdaq100 || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 })} (${(macro.nasdaq_chg_pct || 0) >= 0 ? '+' : ''}${Number(macro.nasdaq_chg_pct || 0).toFixed(2)}%)`);

  const tbody = document.getElementById('radar-table');
  tbody.innerHTML = '';

  const fMap = (res && res.funds) || {};
  const KNOWN_RADAR_META = {
    ZBJ: { name: 'Ziraat Başak (Günlük Kazanç)', role: 'Boşta para beklerken her gece kazandırır' },
    TZL: { name: 'Ziraat Para Piyasası', role: 'Acil durumda aynı gün nakde döner' },
    TCA: { name: 'Ziraat Altın Katılım', role: 'Enflasyona karşı makassız altın koruması' },
    AFT: { name: 'Ak Portföy Teknoloji', role: 'Nvidia / Apple / Microsoft uzun vadeli büyüme' },
    KZL: { name: 'Kuveyt Türk Altın', role: 'Alternatif katılım altın fonu' },
    TI3: { name: 'İş Portföy BIST30', role: 'Borsa İstanbul büyük şirketleri' },
  };
  const allRadarCodes = getCatalogFundCodes(fMap, globalPortfolio?.funds || []);
  const searchQ = (document.getElementById('radar-search-input')?.value || '').toLowerCase().trim();
  const sortMode = document.getElementById('radar-sort-select')?.value || 'default';

  let configs = allRadarCodes.map(code => {
    const meta = fMap[code] || {};
    const known = KNOWN_RADAR_META[code];
    const cat = meta.category || 'TEFAS Fonu';
    return {
      code,
      name: known ? known.name : (meta.name || `${code} TEFAS Fonu`),
      role: known ? known.role : `${cat} • Canlı TEFAS takibinde`,
      buyable: true
    };
  }).filter(cfg => {
    if (!searchQ) return true;
    return `${cfg.code} ${cfg.name} ${cfg.role}`.toLowerCase().includes(searchQ);
  });

  if (sortMode === 'monthly_desc') {
    configs.sort((a, b) => Number(fMap[b.code]?.monthly_yield_pct || -999) - Number(fMap[a.code]?.monthly_yield_pct || -999));
  } else if (sortMode === 'weekly_desc') {
    configs.sort((a, b) => Number(fMap[b.code]?.weekly_yield_pct || -999) - Number(fMap[a.code]?.weekly_yield_pct || -999));
  } else if (sortMode === 'daily_desc') {
    configs.sort((a, b) => Number(fMap[b.code]?.daily_yield_pct || -999) - Number(fMap[a.code]?.daily_yield_pct || -999));
  } else if (sortMode === 'drawdown_asc') {
    configs.sort((a, b) => Number(fMap[a.code]?.drawdown_from_high_pct || 0) - Number(fMap[b.code]?.drawdown_from_high_pct || 0));
  }

  const fmtPct = (v) => {
    const n = Number(v || 0);
    const cls = n >= 0 ? 'text-emerald-400' : 'text-rose-400';
    return `<span class="${cls}">${n >= 0 ? '+' : ''}%${n.toFixed(2)}</span>`;
  };

  if (!configs.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="py-8 text-center text-slate-400 text-sm">Aramana uyan TEFAS fonu bulunamadı.</td></tr>';
  }

  configs.forEach(cfg => {
    const f = fMap[cfg.code] || {};
    const price = Number(f.price || 0);
    const dd = Number(f.drawdown_from_high_pct || 0);
    const ddCls = dd <= -2.0 ? 'text-amber-300 font-semibold' : 'text-slate-400';
    const rankTxt = (f.category_rank && f.category_total) ? `${f.category_rank} / ${f.category_total}` : '—';

    const row = document.createElement('tr');
    row.className = 'hover:bg-slate-800/35 transition-colors';
    row.innerHTML = `
      <td class="py-3.5 font-bold text-indigo-300 font-mono">${escapeHtml(cfg.code)}</td>
      <td class="py-3.5 text-slate-100 font-medium">${escapeHtml(cfg.name)}</td>
      <td class="py-3.5 font-mono font-bold text-white">₺${price.toFixed(4)}</td>
      <td class="py-3.5 font-mono text-xs space-x-1.5 whitespace-nowrap">${fmtPct(f.daily_yield_pct)} <span class="text-slate-600">/</span> ${fmtPct(f.weekly_yield_pct)} <span class="text-slate-600">/</span> ${fmtPct(f.monthly_yield_pct)}</td>
      <td class="py-3.5 font-mono text-xs ${ddCls}">${dd >= 0 ? '+' : ''}%${dd.toFixed(2)}</td>
      <td class="py-3.5 font-mono text-xs text-slate-200">${escapeHtml(rankTxt)}</td>
      <td class="py-3.5 text-slate-300 text-[13px]">${escapeHtml(cfg.role)}</td>
      <td class="py-3.5 text-right whitespace-nowrap">
        <div class="inline-flex items-center gap-1.5">
          <button type="button" data-code="${escapeHtml(cfg.code)}" onclick="quickAlarmFromRadar(this.dataset.code)" class="h-9 px-2.5 rounded-xl bg-white/[0.05] hover:bg-amber-500/20 text-amber-200 border border-white/[0.1] text-xs font-semibold transition active:scale-95" title="Bu fon için fiyat düşüş alarmı hazırla"><i class="fa-solid fa-bell"></i></button>
          ${cfg.buyable ? `<button data-code="${escapeHtml(cfg.code)}" onclick="quickBuyFromRadar(this.dataset.code)" class="h-9 px-3.5 rounded-xl bg-indigo-600/85 hover:bg-indigo-500 text-white text-xs font-semibold transition active:scale-95">+ Hızlı Al</button>` : '<span class="text-slate-400 text-xs">İzleme</span>'}
        </div>
      </td>
    `;
    tbody.appendChild(row);
  });
  if (typeof renderFundSideBySideCompare === 'function') renderFundSideBySideCompare();
  if (!localOnly) {
    renderRadarPerformanceChart(fMap);
    loadQuantRadar(false);
    loadTefasLeague(false);
    loadAlphaHunter(false);
  }
}

function _stripLeadingEmoji(txt) {
  return String(txt || '').replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\s]+/u, '').trim();
}

function _fmtAlphaPrice(val, cur) {
  const sym = cur === 'TRY' ? '₺' : '$';
  const n = Number(val || 0);
  return sym + n.toLocaleString('tr-TR', { minimumFractionDigits: n >= 100 ? 2 : 4, maximumFractionDigits: n >= 100 ? 2 : 4 });
}

async function loadAlphaHunter(forceRefresh = false) {
  const strip = document.getElementById('alpha-summary-strip');
  const oppsGrid = document.getElementById('alpha-opps-grid');
  const klGrid = document.getElementById('alpha-knives-leaders');
  const updEl = document.getElementById('alpha-updated-at');
  const iconEl = document.getElementById('alpha-refresh-icon');
  if (!oppsGrid) return;
  if (iconEl && forceRefresh) iconEl.classList.add('fa-spin');
  try {
    const data = await fetch(`/api/alpha/scan?refresh=${forceRefresh ? 'true' : 'false'}`).then(r => r.json());
    if (!data || data.status !== 'ok') return;
    if (updEl && data.fetched_at) updEl.innerText = `Son tarama: ${data.fetched_at}`;

    const opps = data.opportunities || [];
    const knives = data.falling_knives || [];
    const leaders = data.top_edge_leaders || [];

    if (strip) {
      const reg = data.macro_regime || 'GREEN';
      const regDot = reg === 'RED' ? 'bg-rose-500' : (reg === 'YELLOW' ? 'bg-amber-400' : 'bg-emerald-400');
      const regLabel = reg === 'RED' ? 'Gergin (Az Para Koy)' : (reg === 'YELLOW' ? 'Dalgalı (Dikkatli)' : 'Sakin');
      strip.innerHTML = `
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.08]">
          <div class="text-xs text-slate-400">İncelenen Hisse / Coin</div>
          <div class="text-lg font-extrabold text-white font-mono">${Number(data.universe_count || 0)} Adet</div>
        </div>
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-emerald-500/25">
          <div class="text-xs text-slate-400">Geçmişte Kazandıranlar</div>
          <div class="text-lg font-extrabold text-emerald-400 font-mono">${Number(data.qualified_edge_count || 0)} Tane</div>
        </div>
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-amber-500/25">
          <div class="text-xs text-slate-400">Bugün Alınabilir</div>
          <div class="text-lg font-extrabold text-amber-300 font-mono">${opps.length} Fırsat</div>
        </div>
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-rose-500/25">
          <div class="text-xs text-slate-400">Şimdi Alma (Bekle) · Piyasa</div>
          <div class="text-lg font-extrabold text-rose-300 font-mono inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full ${regDot}"></span><span>${knives.length} Adet · ${escapeHtml(regLabel)}</span></div>
        </div>
      `;
    }

    if (opps.length) {
      oppsGrid.innerHTML = opps.map(item => {
        const bt = item.active_backtest || {};
        const rp = item.risk_plan || {};
        const cur = item.currency || 'USD';
        return `
          <div class="p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/35 space-y-2">
            <div class="flex items-center justify-between gap-2">
              <span class="font-bold text-white font-mono text-base">${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</span>
              <span class="px-2.5 py-0.5 rounded-full bg-emerald-500/25 text-emerald-200 text-xs font-bold inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400"></span>ALINABİLİR</span>
            </div>
            <div class="text-sm text-emerald-100 leading-relaxed">${escapeHtml(item.plain_reason || '')}</div>
            <div class="text-xs font-mono text-slate-200">Şu anki fiyat: ${escapeHtml(_fmtAlphaPrice(item.price, cur))} · Son 5 yılda ${Number(bt.trades || 0)} kezden %${Number(bt.win_rate_pct || 0)}'inde kazandırdı</div>
            <div class="text-xs text-slate-300 font-mono">Zarar Kes: ${escapeHtml(_fmtAlphaPrice(rp.stop_loss, cur))} (${Number(rp.stop_pct || 0)}%) · İlk Hedef: ${escapeHtml(_fmtAlphaPrice(rp.target_1, cur))} (+${Number(rp.target_1_pct || 0)}%) · En fazla %${Number(rp.max_pos_pct || 0)} koy</div>
          </div>
        `;
      }).join('');
    } else {
      oppsGrid.innerHTML = `
        <div class="p-4 rounded-2xl bg-[#070c1e] border border-white/[0.08] text-sm text-slate-300 col-span-full flex items-center gap-2.5">
          <i class="fa-solid fa-shield-halved text-emerald-400 shrink-0"></i>
          <span><strong class="text-white">Bugün alınabilecek temiz bir düşüş fırsatı yok.</strong> Acele edip düşerken almak yerine paranı boşta veya ZBJ'de tut.</span>
        </div>
      `;
    }

    if (klGrid) {
      const kHtml = knives.slice(0, 5).map(k => {
        const dropV = Math.abs(Number(k.dd90_pct || 0)) > Math.abs(Number(k.dd20_pct || 0)) + 4 ? Math.abs(Number(k.dd90_pct || 0)) : Math.abs(Number(k.dd20_pct || 0));
        const sinceTxt = k.dip_from_date ? `${k.dip_from_date}'den beri ` : '';
        return `
        <div class="flex flex-wrap items-center justify-between gap-2 text-xs py-2 border-b border-white/[0.05] cursor-pointer hover:bg-white/[0.02] px-1 rounded transition" onclick="analyzeAlphaAsset('${escapeHtml(k.symbol)}')">
          <span class="font-mono font-bold text-rose-300">${escapeHtml(k.symbol)} (${escapeHtml(_fmtAlphaPrice(k.price, k.currency))})</span>
          <span class="text-slate-300">${escapeHtml(sinceTxt)}-${dropV.toFixed(1)}% düştü · Satış sürüyor, bekle</span>
        </div>`;
      }).join('') || '<div class="text-xs text-slate-400">Şu an düşmeye devam eden tehlikeli bir varlık yok.</div>';

      const lHtml = leaders.slice(0, 5).map(ld => {
        const bt = ld.best_historical_backtest || {};
        return `
          <div class="flex flex-wrap items-center justify-between gap-2 text-xs py-2 border-b border-white/[0.05] cursor-pointer hover:bg-white/[0.02] px-1 rounded transition" onclick="analyzeAlphaAsset('${escapeHtml(ld.symbol)}')">
            <span class="font-mono font-bold text-amber-300">${escapeHtml(ld.symbol)}</span>
            <span class="text-slate-300">Son 5 yılda ${Number(bt.trades || 0)} fırsatta %${Number(bt.win_rate_pct || 0)} kazandırdı (ort. +${Number(bt.avg_return_pct || 0)}%)</span>
          </div>
        `;
      }).join('') || '<div class="text-xs text-slate-400">Liste hazırlanıyor.</div>';

      klGrid.innerHTML = `
        <div class="p-4 rounded-2xl bg-[#070c1e] border border-rose-500/25 space-y-2">
          <div class="text-xs font-bold text-rose-300 flex items-center gap-1.5"><i class="fa-solid fa-hand text-rose-400"></i><span>Düştü Ama Şimdi Alma (Dönmesini Bekle)</span></div>
          ${kHtml}
        </div>
        <div class="p-4 rounded-2xl bg-[#070c1e] border border-amber-500/25 space-y-2">
          <div class="text-xs font-bold text-amber-300 flex items-center gap-1.5"><i class="fa-solid fa-clock-rotate-left text-amber-400"></i><span>Fırsat Çıkınca İlk Bakılacaklar (Geçmiş Kazananları)</span></div>
          ${lHtml}
        </div>
      `;
    }
    if (forceRefresh) showToast("Tarama güncellendi!");
  } catch (err) {
    console.error('Alpha hunter err:', err);
  } finally {
    if (iconEl) iconEl.classList.remove('fa-spin');
  }
}

function openAlphaFromTerminal() {
  if (typeof switchTab === 'function') switchTab('radar');
  setTimeout(() => {
    const el = document.getElementById('alpha-hunter-card');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 150);
}

function renderTerminalAlphaStrip(summary) {
  const el = document.getElementById('terminal-alpha-strip');
  const cardsGrid = document.getElementById('terminal-alpha-cards-grid');
  if (!summary) return;
  const opps = summary.opportunities || [];
  const knives = summary.falling_knives || [];
  const uCount = Number(summary.universe_count || 0);

  if (el && (uCount || opps.length || knives.length)) {
    el.classList.remove('hidden');
    if (opps.length) {
      const names = opps.slice(0, 4).map(o => `<strong class="text-emerald-300">${escapeHtml(o.symbol)}</strong>`).join(', ');
      el.innerHTML = `
        <div class="flex items-center gap-2.5 min-w-0">
          <span class="px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-bold text-xs shrink-0 inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400"></span>ALINABİLİR (${opps.length})</span>
          <span class="text-slate-200 truncate">BIST / Global / Kripto 5Y kontrolü: ${names} için dönüş teyidi geldi.</span>
        </div>
        <span class="text-xs font-semibold text-amber-300 shrink-0">Tüm Radar &amp; Stop Planı →</span>
      `;
    } else {
      const knifeNames = knives.slice(0, 3).map(k => escapeHtml(k.symbol)).join(', ');
      const kTxt = knives.length ? ` · ${knives.length} düşmeye devam (${knifeNames}) — Alma` : '';
      el.innerHTML = `
        <div class="flex items-center gap-2.5 min-w-0">
          <span class="px-2.5 py-0.5 rounded-full bg-amber-500/15 text-amber-300 font-bold text-xs shrink-0 inline-flex items-center gap-1.5"><i class="fa-solid fa-shield-halved text-[11px]"></i>PİYASA KONTROLÜ (${uCount} Varlık)</span>
          <span class="text-slate-200 truncate">Bugün alınabilecek güvenli dip fırsatı yok${kTxt}</span>
        </div>
        <span class="text-xs font-semibold text-sky-300 shrink-0">Detaylı Avcı →</span>
      `;
    }
  }

  if (cardsGrid) {
    const leaders = summary.top_edge_leaders || [];
    const combined = [
      ...opps.slice(0, 4).map(o => ({ item: o, kind: 'opp' })),
      ...knives.slice(0, 3).map(k => ({ item: k, kind: 'knife' }))
    ];
    for (const ld of leaders) {
      if (combined.length >= 6) break;
      if (!combined.some(x => x.item.symbol === ld.symbol)) {
        combined.push({ item: ld, kind: 'leader' });
      }
    }
    if (!combined.length) {
      cardsGrid.innerHTML = `<div class="col-span-full p-4 rounded-xl bg-white/[0.025] border border-white/[0.08] text-xs text-slate-300">Bugün 27 varlık içinde 5 yıllık başarı eşiğini geçen dip veya düşen bıçak sinyali yok.</div>`;
    } else {
      cardsGrid.innerHTML = combined.slice(0, 6).map(({ item, kind }) => {
        const cur = item.currency || 'USD';
        const priceStr = _fmtAlphaPrice(item.price, cur);
        const dropV = Math.abs(Number(item.dd90_pct || 0)) > Math.abs(Number(item.dd20_pct || 0)) + 4
          ? Math.abs(Number(item.dd90_pct || 0))
          : Math.abs(Number(item.dd20_pct || 0));
        const dipDate = item.dip_from_date ? escapeHtml(item.dip_from_date) : escapeHtml(item.last_date_tr || 'Güncel');
        if (kind === 'opp') {
          const bt = item.active_backtest || item.best_historical_backtest || {};
          const rp = item.risk_plan || {};
          const isDip = item.active_strategy === 'dip_bounce';
          const badge = isDip
            ? `<span class="px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-300 border border-emerald-400/35 text-[11px] font-bold">DİPTEN DÖNÜŞ · AL</span>`
            : `<span class="px-2 py-0.5 rounded-md bg-sky-500/20 text-sky-300 border border-sky-400/35 text-[11px] font-bold">KIRILIM · AL</span>`;
          return `
            <div class="p-4 rounded-2xl bg-emerald-500/[0.06] border border-emerald-400/30 hover:border-emerald-400/55 space-y-2 transition">
              <div class="flex items-center justify-between gap-2">
                <span class="font-extrabold text-white text-sm truncate">${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</span>
                ${badge}
              </div>
              <div class="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
                <span class="text-white font-bold text-sm">${escapeHtml(priceStr)}</span>
                <span class="text-emerald-300">5Y Başarı: %${Number(bt.win_rate_pct || 0)} (${Number(bt.trades || 0)} işlem · PF ${Number(bt.profit_factor || 0)})</span>
              </div>
              <div class="text-[11px] text-slate-200 leading-relaxed">${escapeHtml(item.plain_reason || '')}</div>
              <div class="text-[11px] font-mono text-slate-300 flex flex-wrap items-center justify-between gap-1 pt-1 border-t border-white/[0.06]">
                <span>Hedef: ${escapeHtml(_fmtAlphaPrice(rp.target_1, cur))} (+%${Number(rp.target_1_pct || 0)})</span>
                <span class="text-rose-300">Zarar Kes: ${escapeHtml(_fmtAlphaPrice(rp.stop_loss, cur))} (%${Number(rp.stop_pct || 0)})</span>
              </div>
            </div>
          `;
        }
        if (kind === 'knife') {
          return `
            <div class="p-4 rounded-2xl bg-rose-500/[0.06] border border-rose-500/30 hover:border-rose-400/55 space-y-2 transition">
              <div class="flex items-center justify-between gap-2">
                <span class="font-extrabold text-white text-sm truncate">${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</span>
                <span class="px-2 py-0.5 rounded-md bg-rose-500/20 text-rose-200 border border-rose-400/35 text-[11px] font-bold">DÜŞEN BIÇAK · ALMA</span>
              </div>
              <div class="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
                <span class="text-white font-bold text-sm">${escapeHtml(priceStr)}</span>
                <span class="text-rose-300">${dipDate}'den beri -%${dropV.toFixed(1)} düştü</span>
              </div>
              <div class="text-[11px] text-rose-100/90 leading-relaxed pt-1 border-t border-white/[0.06]">
                ${escapeHtml(item.plain_reason || 'Satış baskısı henüz durmadı; dönüş mumunu bekle.')}
              </div>
            </div>
          `;
        }
        const bt = item.best_historical_backtest || {};
        return `
          <div class="p-4 rounded-2xl bg-amber-500/[0.05] border border-amber-400/25 hover:border-amber-400/50 space-y-2 transition">
            <div class="flex items-center justify-between gap-2">
              <span class="font-extrabold text-white text-sm truncate">${escapeHtml(item.symbol)} · ${escapeHtml(item.name)}</span>
              <span class="px-2 py-0.5 rounded-md bg-amber-500/18 text-amber-200 border border-amber-400/30 text-[11px] font-bold">5Y LİDER · İZLE</span>
            </div>
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
              <span class="text-white font-bold text-sm">${escapeHtml(priceStr)}</span>
              <span class="text-amber-300">5Y: %${Number(bt.win_rate_pct || 0)} Başarı · Ort +${Number(bt.avg_return_pct || 0)}%</span>
            </div>
            <div class="text-[11px] text-slate-300 leading-relaxed pt-1 border-t border-white/[0.06]">
              ${escapeHtml(item.plain_reason || 'Geçmiş 5 yılda en yüksek başarı oranına sahip varlık; uygun geri çekilmeyi bekle.')}
            </div>
          </div>
        `;
      }).join('');
    }
  }
}

let __termKarneBootstrapped = false;
async function fetchTerminalAlphaStrip(forceRefresh = false) {
  try {
    if (window.location.hostname.includes('onrender.com')) {
      const syncBtn = document.getElementById('btn-sync-render');
      if (syncBtn) syncBtn.classList.add('hidden');
    }
    const url = forceRefresh ? '/api/alpha/scan?refresh=true' : '/api/alpha/scan';
    const data = await fetch(url).then(r => r.json());
    if (data && data.status === 'ok') renderTerminalAlphaStrip(data);
  } catch (err) {
    console.warn('Alpha strip err:', err);
  }
  if (!__termKarneBootstrapped) {
    __termKarneBootstrapped = true;
    setTimeout(() => {
      runTerminalTefasLookup('TCA');
      runTerminalSwitchSim();
    }, 450);
  }
}

function setTerminalDecisionMode(mode) {
  const oppSec = document.getElementById('terminal-opp-section');
  const alphaSec = document.getElementById('terminal-alpha-section');
  const karneSec = document.getElementById('terminal-karne-section');
  if (oppSec) oppSec.classList.toggle('hidden', mode !== 'all');
  if (alphaSec) alphaSec.classList.toggle('hidden', mode !== 'all' && mode !== 'alpha');
  if (karneSec) karneSec.classList.toggle('hidden', mode !== 'all' && mode !== 'karne');
  ['all', 'alpha', 'karne'].forEach(k => {
    const btn = document.getElementById('term-mode-' + k);
    if (!btn) return;
    const isAct = k === mode;
    btn.classList.toggle('bg-indigo-600', isAct);
    btn.classList.toggle('text-white', isAct);
    btn.classList.toggle('text-slate-300', !isAct);
  });
}

async function runTerminalTefasLookup(presetCode = null) {
  const inp = document.getElementById('term-tefas-code');
  const box = document.getElementById('term-tefas-result');
  if (!inp || !box) return;
  if (presetCode) inp.value = presetCode;
  const code = inp.value.trim().toUpperCase();
  if (!code) return;
  box.innerHTML = '<div class="h-12 rounded-xl skeleton-shimmer"></div>';
  try {
    const resp = await fetch(`/api/tefas/analyze/${encodeURIComponent(code)}`);
    if (!resp.ok) {
      box.innerHTML = `<div class="text-rose-300">${escapeHtml(code)} kodlu fon bulunamadı.</div>`;
      return;
    }
    const d = await resp.json();
    const ex = Number(d.excess_vs_zbj_1y_pct || 0);
    const exBadge = ex >= 0
      ? `<span class="text-emerald-300 font-mono font-bold">ZBJ'den +${ex}% önde</span>`
      : `<span class="text-rose-300 font-mono font-bold">ZBJ'den ${ex}% geride</span>`;
    const cleanVerdict = _stripLeadingEmoji(d.verdict_tr);
    box.innerHTML = `
      <div class="p-3.5 rounded-xl bg-white/[0.04] border border-white/[0.1] space-y-1.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-bold text-white">${escapeHtml(d.code)} · ${escapeHtml(d.name)}</span>
          <span class="px-2.5 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-400/35 text-xs font-semibold text-indigo-200">${escapeHtml(cleanVerdict)}</span>
        </div>
        <div class="font-mono text-slate-200">Fiyat: ₺${Number(d.price).toFixed(4)} · 30G: ${Number(d.ret_30d_pct) >= 0 ? '+' : ''}${Number(d.ret_30d_pct)}% · 1Y: ${Number(d.ret_1y_pct) >= 0 ? '+' : ''}${Number(d.ret_1y_pct)}% · ${exBadge}</div>
        <div class="text-slate-300 leading-relaxed">${escapeHtml(d.plain_reason)} <span class="text-amber-300 font-mono">(Stopaj %${Number(d.stopaj_pct)} · Valör T+${Number(d.valor_days)})</span></div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<div class="text-rose-300">Hata: ${escapeHtml(String(err))}</div>`;
  }
}

function presetTerminalSwitch(fromCode, toCode, amtTry, profitTry) {
  const fEl = document.getElementById('term-sw-from');
  const tEl = document.getElementById('term-sw-to');
  const aEl = document.getElementById('term-sw-amt');
  const pEl = document.getElementById('term-sw-profit');
  if (fEl) fEl.value = fromCode;
  if (tEl) tEl.value = toCode;
  if (aEl && amtTry !== undefined) aEl.value = amtTry;
  if (pEl && profitTry !== undefined) pEl.value = profitTry;
  runTerminalSwitchSim();
}

async function runTerminalSwitchSim() {
  const fEl = document.getElementById('term-sw-from');
  const tEl = document.getElementById('term-sw-to');
  const aEl = document.getElementById('term-sw-amt');
  const pEl = document.getElementById('term-sw-profit');
  const box = document.getElementById('term-sw-result');
  if (!fEl || !tEl || !box) return;
  const fc = fEl.value.trim().toUpperCase();
  const tc = tEl.value.trim().toUpperCase();
  const amt = Math.max(50, Number(aEl?.value || 731));
  const prof = Math.max(0, Number(pEl?.value || 0));
  if (!fc || !tc) return;
  box.innerHTML = '<div class="h-12 rounded-xl skeleton-shimmer"></div>';
  try {
    const url = `/api/tefas/switch-sim?from_code=${encodeURIComponent(fc)}&to_code=${encodeURIComponent(tc)}&amount_try=${encodeURIComponent(amt)}&profit_try=${encodeURIComponent(prof)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      box.innerHTML = '<div class="text-rose-300">İki farklı geçerli TEFAS fon kodu girin.</div>';
      return;
    }
    const s = await resp.json();
    const bTxt = s.breakeven_days ? `${s.breakeven_days} gün` : 'Kapanmıyor';
    const cleanVerdict = _stripLeadingEmoji(s.verdict_tr);
    const isGood = String(s.verdict_tr || '').includes('GEÇİLİR');
    const pillCls = isGood ? 'bg-emerald-500/20 border-emerald-400/35 text-emerald-200' : 'bg-amber-500/20 border-amber-400/35 text-amber-200';
    box.innerHTML = `
      <div class="p-3.5 rounded-xl bg-white/[0.04] border border-white/[0.1] space-y-1.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-bold text-white">${escapeHtml(s.from_code)} → ${escapeHtml(s.to_code)} (₺${Number(s.gross_try).toLocaleString('tr-TR')})</span>
          <span class="px-2.5 py-0.5 rounded-full border text-xs font-semibold ${pillCls}">${escapeHtml(cleanVerdict)}</span>
        </div>
        <div class="font-mono text-slate-200">Masraf: Stopaj -₺${Number(s.stopaj_cost_try).toLocaleString('tr-TR')} + ${Number(s.valor_days)}g Valör -₺${Number(s.valor_cost_try).toLocaleString('tr-TR')} = <strong class="text-rose-300">-₺${Number(s.total_switch_cost_try).toLocaleString('tr-TR')}</strong> · Amorti: <strong class="text-white">${escapeHtml(bTxt)}</strong></div>
        <div class="text-slate-300 leading-relaxed">${escapeHtml(s.summary_tr)}</div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<div class="text-rose-300">Hata: ${escapeHtml(String(err))}</div>`;
  }
}

function openInlineTefasKarne(code) {
  setTerminalDecisionMode('all');
  const sec = document.getElementById('terminal-karne-section');
  if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
  runTerminalTefasLookup(code);
}

function openInlineSwitchSim(fromCode, toCode, amtTry, profitTry) {
  setTerminalDecisionMode('all');
  const sec = document.getElementById('terminal-karne-section');
  if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
  presetTerminalSwitch(fromCode, toCode, amtTry, profitTry);
}

async function syncFromRenderCloud() {
  const btn = document.getElementById('btn-sync-render');
  const icon = document.getElementById('sync-render-icon');
  const lbl = document.getElementById('sync-render-label');
  if (btn) btn.disabled = true;
  if (icon) icon.className = 'fa-solid fa-spinner fa-spin text-emerald-300 text-xs';
  if (lbl) lbl.innerText = 'Eşitleniyor...';
  try {
    const resp = await fetch('/api/sync/pull-from-render', { method: 'POST' });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      if (typeof showToast === 'function') showToast(data.detail || 'Render senkronizasyon hatası', 'error');
      return;
    }
    if (typeof fetchAllData === 'function') await fetchAllData(true);
    if (typeof fetchNetWorthHistory === 'function') fetchNetWorthHistory(window.__activeNwDays || 30);
    if (typeof showToast === 'function') {
      showToast(`Render bulut verileri yerel veritabanına eşitlendi! (Nakit: ₺${Number(data.cash_try || 0).toFixed(2)} · ${data.active_funds || 0} Aktif Fon · ${data.counts?.transactions || 0} İşlem)`, 'success');
    }
  } catch (err) {
    if (typeof showToast === 'function') showToast('Render bağlantı hatası: ' + String(err), 'error');
  } finally {
    if (btn) btn.disabled = false;
    if (icon) icon.className = 'fa-solid fa-cloud-arrow-down text-emerald-300 text-xs';
    if (lbl) lbl.innerText = "Render'dan Eşitle";
  }
}

async function runTefasFundLookup(presetCode = null) {
  const inp = document.getElementById('tefas-lookup-code');
  const box = document.getElementById('tefas-lookup-result');
  const btn = document.getElementById('tefas-lookup-btn');
  if (!inp || !box) return;
  if (presetCode) inp.value = presetCode;
  const code = inp.value.trim().toUpperCase();
  if (!code) return;
  if (btn) btn.disabled = true;
  box.innerHTML = '<div class="h-14 rounded-xl skeleton-shimmer"></div>';
  try {
    const resp = await fetch(`/api/tefas/analyze/${encodeURIComponent(code)}`);
    if (!resp.ok) {
      box.innerHTML = `<div class="text-rose-300">${escapeHtml(code)} kodlu fon bulunamadı.</div>`;
      return;
    }
    const d = await resp.json();
    const ex = Number(d.excess_vs_zbj_1y_pct || 0);
    const exBadge = ex >= 0 ? `<span class="text-emerald-300 font-mono">ZBJ'den +${ex}% önde</span>` : `<span class="text-rose-300 font-mono">ZBJ'den ${ex}% geride</span>`;
    const cleanVerdict = _stripLeadingEmoji(d.verdict_tr);
    box.innerHTML = `
      <div class="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-1.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-bold text-white">${escapeHtml(d.code)} · ${escapeHtml(d.name)}</span>
          <span class="px-2.5 py-0.5 rounded-full bg-white/[0.08] text-xs font-semibold text-white">${escapeHtml(cleanVerdict)}</span>
        </div>
        <div class="font-mono text-slate-200">Fiyat: ₺${Number(d.price).toFixed(6)} · 30G: ${Number(d.ret_30d_pct) >= 0 ? '+' : ''}${Number(d.ret_30d_pct)}% · 1Y: ${Number(d.ret_1y_pct) >= 0 ? '+' : ''}${Number(d.ret_1y_pct)}% · ${exBadge}</div>
        <div class="text-slate-300 leading-relaxed">${escapeHtml(d.plain_reason)} (Stopaj %${Number(d.stopaj_pct)} · Valör T+${Number(d.valor_days)})</div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<div class="text-rose-300">Hata: ${escapeHtml(String(err))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function presetTefasSwitch(fromCode, toCode) {
  const fInp = document.getElementById('switch-from-code');
  const tInp = document.getElementById('switch-to-code');
  if (fInp) fInp.value = fromCode;
  if (tInp) tInp.value = toCode;
  runTefasSwitchSim();
}

async function runTefasSwitchSim() {
  const fInp = document.getElementById('switch-from-code');
  const tInp = document.getElementById('switch-to-code');
  const aInp = document.getElementById('switch-amount-try');
  const box = document.getElementById('tefas-switch-result');
  const btn = document.getElementById('tefas-switch-btn');
  if (!fInp || !tInp || !box) return;
  const fc = fInp.value.trim().toUpperCase();
  const tc = tInp.value.trim().toUpperCase();
  if (!fc || !tc) return;
  if (btn) btn.disabled = true;
  box.innerHTML = '<div class="h-14 rounded-xl skeleton-shimmer"></div>';
  try {
    let url = `/api/tefas/switch-sim?from_code=${encodeURIComponent(fc)}&to_code=${encodeURIComponent(tc)}`;
    const amt = Number(aInp?.value || 0);
    if (amt >= 100) url += `&amount_try=${encodeURIComponent(amt)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      box.innerHTML = '<div class="text-rose-300">İki farklı geçerli TEFAS fon kodu gir (Örn: AFT → YAY).</div>';
      return;
    }
    const s = await resp.json();
    const bTxt = s.breakeven_days ? `${s.breakeven_days} gün` : 'Kapanmıyor';
    const cleanVerdict = _stripLeadingEmoji(s.verdict_tr);
    box.innerHTML = `
      <div class="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-1.5">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-bold text-white">${escapeHtml(s.from_code)} → ${escapeHtml(s.to_code)} (₺${Number(s.gross_try).toLocaleString('tr-TR')})</span>
          <span class="px-2.5 py-0.5 rounded-full bg-white/[0.08] text-xs font-semibold text-white">${escapeHtml(cleanVerdict)}</span>
        </div>
        <div class="font-mono text-slate-200">Masraf: Stopaj -₺${Number(s.stopaj_cost_try).toLocaleString('tr-TR')} + ${Number(s.valor_days)}g Valör -₺${Number(s.valor_cost_try).toLocaleString('tr-TR')} = -₺${Number(s.total_switch_cost_try).toLocaleString('tr-TR')} · Amorti: ${escapeHtml(bTxt)}</div>
        <div class="text-slate-300 leading-relaxed">${escapeHtml(s.summary_tr)}</div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<div class="text-rose-300">Hata: ${escapeHtml(String(err))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function analyzeAlphaAsset(presetSym = null) {
  const inp = document.getElementById('alpha-asset-input');
  const box = document.getElementById('alpha-single-result');
  const btn = document.getElementById('alpha-asset-btn');
  if (!inp || !box) return;
  if (presetSym) inp.value = presetSym;
  const sym = inp.value.trim();
  if (!sym) return;
  if (btn) btn.disabled = true;
  box.classList.remove('hidden');
  box.innerHTML = '<div class="mt-2 h-20 rounded-xl skeleton-shimmer"></div>';
  try {
    const res = await fetch(`/api/alpha/asset/${encodeURIComponent(sym)}`);
    if (!res.ok) {
      box.innerHTML = `<div class="text-xs text-rose-300 pt-2">${escapeHtml(sym.toUpperCase())} için yeterli veri bulunamadı.</div>`;
      return;
    }
    const d = await res.json();
    const cur = d.currency || 'USD';
    const rp = d.risk_plan || {};
    const labels = {
      UPTREND_PULLBACK: 'Yükselişteyken düşüp döndüğünde',
      DEEP_VALUE_REBOUND: 'Büyük çöküşten toparlandığında',
      MOMENTUM_BREAKOUT: 'Zirve kırıp yükselişe geçtiğinde',
    };
    const strats = Object.entries(d.strategies || {}).map(([k, v]) => {
      const iconHtml = v.qualified
        ? '<i class="fa-solid fa-circle-check text-emerald-400 mr-1.5"></i>'
        : '<i class="fa-solid fa-circle-xmark text-rose-400 mr-1.5"></i>';
      const lbl = labels[k] || k;
      const detail = v.trades > 0 ? `${v.trades} kez oldu → %${v.win_rate_pct} kazandırdı (ort. ${v.avg_return_pct >= 0 ? '+' : ''}${v.avg_return_pct}%)` : 'Son 5 yılda yeterli örnek yok';
      return `<div class="text-xs text-slate-200">${iconHtml}<strong>${escapeHtml(lbl)}:</strong> ${escapeHtml(detail)}</div>`;
    }).join('');
    const cleanVerdict = _stripLeadingEmoji(d.verdict_tr || d.status);
    box.innerHTML = `
      <div class="mt-2 p-4 rounded-xl bg-white/[0.03] border border-white/[0.1] space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2 text-sm font-bold text-white">
          <span>${escapeHtml(d.symbol)} · ${escapeHtml(_fmtAlphaPrice(d.price, cur))} (Bugün ${Number(d.chg_1d_pct) >= 0 ? '+' : ''}${Number(d.chg_1d_pct)}%)</span>
          <span class="px-3 py-0.5 rounded-full bg-white/[0.08] text-xs">${escapeHtml(cleanVerdict)}</span>
        </div>
        <div class="text-xs text-slate-200 leading-relaxed">${escapeHtml(d.plain_reason || '')}</div>
        <div class="space-y-1 pt-1">${strats}</div>
        <div class="text-xs font-mono text-sky-300 pt-1">Zarar Kes (altına inerse sat): ${escapeHtml(_fmtAlphaPrice(rp.stop_loss, cur))} (${Number(rp.stop_pct)}%) · Hedef: ${escapeHtml(_fmtAlphaPrice(rp.target_1, cur))} (+${Number(rp.target_1_pct)}%) · En fazla %${Number(rp.max_pos_pct)} koy</div>
      </div>
    `;
  } catch (err) {
    box.innerHTML = `<div class="text-xs text-rose-300 pt-2">Hata: ${escapeHtml(String(err))}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

let qrBacktestChartInstance = null;
function setQuantBudget(val) {
  const inp = document.getElementById('qr-budget-input');
  if (inp) inp.value = val;
  loadQuantRadar(false);
}

async function loadQuantRadar(forceRefresh = false) {
  const regBox = document.getElementById('qr-regime-box');
  if (!regBox) return;
  const iconEl = document.getElementById('qr-refresh-icon');
  const updEl = document.getElementById('qr-updated-at');
  const budgetEl = document.getElementById('qr-budget-input');
  const modeEl = document.getElementById('qr-stress-mode');
  const budgetVal = Math.max(100, Number(budgetEl?.value || 6000));
  const stressMode = modeEl?.value || 'plan10k';

  if (iconEl && forceRefresh) iconEl.classList.add('fa-spin');
  try {
    const url = `/api/quant/radar?budget_try=${encodeURIComponent(budgetVal)}&stress_mode=${encodeURIComponent(stressMode)}&refresh=${forceRefresh ? 'true' : 'false'}`;
    const q = await fetch(url).then(r => r.json());
    if (!q || q.status !== 'ok') return;

    if (updEl && q.updated_at) updEl.innerText = `Son kontrol: ${q.updated_at}`;
    const reg = q.overall_regime || {};
    const lvl = reg.level || 'GREEN';
    const scoreVal = Number(reg.overall_score || 0);
    const vixVal = Number(reg.vix || 0);
    const vixComment = vixVal >= 28 ? 'Panik Yüksek' : (vixVal >= 22 ? 'Tedirgin' : 'Sakin');
    const regStyle = lvl === 'RED'
      ? { wrap: 'bg-rose-500/10 border-rose-500/40', badge: 'bg-rose-500/25 text-rose-100', bar: 'bg-rose-500', dot: 'bg-rose-500 shadow-[0_0_10px_rgba(244,63,94,0.6)]', shortTitle: 'TEHLİKE / KIRMIZI DURUM — RİSKLİ FON ALMA, PARAYI KORU' }
      : (lvl === 'YELLOW'
        ? { wrap: 'bg-amber-500/10 border-amber-500/40', badge: 'bg-amber-500/25 text-amber-100', bar: 'bg-amber-400', dot: 'bg-amber-400 shadow-[0_0_10px_rgba(245,158,11,0.6)]', shortTitle: 'DİKKAT / SARI DURUM — BÜYÜME ALIMINI YARIYA KIS' }
        : { wrap: 'bg-emerald-500/10 border-emerald-500/40', badge: 'bg-emerald-500/25 text-emerald-100', bar: 'bg-emerald-400', dot: 'bg-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.6)]', shortTitle: 'GÜVENLİ / YEŞİL DURUM — %55 BÜYÜME PLANINA DEVAM' });

    regBox.className = `p-5 sm:p-6 rounded-2xl border space-y-4 ${regStyle.wrap}`;
    regBox.innerHTML = `
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3 text-base sm:text-lg font-extrabold text-white">
          <span class="w-3.5 h-3.5 rounded-full shrink-0 ${regStyle.dot}"></span>
          <span>${escapeHtml(regStyle.shortTitle)}</span>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs font-mono">
          <span class="px-3 py-1 rounded-full ${regStyle.badge} font-bold">Piyasa Sağlığı: ${scoreVal} / 100</span>
          <span class="px-3 py-1 rounded-full bg-white/[0.08] text-slate-100">Dünya Korku Seviyesi: ${vixVal.toFixed(1)} (${vixComment})</span>
          <span class="px-3 py-1 rounded-full bg-white/[0.08] text-slate-100">ABD Faiz Baskısı: %${Number(reg.us10y || 0).toFixed(2)}</span>
        </div>
      </div>
      <div class="w-full h-2.5 rounded-full bg-white/[0.08] overflow-hidden">
        <div class="h-full ${regStyle.bar} transition-all duration-500" style="width: ${Math.max(4, Math.min(100, scoreVal))}%"></div>
      </div>
      <p class="text-sm text-slate-100 leading-relaxed">${escapeHtml(reg.summary || '')}</p>
    `;

    // Dinamik Dağıtım Reçetesi
    const dyn = q.dynamic_plan || {};
    const dynAdv = document.getElementById('qr-dyn-advice');
    if (dynAdv) dynAdv.innerText = `${dyn.rebalance_advice || ''} (Şu anki öneri: %${Number(dyn.growth_total_pct || 0)} Büyüme / %${Number(dyn.shield_total_pct || 0)} Güvenli Kalkan)`;
    const dynGrid = document.getElementById('qr-dynamic-plan');
    if (dynGrid) {
      dynGrid.innerHTML = (dyn.items || []).map(it => {
        const isPaused = Number(it.pct || 0) <= 0;
        return `
          <div class="p-4 sm:p-5 rounded-2xl ${isPaused ? 'bg-[#070c1e]/60 border-white/[0.06] opacity-70' : 'bg-[#070c1e] border-indigo-500/35'} border space-y-2">
            <div class="flex items-center justify-between text-sm">
              <span class="font-bold text-white font-mono text-base">${escapeHtml(it.code)}</span>
              <span class="px-2.5 py-0.5 rounded-lg ${isPaused ? 'bg-rose-500/15 text-rose-300' : 'bg-indigo-500/20 text-indigo-200'} font-mono font-bold text-xs">Bütçenin %${Number(it.pct || 0)}'i</span>
            </div>
            <div class="text-xs text-slate-300">${escapeHtml(it.label || '')}</div>
            <div class="text-2xl font-extrabold text-white font-mono">₺${Number(it.amount_try || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 })}</div>
            <div class="text-xs text-slate-400">${isPaused ? '<i class="fa-solid fa-ban text-rose-400 mr-1"></i>Piyasa düzelene kadar yeni alım durduruldu' : `Yaklaşık ${Number(it.shares || 0).toLocaleString('tr-TR')} adet al (1 pay = ₺${Number(it.price || 0).toFixed(4)})`}</div>
          </div>
        `;
      }).join('');
    }

    // Fon Bazlı Sağlık & 30G Tahmin Bandı
    const fGrid = document.getElementById('qr-funds-grid');
    if (fGrid) {
      const fMapQ = q.funds || {};
      const prefOrder = ['AFT', 'TI3', 'TCA', 'ZBJ', 'KZL', 'TZL'];
      const ownedSetQ = new Set((globalPortfolio?.funds || []).map(f => String(f.code || '').toUpperCase()));
      const order = Object.keys(fMapQ).sort((a, b) => {
        const ao = ownedSetQ.has(a) ? 0 : 1;
        const bo = ownedSetQ.has(b) ? 0 : 1;
        if (ao !== bo) return ao - bo;
        const ai = prefOrder.indexOf(a);
        const bi = prefOrder.indexOf(b);
        if (ai !== -1 && bi !== -1) return ai - bi;
        if (ai !== -1) return -1;
        if (bi !== -1) return 1;
        return a.localeCompare(b);
      });
      fGrid.innerHTML = order.map(code => {
        const f = fMapQ[code];
        if (!f) return '';
        const r = f.regime || {};
        const t = f.technicals || {};
        const p30 = t.proj_30d || {};
        const rLvl = r.level || 'GREEN';
        const hs = Number(r.health_score || 0);
        const rsiVal = Number(t.rsi14 || 50);
        const rsiLabel = rsiVal < 33 ? 'Çok ucuzlamış' : (rsiVal < 46 ? 'Zayıf' : (rsiVal > 70 ? 'Aşırı şişmiş' : 'Normal'));
        const d50 = Number(t.dist_sma50_pct || 0);
        const d50Cls = d50 >= 0 ? 'text-emerald-400' : 'text-rose-400';
        const rBadgeCls = rLvl === 'RED'
          ? 'bg-rose-500/15 text-rose-200 border-rose-500/35'
          : (rLvl === 'YELLOW' ? 'bg-amber-500/15 text-amber-200 border-amber-500/35' : 'bg-emerald-500/15 text-emerald-200 border-emerald-500/35');
        const barCls = rLvl === 'RED' ? 'bg-rose-500' : (rLvl === 'YELLOW' ? 'bg-amber-400' : 'bg-emerald-400');
        const reasonsHtml = (r.reasons || []).map(rs => `<li class="text-xs sm:text-sm text-slate-300 leading-snug">• ${escapeHtml(rs)}</li>`).join('');
        return `
          <div class="p-5 rounded-2xl bg-[#070c1e] border border-white/[0.09] space-y-3.5">
            <div class="flex flex-wrap items-start justify-between gap-2.5">
              <div class="space-y-0.5">
                <div class="text-sm sm:text-base font-bold text-white"><span class="font-mono text-indigo-300">${escapeHtml(code)}</span> · ${escapeHtml(f.name || '')}</div>
                <div class="text-xs sm:text-sm text-slate-300 font-medium"><i class="fa-solid fa-arrow-right text-indigo-400 mr-1.5 text-xs"></i>${escapeHtml(r.action_short || '')}</div>
              </div>
              <span class="px-3 py-1 rounded-xl border text-xs font-bold whitespace-nowrap ${rBadgeCls}">Sağlık: ${hs}/100 · ${escapeHtml(r.badge || '')}</span>
            </div>
            <div class="w-full h-2 rounded-full bg-white/[0.06] overflow-hidden">
              <div class="h-full ${barCls}" style="width: ${Math.max(4, Math.min(100, hs))}%"></div>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs bg-white/[0.03] p-3 rounded-xl border border-white/[0.06]">
              <div>
                <div class="text-slate-300 text-xs">Güç Puanı (RSI)</div>
                <div class="text-white font-mono font-bold text-sm mt-0.5">${rsiVal.toFixed(0)} <span class="text-xs font-sans font-normal text-slate-300">(${rsiLabel})</span></div>
              </div>
              <div>
                <div class="text-slate-300 text-xs">2,5 Aylık Ort. Farkı</div>
                <div class="${d50Cls} font-mono font-bold text-sm mt-0.5">${d50 >= 0 ? '+' : ''}%${d50.toFixed(1)}</div>
              </div>
              <div>
                <div class="text-slate-300 text-xs">1 Aylık Beklenti</div>
                <div class="text-slate-100 font-mono font-bold text-sm mt-0.5">%${Number(p30.p10_pct || 0).toFixed(0)} .. ${Number(p30.p90_pct || 0) >= 0 ? '+' : ''}%${Number(p30.p90_pct || 0).toFixed(0)}</div>
              </div>
              <div>
                <div class="text-slate-300 text-xs">1 Ayda %5 Düşme Riski</div>
                <div class="${Number(p30.prob_drop_5pct || 0) >= 30 ? 'text-rose-300' : 'text-emerald-300'} font-mono font-bold text-sm mt-0.5">%${Number(p30.prob_drop_5pct || 0).toFixed(0)} ihtimal</div>
              </div>
            </div>
            <ul class="space-y-1">${reasonsHtml}</ul>
          </div>
        `;
      }).join('');
    }

    // Canlı TL Stres Testi
    const st = q.stress_test || {};
    const stSub = document.getElementById('qr-stress-subtitle');
    if (stSub) stSub.innerText = `Hesaplanan sepet: ${st.label || ''}`;
    const stGrid = document.getElementById('qr-stress-grid');
    if (stGrid) {
      stGrid.innerHTML = (st.scenarios || []).map(sc => {
        const pnl = Number(sc.pnl_try || 0);
        const cls = pnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
        const recDays = Number(sc.recovery_days_zbj || 0);
        const recTxt = recDays > 0 && recDays < 999 ? ` • ZBJ faizi ${recDays} günde kapatır` : '';
        return `
          <div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.07] flex flex-wrap items-center justify-between gap-2 text-[13px] sm:text-sm">
            <div class="min-w-0">
              <div class="font-bold text-white">${escapeHtml(sc.name || '')}</div>
              <div class="text-xs text-slate-400 mt-0.5">${escapeHtml(sc.desc || '')}</div>
            </div>
            <div class="text-right font-mono shrink-0">
              <div class="${cls} font-extrabold text-sm sm:text-base">${pnl >= 0 ? '+' : ''}₺${pnl.toLocaleString('tr-TR', { maximumFractionDigits: 0 })} (${Number(sc.pnl_pct || 0).toFixed(1)}%)</div>
              <div class="text-xs font-sans text-slate-300 mt-0.5">Kalan para: ₺${Number(sc.after_total_try || 0).toLocaleString('tr-TR', { maximumFractionDigits: 0 })}${escapeHtml(recTxt)}</div>
            </div>
          </div>
        `;
      }).join('');
    }

    // Gerçek Backtest
    const bt = q.backtest || {};
    const btStats = document.getElementById('qr-backtest-stats');
    const btSub = document.getElementById('qr-backtest-sub');
    if (bt.available && btStats) {
      if (btSub) btSub.innerText = `${bt.start_date} – ${bt.end_date} arası gerçek fiyatlarla test (${bt.trading_days} gün): Tehlikeli ${bt.red_days + bt.yellow_days} günde fren açıldı.`;
      btStats.innerHTML = `
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.07] space-y-1">
          <div class="text-slate-300 text-xs">Korumasız (%55 Büyüme)</div>
          <div class="font-mono font-extrabold text-lg text-slate-100">+%${Number(bt.static_return_pct || 0).toFixed(1)}</div>
          <div class="text-xs text-rose-400">En sert düşüş: %${Number(bt.static_max_drawdown_pct || 0).toFixed(1)}</div>
        </div>
        <div class="p-3.5 rounded-xl bg-indigo-500/12 border border-indigo-500/35 space-y-1">
          <div class="text-indigo-200 text-xs font-bold">Akıllı Fren (Bu Sistem)</div>
          <div class="font-mono font-extrabold text-lg text-white">+%${Number(bt.smart_return_pct || 0).toFixed(1)}</div>
          <div class="text-xs text-emerald-300">En sert düşüş: %${Number(bt.smart_max_drawdown_pct || 0).toFixed(1)}</div>
        </div>
        <div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.07] space-y-1">
          <div class="text-slate-300 text-xs">Sadece ZBJ (PPF)</div>
          <div class="font-mono font-extrabold text-lg text-emerald-400">+%${Number(bt.ppf_return_pct || 0).toFixed(1)}</div>
          <div class="text-xs text-emerald-300">Kayıp koruması: +${Number(bt.drawdown_saved_pct || 0).toFixed(1)} puan</div>
        </div>
      `;
      const canvas = document.getElementById('qrBacktestChart');
      const ser = bt.series || {};
      if (canvas && ser.dates && ser.dates.length) {
        if (qrBacktestChartInstance) qrBacktestChartInstance.destroy();
        qrBacktestChartInstance = new Chart(canvas.getContext('2d'), {
          type: 'line',
          data: {
            labels: ser.dates.map(d => d.slice(5)),
            datasets: [
              { label: 'Akıllı Fren Motoru (Bu Sistem)', data: ser.smart, borderColor: '#818cf8', borderWidth: 3, pointRadius: 0, tension: 0.2 },
              { label: 'Korumasız (%55 Büyüme)', data: ser.static, borderColor: '#fb7185', borderWidth: 2, borderDash: [5, 4], pointRadius: 0, tension: 0.2 },
              { label: 'Sadece ZBJ (Güvenli Faiz)', data: ser.ppf, borderColor: '#34d399', borderWidth: 1.8, pointRadius: 0, tension: 0.2 },
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { labels: { color: '#e2e8f0', font: { family: "'Plus Jakarta Sans', sans-serif", size: 12 }, boxWidth: 14 } } },
            scales: {
              x: { grid: { display: false }, ticks: { color: '#9fb0c4', font: { family: "'JetBrains Mono', monospace", size: 12 }, maxTicksLimit: 7 } },
              y: { grid: { color: 'rgba(255,255,255,0.06)' }, ticks: { color: '#cbd5e1', font: { family: "'JetBrains Mono', monospace", size: 12 } } }
            }
          }
        });
      }
    }
    if (forceRefresh) showToast("Erken Uyarı & Tahmin motoru canlı verilerle yenilendi!");
  } catch (err) {
    console.error('Quant radar err:', err);
  } finally {
    if (iconEl) iconEl.classList.remove('fa-spin');
  }
}

let radarPerfChartInstance = null;
function renderRadarPerformanceChart(fMap) {
  const canvas = document.getElementById('radarPerfChart');
  if (!canvas || !fMap) return;
  const COLOR_MAP = {
    ZBJ: { label: 'ZBJ Başak (PPF)', color: '#10b981' },
    TZL: { label: 'TZL Para Piyasası', color: '#34d399' },
    TCA: { label: 'TCA Altın', color: '#fbbf24' },
    AFT: { label: 'AFT Teknoloji', color: '#38bdf8' },
    KZL: { label: 'KZL Altın', color: '#f97316' },
    TI3: { label: 'TI3 BIST30', color: '#c084fc' },
  };
  const EXTRA_COLORS = ['#a78bfa', '#f472b6', '#22d3ee', '#fb7185', '#facc15', '#4ade80'];
  let extraIdx = 0;
  const metaList = Object.keys(fMap)
    .filter(code => Array.isArray(fMap[code]?.sparkline_points) && fMap[code].sparkline_points.length >= 2)
    .map(code => {
      if (COLOR_MAP[code]) return { code, ...COLOR_MAP[code] };
      const col = EXTRA_COLORS[extraIdx++ % EXTRA_COLORS.length];
      return { code, label: `${code} (${fMap[code]?.category || 'TEFAS'})`, color: col };
    });
  const basePts = (metaList[0] && fMap[metaList[0].code]?.sparkline_points) || [];
  if (basePts.length < 2) return;
  const dates = basePts.map(pt => pt.date);
  const labels = dates.map(d => d.slice(5));

  const rawPriceLookup = {};
  const datasets = metaList.map(m => {
    const sp = (fMap[m.code] && fMap[m.code].sparkline_points) || [];
    const pByDate = {};
    sp.forEach(item => { pByDate[item.date] = Number(item.price || 0); });
    rawPriceLookup[m.code] = dates.map(d => pByDate[d] || null);
    const firstP = rawPriceLookup[m.code].find(v => v && v > 0) || 0;
    const pctSeries = rawPriceLookup[m.code].map(v => (v && firstP > 0) ? Number((((v - firstP) / firstP) * 100).toFixed(2)) : null);
    return {
      label: m.label,
      data: pctSeries,
      borderColor: m.color,
      backgroundColor: m.color,
      borderWidth: m.code === 'ZBJ' || m.code === 'TCA' || m.code === 'AFT' ? 2.5 : 1.6,
      borderDash: m.code === 'KZL' || m.code === 'TI3' ? [4, 4] : [],
      tension: 0.3,
      pointRadius: 2,
      pointHoverRadius: 5,
      spanGaps: true
    };
  });

  if (radarPerfChartInstance) {
    radarPerfChartInstance.$dates = dates;
    radarPerfChartInstance.$rawPrices = rawPriceLookup;
    radarPerfChartInstance.$codes = metaList.map(x => x.code);
    radarPerfChartInstance.data.labels = labels;
    radarPerfChartInstance.data.datasets = datasets;
    radarPerfChartInstance.update('none');
    return;
  }

  const radarCrosshair = typeof window.createVerticalCrosshairPlugin === 'function'
    ? window.createVerticalCrosshairPlugin('radarCrosshair', 'rgba(99, 102, 241, 0.4)')
    : { id: 'radarCrosshair' };

  radarPerfChartInstance = new Chart(canvas.getContext('2d'), {
    type: 'line',
    plugins: [radarCrosshair],
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          align: 'end',
          labels: { color: '#cbd5e1', usePointStyle: true, boxWidth: 8, padding: 14, font: { family: "'Plus Jakarta Sans', sans-serif", size: 12 } }
        },
        tooltip: {
          backgroundColor: 'rgba(8, 12, 24, 0.97)',
          titleColor: '#f8fafc',
          bodyColor: '#e2e8f0',
          borderColor: 'rgba(129, 140, 248, 0.45)',
          borderWidth: 1,
          padding: 12,
          cornerRadius: 12,
          bodyFont: { family: "'JetBrains Mono', monospace", size: 12 },
          callbacks: {
            title: items => {
              const idx = items?.[0]?.dataIndex ?? 0;
              const fullD = (radarPerfChartInstance?.$dates || [])[idx] || '';
              return fullD ? `${fullD} · TEFAS Kapanışları` : '';
            },
            label: c => {
              const code = (radarPerfChartInstance?.$codes || [])[c.datasetIndex];
              const idx = c.dataIndex;
              const rawP = ((radarPerfChartInstance?.$rawPrices || {})[code] || [])[idx];
              const val = Number(c.parsed.y || 0);
              const sign = val >= 0 ? '+' : '';
              const pTxt = rawP ? ` (₺${Number(rawP).toFixed(4)})` : '';
              return ` ${c.dataset.label}: ${sign}%${val.toFixed(2)}${pTxt}`;
            }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#9fb0c4', font: { family: "'JetBrains Mono', monospace", size: 12 } } },
        y: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#cbd5e1', font: { family: "'JetBrains Mono', monospace", size: 12 }, callback: v => `${v >= 0 ? '+' : ''}%${Number(v).toFixed(1)}` }
        }
      }
    }
  });
  radarPerfChartInstance.$dates = dates;
  radarPerfChartInstance.$rawPrices = rawPriceLookup;
  radarPerfChartInstance.$codes = metaList.map(x => x.code);
}

async function loadTefasLeague(forceRefresh = false) {
  const grid = document.getElementById('tefas-league-grid');
  const updEl = document.getElementById('league-updated-at');
  const iconEl = document.getElementById('league-refresh-icon');
  if (!grid) return;
  if (iconEl && forceRefresh) iconEl.classList.add('fa-spin');
  try {
    const res = await fetch('/api/league' + (forceRefresh ? '?refresh=1' : '')).then(r => r.json());
    if (updEl && res.updated_at) updEl.innerText = `Güncelleme: ${res.updated_at}`;
    const leagues = res.leagues || [];
    if (!leagues.length) {
      grid.innerHTML = '<div class="p-4 rounded-2xl bg-[#070c1e] border border-white/[0.07] text-sm text-slate-400 col-span-full">TEFAS verisi alınamadı.</div>';
      return;
    }
    grid.innerHTML = leagues.map(lg => {
      const rowsHtml = (lg.funds || []).map(f => {
        const rankCls = f.rank === 1 ? 'text-amber-300' : (f.rank === 2 ? 'text-slate-200' : (f.rank === 3 ? 'text-amber-500/80' : 'text-slate-400'));
        const medal = `<span class="${rankCls} font-mono text-xs font-extrabold">#${f.rank}</span>`;
        const ownCls = f.is_owned ? 'bg-indigo-500/12 border border-indigo-500/35' : 'bg-[#070c1e]/80 border border-white/[0.05]';
        const c30 = Number(f.chg_30d || 0);
        const c7 = Number(f.chg_7d || 0);
        const c30Cls = c30 >= 0 ? 'text-emerald-400' : 'text-rose-400';
        return `
          <div class="p-3 rounded-xl ${ownCls} flex items-center justify-between gap-2 text-[13px] font-mono">
            <div class="min-w-0">
              <div class="flex items-center gap-1.5 text-white font-bold">
                <span>${medal}</span>
                <span>${escapeHtml(f.code)}</span>
                ${f.is_owned ? '<span class="px-2 py-0.5 rounded bg-indigo-500/25 text-indigo-200 text-xs font-sans font-semibold">Senin</span>' : ''}
              </div>
              <div class="text-xs text-slate-300 font-sans truncate mt-0.5">${escapeHtml(f.name || '')}</div>
            </div>
            <div class="text-right shrink-0">
              <div class="${c30Cls} font-bold">30G ${c30 >= 0 ? '+' : ''}%${c30.toFixed(2)}</div>
              <div class="text-xs text-slate-400">7G ${c7 >= 0 ? '+' : ''}%${c7.toFixed(2)}</div>
            </div>
          </div>
        `;
      }).join('');
      return `
        <div class="p-4 rounded-2xl bg-white/[0.025] border border-white/[0.08] flex flex-col justify-between space-y-3">
          <div class="space-y-2.5">
            <div class="text-[13px] font-bold text-white border-b border-white/[0.07] pb-2">${escapeHtml(lg.title)}</div>
            <div class="space-y-2">${rowsHtml}</div>
          </div>
          <div class="p-3 rounded-xl bg-[#060a17] border border-amber-500/20 text-[13px] text-slate-200 leading-relaxed">
            ${escapeHtml(lg.verdict || '')}
          </div>
        </div>
      `;
    }).join('');
    if (forceRefresh) showToast("TEFAS Lig Tablosu canlı verilerle güncellendi!");
  } catch (e) {
    console.error("League load error:", e);
  } finally {
    if (iconEl) iconEl.classList.remove('fa-spin');
  }
}

// ======================================================================
// CANLI PİYASA & MAKRO HABERLER MODÜLÜ (LLM RADARI)
// ======================================================================

async function fetchOpportunityRadar() {
  try {
    const res = await fetch('/api/market/opportunity-radar').then(r => r.json());
    applyOpportunityRadarPayload(res);
  } catch (err) {
    console.error("Fırsat radarı yükleme hatası:", err);
  }
}

function applyOpportunityRadarPayload(res) {
  if (!res || !res.items) return;
  try {
    const bannerEl = document.getElementById('terminal-quant-banner');
    const ov = res.overall_regime || {};
    if (bannerEl && ov.level) {
      const isRed = ov.level === 'RED';
      const isYel = ov.level === 'YELLOW';
      bannerEl.className = `p-4 rounded-2xl border text-sm flex flex-wrap items-center justify-between gap-3 ${
        isRed ? 'bg-rose-500/10 border-rose-500/35 text-rose-100'
        : (isYel ? 'bg-amber-500/10 border-amber-500/35 text-amber-100' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-100')
      }`;
      const dotCls = isRed ? 'bg-rose-500' : (isYel ? 'bg-amber-400' : 'bg-emerald-400');
      bannerEl.innerHTML = `
        <div class="flex items-center gap-2.5 min-w-0">
          <span class="w-2.5 h-2.5 rounded-full shrink-0 ${dotCls}"></span>
          <span class="font-semibold truncate">${escapeHtml(ov.title || '')}</span>
        </div>
        <div class="flex items-center gap-3 font-mono text-xs shrink-0">
          <span>Sağlık: ${Number(ov.overall_score || 0)}/100</span>
          <span>• VIX: ${Number(ov.vix || 0).toFixed(1)}</span>
        </div>
      `;
    }

    const grid = document.getElementById('opp-radar-grid');
    if (!grid) return;

    grid.innerHTML = res.items.map(item => {
      const isPositive = (item.change_pct || 0) >= 0;
      const chgColor = isPositive ? 'text-emerald-400' : 'text-rose-400';
      const chgSign = isPositive ? '+' : '';
      
      let badgeClass = 'bg-white/[0.06] text-slate-300';
      if (item.badge_color === 'emerald') badgeClass = 'bg-emerald-500/10 text-emerald-300';
      else if (item.badge_color === 'amber') badgeClass = 'bg-amber-500/10 text-amber-300';
      else if (item.badge_color === 'rose') badgeClass = 'bg-rose-500/15 text-rose-300 border border-rose-500/30';
      else if (item.badge_color === 'purple') badgeClass = 'bg-indigo-500/10 text-indigo-300';
      else if (item.badge_color === 'cyan') badgeClass = 'bg-sky-500/10 text-sky-300';

      const cleanTitle = String(item.name || '').replace('Ziraat Portföy ', '').replace('Ak Portföy ', '');
      const signalLabel = String(item.signal || '').toLocaleLowerCase('tr-TR').replace(/(^|\s)\S/g, l => l.toLocaleUpperCase('tr-TR'));

      const weeklyPct = Number(item.weekly_pct || 0);
      const wSign = weeklyPct >= 0 ? '+' : '';
      const ddPct = Number(item.drawdown_pct || 0);
      const ddText = ddPct < 0 ? ` • Zirveden %${Math.abs(ddPct).toFixed(1)} düşük` : '';

      const ownedBadge = item.is_owned ? `<span class="px-2 py-0.5 rounded-md bg-violet-500/20 text-violet-200 border border-violet-500/30 text-xs font-medium whitespace-nowrap">Elindeki</span>` : '';
      return `
        <div class="p-4 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-2 hover:border-violet-400/30 transition cursor-pointer" onclick="quickBuyFromRadar('${escapeHtml(item.code)}')">
          <div class="flex items-center justify-between gap-2 text-sm">
            <span class="font-semibold text-white truncate">${escapeHtml(item.code)} · ${escapeHtml(cleanTitle)}</span>
            <div class="flex items-center gap-1.5 shrink-0">${ownedBadge}<span class="px-2.5 py-0.5 rounded-md ${badgeClass} text-xs font-medium whitespace-nowrap">${escapeHtml(signalLabel)}</span></div>
          </div>
          <div class="text-lg font-bold text-white font-mono">₺${Number(item.price).toFixed(4)} <span class="text-xs ${chgColor} font-normal">(1G ${chgSign}%${Number(item.change_pct).toFixed(2)} • 7G ${wSign}%${weeklyPct.toFixed(2)})</span></div>
          <div class="text-xs text-slate-300 leading-relaxed">${escapeHtml(item.advice)}${escapeHtml(ddText)}</div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error("Fırsat radarı yükleme hatası:", err);
  }
}

async function initProjectionChart(monthlyDeposit = 5500) {
  const canvas = document.getElementById('projectionChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  try {
    const res = await fetch(`/api/simulation/monte-carlo?monthly_deposit=${monthlyDeposit}`).then(r => r.json());
    if (!res || !res.labels) return;

    // Update Quant Stat Cards
    const probEl = document.getElementById('mc-prob-val');
    const p90El = document.getElementById('mc-p90-val');
    const p50El = document.getElementById('mc-p50-val');
    const p10El = document.getElementById('mc-p10-val');
    if (probEl) probEl.innerText = `%${res.success_probability_pct}`;
    if (p90El) p90El.innerText = '₺' + Math.round(res.stats.best_case_p90).toLocaleString('tr-TR');
    if (p50El) p50El.innerText = '₺' + Math.round(res.stats.expected_p50).toLocaleString('tr-TR');
    if (p10El) p10El.innerText = '₺' + Math.round(res.stats.worst_case_p10).toLocaleString('tr-TR');
    const paramsBadge = document.getElementById('mc-params-badge');
    if (paramsBadge && res.portfolio_params) {
      paramsBadge.innerText = `Aylık Getiri: %${res.portfolio_params.mu_monthly_pct} • Risk: %${res.portfolio_params.sigma_monthly_pct}`;
    }

    if (chartInstance) {
      chartInstance.data.labels = res.labels;
      chartInstance.data.datasets[0].data = res.p90_bull;
      chartInstance.data.datasets[1].data = res.p50_median;
      chartInstance.data.datasets[2].data = res.p10_bear;
      chartInstance.data.datasets[3].data = res.invested;
      chartInstance.update('none');
      return;
    }

    const gradientBull = ctx.createLinearGradient(0, 0, 0, 320);
    gradientBull.addColorStop(0, 'rgba(16, 185, 129, 0.14)');
    gradientBull.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

    const gradientMedian = ctx.createLinearGradient(0, 0, 0, 320);
    gradientMedian.addColorStop(0, 'rgba(99, 102, 241, 0.28)');
    gradientMedian.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

    const targetLinePlugin = {
      id: 'targetLinePlugin',
      afterDatasetsDraw(chart) {
        const yScale = chart.scales.y;
        const xScale = chart.scales.x;
        if (!yScale || !xScale) return;
        const tgt = typeof getGoalTargetTry === 'function' ? getGoalTargetTry() : 650000;
        const yVal = yScale.getPixelForValue(tgt);
        if (yVal < yScale.top || yVal > yScale.bottom) return;
        const c = chart.ctx;
        c.save();
        c.beginPath();
        c.setLineDash([4, 4]);
        c.strokeStyle = 'rgba(244, 63, 94, 0.55)';
        c.lineWidth = 1.25;
        c.moveTo(xScale.left, yVal);
        c.lineTo(xScale.right, yVal);
        c.stroke();
        c.fillStyle = 'rgba(251, 113, 133, 0.9)';
        c.font = '600 11px Inter, sans-serif';
        c.textAlign = 'right';
        c.fillText(`Hedef: ₺${Math.round(tgt / 1000)}K`, xScale.right - 6, yVal - 6);
        c.restore();
      }
    };

    function createVerticalCrosshairPlugin(pluginId, strokeColor) {
      return {
        id: pluginId,
        afterDatasetsDraw(chart) {
          const active = chart.tooltip && chart.tooltip.getActiveElements ? chart.tooltip.getActiveElements() : [];
          if (!active || !active.length) return;
          const x = active[0].element.x;
          const yScale = chart.scales.y;
          if (!yScale || !Number.isFinite(x)) return;
          const c = chart.ctx;
          c.save();
          c.beginPath();
          c.setLineDash([3, 3]);
          c.lineWidth = 1;
          c.strokeStyle = strokeColor;
          c.moveTo(x, yScale.top);
          c.lineTo(x, yScale.bottom);
          c.stroke();
          c.restore();
        }
      };
    }
    window.createVerticalCrosshairPlugin = createVerticalCrosshairPlugin;

    const mcCrosshairPlugin = createVerticalCrosshairPlugin('mcCrosshairPlugin', 'rgba(129, 140, 248, 0.42)');

    chartInstance = new Chart(ctx, {
      type: 'line',
      plugins: [targetLinePlugin, mcCrosshairPlugin],
      data: {
        labels: res.labels,
        datasets: [
          {
            label: 'İyimser Senaryo (P90)',
            data: res.p90_bull,
            borderColor: '#10b981',
            backgroundColor: gradientBull,
            borderWidth: 2,
            fill: true,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: '#10b981',
            pointHoverBorderColor: '#07090f',
            pointHoverBorderWidth: 2
          },
          {
            label: 'Beklenen Medyan (P50)',
            data: res.p50_median,
            borderColor: '#818cf8',
            backgroundColor: gradientMedian,
            borderWidth: 3,
            fill: true,
            tension: 0.4,
            pointRadius: 3,
            pointHoverRadius: 6,
            pointBackgroundColor: '#818cf8',
            pointBorderColor: '#07090f',
            pointBorderWidth: 1.5
          },
          {
            label: 'Temkinli Senaryo (P10)',
            data: res.p10_bear,
            borderColor: '#f59e0b',
            borderWidth: 2,
            borderDash: [5, 5],
            fill: false,
            tension: 0.4,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: '#f59e0b'
          },
          {
            label: 'Yatırılan Anapara',
            data: res.invested,
            borderColor: '#64748b',
            borderDash: [3, 4],
            borderWidth: 1.5,
            fill: false,
            tension: 0.25,
            pointRadius: 0,
            pointHoverRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        animation: { duration: (window.appPrefs && window.appPrefs.chartAnimations === false) ? 0 : 350, easing: 'easeOutQuart' },
        plugins: {
          legend: {
            position: 'top',
            align: 'end',
            labels: {
              color: '#cbd5e1',
              usePointStyle: true,
              pointStyle: 'circle',
              boxWidth: 8,
              boxHeight: 8,
              padding: 16,
              font: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 12.5, weight: '600' }
            }
          },
          tooltip: {
            backgroundColor: 'rgba(8, 12, 24, 0.97)',
            titleColor: '#f8fafc',
            bodyColor: '#e2e8f0',
            borderColor: 'rgba(129, 140, 248, 0.45)',
            borderWidth: 1,
            padding: 14,
            cornerRadius: 12,
            titleFont: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 13, weight: '700' },
            bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
            callbacks: {
              title: function(items) {
                if (!items || !items.length) return '';
                const idx = items[0].dataIndex;
                const rawLbl = String(items[0].label || '');
                const mMatch = rawLbl.match(/\d+/);
                const monthOffset = mMatch ? parseInt(mMatch[0], 10) : idx;
                const d = new Date();
                d.setMonth(d.getMonth() + monthOffset);
                const moNames = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
                return `${rawLbl} · ${moNames[d.getMonth()]} ${d.getFullYear()}`;
              },
              label: function(c) {
                const val = Math.round(c.parsed.y);
                const usd = Number(globalMarket?.macro?.usd_try || 0);
                const usdStr = usd > 0 ? ` (~$${Math.round(val / usd).toLocaleString('tr-TR')})` : '';
                return ` ${c.dataset.label}: ₺${val.toLocaleString('tr-TR')}${c.datasetIndex === 1 ? usdStr : ''}`;
              },
              afterBody: function(items) {
                const med = items.find(i => i.datasetIndex === 1);
                const inv = items.find(i => i.datasetIndex === 3);
                if (!med || !inv) return [];
                const medVal = Number(med.parsed.y || 0);
                const invVal = Math.max(1, Number(inv.parsed.y || 0));
                const diff = Math.round(medVal - invVal);
                const profitPct = ((diff / invVal) * 100).toFixed(1);
                const goalTarget = typeof getGoalTargetTry === 'function' ? getGoalTargetTry() : 650000;
                const goalPct = ((medVal / goalTarget) * 100).toFixed(1);
                const goalLine = medVal >= goalTarget
                  ? ` Hedef Aşıldı (+₺${Math.round(medVal - goalTarget).toLocaleString('tr-TR')})`
                  : ` ₺${Math.round(goalTarget / 1000)}K Hedefinin %${goalPct}'i`;
                const ppfRate = Number(globalPortfolio?.ppf_daily_rate || 0.0011);
                const passiveMonth = Math.round(medVal * ppfRate * 30.5);
                return [
                  ' ──────────────────────',
                  ` Bileşik Kazanç: ${diff >= 0 ? '+' : ''}₺${diff.toLocaleString('tr-TR')} (${diff >= 0 ? '+' : ''}%${profitPct})`,
                  goalLine,
                  ` Aylık Pasif Getiri Gücü: ~₺${passiveMonth.toLocaleString('tr-TR')}/ay`
                ];
              }
            }
          }
        },
        scales: {
          x: { 
            grid: { display: false },
            border: { display: false },
            ticks: { color: '#cbd5e1', font: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 12, weight: '500' }, padding: 8 }
          },
          y: { 
            grid: { color: 'rgba(148, 163, 184, 0.08)', drawTicks: false },
            border: { display: false },
            ticks: { 
              color: '#cbd5e1', 
              padding: 10,
              font: { family: 'JetBrains Mono, monospace', size: 12 },
              callback: function(v) { return '₺' + Math.round(v / 1000) + 'K'; }
            } 
          }
        }
      }
    });
  } catch (err) {
    console.error("Monte Carlo Chart Error:", err);
  }
}

let simDebounceTimer = null;
function updateSimulation() {
  const sliderEl = document.getElementById('slider-deposit');
  if (!sliderEl) return;
  sliderEl.dataset.userMoved = '1';
  const val = parseInt(sliderEl.value, 10);
  document.getElementById('slider-val').innerText = '₺' + val.toLocaleString('tr-TR');
  if (typeof updateGoalEtaBadge === 'function') updateGoalEtaBadge(val);
  if (simDebounceTimer) clearTimeout(simDebounceTimer);
  simDebounceTimer = setTimeout(() => {
    initProjectionChart(val);
  }, 40);
}


window.__activeNwDays = (() => {
  try {
    const raw = localStorage.getItem('fin_nw_days');
    const parsed = raw !== null ? parseInt(raw, 10) : 30;
    return [0, 7, 30].includes(parsed) ? parsed : 30;
  } catch (e) { return 30; }
})();
window.__activeNwUnit = (() => {
  try {
    const u = localStorage.getItem('fin_nw_unit');
    return ['TRY', 'USD', 'GOLD', 'RACE'].includes(u) ? u : 'TRY';
  } catch (e) { return 'TRY'; }
})();
window.__lastNwData = null;

function syncNetWorthControlsUI() {
  const unit = window.__activeNwUnit || 'TRY';
  const days = Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30;
  document.querySelectorAll('#nw-unit-pills .nw-unit-btn').forEach(el => {
    const active = el.dataset.nwUnit === unit;
    el.className = active
      ? 'nw-unit-btn h-8 px-3 rounded-lg bg-emerald-600 text-white font-bold transition'
      : 'nw-unit-btn h-8 px-3 rounded-lg text-slate-300 hover:text-white transition';
  });
  document.querySelectorAll('#nw-range-pills .nw-pill').forEach(el => {
    const active = parseInt(el.dataset.nwDays, 10) === days;
    el.className = active
      ? 'nw-pill h-9 px-3.5 rounded-xl bg-indigo-600 text-white font-semibold transition'
      : 'nw-pill h-9 px-3.5 rounded-xl bg-[#070c1e] text-slate-200 border border-white/[0.1] transition';
  });
}

function setNetWorthUnit(unit, btn) {
  window.__activeNwUnit = unit || 'TRY';
  try { localStorage.setItem('fin_nw_unit', window.__activeNwUnit); } catch (e) {}
  syncNetWorthControlsUI();
  if (window.__lastNwData) renderNetWorthChartFromData(window.__lastNwData);
}

function renderNetWorthChartFromData(res) {
  const pts = (res && res.points) || [];
  const unit = window.__activeNwUnit || 'TRY';
  const unitMeta = unit === 'USD'
    ? { label: 'Net Varlık ($)', prefix: '$', dec: 2, color: '#818cf8', fillTop: 'rgba(129, 140, 248, 0.25)' }
    : (unit === 'GOLD'
      ? { label: 'Net Varlık (gr Altın)', prefix: '', suffix: ' gr', dec: 3, color: '#fbbf24', fillTop: 'rgba(251, 191, 36, 0.24)' }
      : { label: 'Net Varlık (₺)', prefix: '₺', dec: 2, color: '#10b981', fillTop: 'rgba(16, 185, 129, 0.25)' });

  const getVal = (p) => unit === 'USD'
    ? Number(p.usd_eq || 0)
    : (unit === 'GOLD' ? Number(p.gold_gr || 0) : Number(p.total_try || 0));

  const values = pts.map(getVal);
  const badge = document.getElementById('nw-change-badge');
  if (badge) {
    if (pts.length >= 2 && values[0] > 0) {
      const chg = values[values.length - 1] - values[0];
      const pct = (chg / values[0]) * 100;
      const sign = chg >= 0 ? '+' : '';
      badge.innerText = `${sign}${unitMeta.prefix}${chg.toLocaleString('tr-TR', {minimumFractionDigits: unitMeta.dec, maximumFractionDigits: unitMeta.dec})}${unitMeta.suffix || ''} (${sign}%${pct.toFixed(2)})`;
      badge.className = `text-xs px-3 py-1 rounded-full border font-mono privacy-mask font-semibold ${chg >= 0 ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : 'bg-rose-500/10 text-rose-300 border-rose-500/30'}`;
    } else {
      badge.innerText = `${pts.length} gün kaydedildi`;
      badge.className = 'text-xs px-3 py-1 rounded-full bg-white/[0.05] text-slate-200 border border-white/[0.08] font-mono privacy-mask';
    }
  }

  const livePill = document.getElementById('nw-live-pill');
  if (livePill && res?.live_breakdown?.updated_at) {
    livePill.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> Canlı · ${escapeHtml(res.live_breakdown.updated_at)}`;
  }

  const canvas = document.getElementById('netWorthChart');
  if (!canvas || pts.length === 0) return;

  if (unit === 'RACE') {
    const r100 = res.race_100 || {};
    const rDates = r100.dates || [];
    const rLabels = rDates.map((d, idx) => (idx === rDates.length - 1 ? `${d.slice(5)} (Canlı)` : d.slice(5)));
    if (badge && r100.portfolio && r100.portfolio.length >= 2) {
      const pEnd = Number(r100.portfolio[r100.portfolio.length - 1] || 100);
      const gEnd = Number((r100.gold || [])[(r100.gold || []).length - 1] || 100);
      const zEnd = Number((r100.zbj || [])[(r100.zbj || []).length - 1] || 100);
      badge.innerText = `Portföy ${pEnd.toFixed(1)} · ZBJ ${zEnd.toFixed(1)} · Altın ${gEnd.toFixed(1)}`;
      badge.className = 'text-xs px-3 py-1 rounded-full bg-indigo-500/15 text-indigo-200 border border-indigo-400/30 font-mono font-semibold';
    }
    if (netWorthChartInstance) {
      netWorthChartInstance.destroy();
      netWorthChartInstance = null;
    }
    const ctxR = canvas.getContext('2d');
    netWorthChartInstance = new Chart(ctxR, {
      type: 'line',
      data: {
        labels: rLabels,
        datasets: [
          { label: 'Senin Portföyün', data: r100.portfolio || [], borderColor: '#10b981', borderWidth: 3, pointRadius: 2, tension: 0.28 },
          { label: 'ZBJ Risksiz Faiz', data: r100.zbj || [], borderColor: '#38bdf8', borderDash: [5, 4], borderWidth: 2, pointRadius: 0, tension: 0.28 },
          { label: 'Gram Altın', data: r100.gold || [], borderColor: '#fbbf24', borderWidth: 2, pointRadius: 0, tension: 0.28 },
          { label: 'Dolar (USD)', data: r100.usd || [], borderColor: '#a78bfa', borderDash: [2, 3], borderWidth: 1.8, pointRadius: 0, tension: 0.28 },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: true, labels: { color: '#e2e8f0', boxWidth: 14, font: { family: 'Plus Jakarta Sans, sans-serif', size: 12 } } },
          tooltip: {
            backgroundColor: 'rgba(8, 12, 24, 0.97)',
            titleColor: '#f8fafc',
            bodyColor: '#e2e8f0',
            borderColor: 'rgba(99, 102, 241, 0.45)',
            borderWidth: 1,
            padding: 12,
            cornerRadius: 12,
            callbacks: {
              label: c => ` ${c.dataset.label}: ₺${Number(c.parsed.y || 100).toFixed(2)} (${(Number(c.parsed.y || 100) - 100) >= 0 ? '+' : ''}${(Number(c.parsed.y || 100) - 100).toFixed(2)}%)`
            }
          }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 12 } } },
          y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#cbd5e1', font: { family: 'JetBrains Mono, monospace', size: 12 }, callback: v => `₺${Number(v).toFixed(0)}` } }
        }
      }
    });
    netWorthChartInstance.$isRaceMode = true;
    return;
  }

  if (netWorthChartInstance && netWorthChartInstance.$isRaceMode) {
    netWorthChartInstance.destroy();
    netWorthChartInstance = null;
  }

  // Tek gün kaydı varsa düz tek nokta yerine okunaklı başlangıç->canlı çizgisi oluştur
  const renderPts = pts.length === 1
    ? [{ ...pts[0], _isSyntheticStart: true }, pts[0]]
    : pts;
  const renderValues = renderPts.map(getVal);
  const labels = renderPts.map((p, idx) => (
    p._isSyntheticStart
      ? 'Başlangıç'
      : (idx === renderPts.length - 1 ? `${p.date.slice(5)} (Canlı)` : p.date.slice(5))
  ));
  const pointRadii = renderPts.map((p, idx) => (idx === renderPts.length - 1 ? 5.5 : (p._isSyntheticStart ? 3 : (renderPts.length <= 14 ? 3 : 1.5))));

  if (netWorthChartInstance) {
    netWorthChartInstance.$nwPoints = renderPts;
    netWorthChartInstance.$nwValues = renderValues;
    netWorthChartInstance.$nwBreakdown = res.live_breakdown || null;
    netWorthChartInstance.$nwMeta = unitMeta;
    netWorthChartInstance.data.labels = labels;
    netWorthChartInstance.data.datasets[0].label = unitMeta.label;
    netWorthChartInstance.data.datasets[0].data = renderValues;
    netWorthChartInstance.data.datasets[0].borderColor = unitMeta.color;
    netWorthChartInstance.data.datasets[0].pointBackgroundColor = unitMeta.color;
    netWorthChartInstance.data.datasets[0].pointRadius = pointRadii;
    netWorthChartInstance.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 200);
  grad.addColorStop(0, unitMeta.fillTop);
  grad.addColorStop(1, 'rgba(16, 185, 129, 0.0)');
  const nwCrosshair = typeof window.createVerticalCrosshairPlugin === 'function'
    ? window.createVerticalCrosshairPlugin('nwCrosshair', 'rgba(16, 185, 129, 0.45)')
    : { id: 'nwCrosshair' };

  netWorthChartInstance = new Chart(ctx, {
    type: 'line',
    plugins: [nwCrosshair],
    data: {
      labels: labels,
      datasets: [{
        label: unitMeta.label,
        data: renderValues,
        borderColor: unitMeta.color,
        backgroundColor: grad,
        fill: true,
        tension: 0.35,
        borderWidth: 2.5,
        pointRadius: pointRadii,
        pointHoverRadius: 7,
        pointBackgroundColor: unitMeta.color,
        pointBorderColor: '#07090f',
        pointBorderWidth: 1.5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(8, 12, 24, 0.97)',
          titleColor: '#f8fafc',
          bodyColor: '#e2e8f0',
          borderColor: 'rgba(16, 185, 129, 0.4)',
          borderWidth: 1,
          padding: 13,
          cornerRadius: 12,
          titleFont: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 13, weight: '700' },
          bodyFont: { family: 'JetBrains Mono, monospace', size: 12 },
          callbacks: {
            title: function(items) {
              if (!items || !items.length) return '';
              const idx = items[0].dataIndex;
              const p = (netWorthChartInstance.$nwPoints || [])[idx];
              if (!p) return items[0].label;
              const isLast = idx === (netWorthChartInstance.$nwPoints || []).length - 1;
              return isLast ? `${p.date} (Bugün · Canlı)` : `${p.date}`;
            },
            label: function(c) {
              const idx = c.dataIndex;
              const p = (netWorthChartInstance.$nwPoints || [])[idx] || {};
              const meta = netWorthChartInstance.$nwMeta || { prefix: '₺', dec: 2, suffix: '' };
              const val = Number(c.parsed.y || 0).toLocaleString('tr-TR', { minimumFractionDigits: meta.dec, maximumFractionDigits: meta.dec });
              return ` Değer: ${meta.prefix}${val}${meta.suffix || ''} (₺${Number(p.total_try || 0).toLocaleString('tr-TR', { minimumFractionDigits: 2 })})`;
            },
            afterBody: function(items) {
              if (!items || !items.length) return [];
              const idx = items[0].dataIndex;
              const ptsArr = netWorthChartInstance.$nwPoints || [];
              const valsArr = netWorthChartInstance.$nwValues || [];
              const meta = netWorthChartInstance.$nwMeta || { prefix: '₺', dec: 2, suffix: '' };
              const p = ptsArr[idx];
              if (!p) return [];
              const lines = [
                ` Eşdeğer: ~$${Number(p.usd_eq || 0).toLocaleString('tr-TR', {minimumFractionDigits: 2})} | ~${Number(p.gold_gr || 0).toFixed(2)} gr Altın`
              ];
              if (idx > 0 && valsArr[idx - 1] > 0) {
                const prevD = valsArr[idx] - valsArr[idx - 1];
                const prevPct = (prevD / valsArr[idx - 1]) * 100;
                const s = prevD >= 0 ? '+' : '';
                lines.push(` Önceki Güne Göre: ${s}${meta.prefix}${prevD.toLocaleString('tr-TR', {minimumFractionDigits: meta.dec, maximumFractionDigits: meta.dec})}${meta.suffix || ''} (${s}%${prevPct.toFixed(2)})`);
              }
              if (idx > 0 && valsArr[0] > 0) {
                const totD = valsArr[idx] - valsArr[0];
                const totPct = (totD / valsArr[0]) * 100;
                const s2 = totD >= 0 ? '+' : '';
                lines.push(` Dönem Başından: ${s2}${meta.prefix}${totD.toLocaleString('tr-TR', {minimumFractionDigits: meta.dec, maximumFractionDigits: meta.dec})}${meta.suffix || ''} (${s2}%${totPct.toFixed(2)})`);
              }
              const lb = netWorthChartInstance.$nwBreakdown;
              if (idx === ptsArr.length - 1 && lb) {
                lines.push(` Nakit: ₺${Number(lb.cash_try).toLocaleString('tr-TR')} • Fon: ₺${Number(lb.settled_funds_try).toLocaleString('tr-TR')} • Valör: ₺${Number(lb.pending_total_try).toLocaleString('tr-TR')}`);
              } else if (p.cash_try !== undefined && p.funds_try !== undefined) {
                lines.push(` Nakit: ₺${Number(p.cash_try).toLocaleString('tr-TR')} • Yatırım: ₺${Number(p.funds_try).toLocaleString('tr-TR')}`);
              }
              return lines;
            }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { family: 'Plus Jakarta Sans, Inter, sans-serif', size: 12 } } },
        y: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: {
            color: '#cbd5e1',
            font: { family: 'JetBrains Mono, monospace', size: 12 },
            callback: v => {
              const m = netWorthChartInstance?.$nwMeta || unitMeta;
              return `${m.prefix}${Number(v).toLocaleString('tr-TR', { maximumFractionDigits: m.dec === 3 ? 2 : 0 })}${m.suffix || ''}`;
            }
          }
        }
      }
    }
  });
  netWorthChartInstance.$nwPoints = renderPts;
  netWorthChartInstance.$nwValues = renderValues;
  netWorthChartInstance.$nwBreakdown = res.live_breakdown || null;
  netWorthChartInstance.$nwMeta = unitMeta;
}

async function fetchNetWorthHistory(days = null, btn = null) {
  const targetDays = Number.isFinite(days) && days !== null ? days : (Number.isFinite(window.__activeNwDays) ? window.__activeNwDays : 30);
  window.__activeNwDays = targetDays;
  try { localStorage.setItem('fin_nw_days', String(targetDays)); } catch (e) {}
  syncNetWorthControlsUI();

  // İlk yüklemede ağ cevabı gelene kadar önbellekteki grafiği koru
  if (!window.__lastNwData) {
    try {
      const cached = localStorage.getItem(`fin_nw_cache_${targetDays}`);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed && Array.isArray(parsed.points) && parsed.points.length > 0) {
          window.__lastNwData = parsed;
          renderNetWorthChartFromData(parsed);
        }
      }
    } catch (e) {}
  }

  try {
    const resp = await fetch(`/api/portfolio/history?days=${targetDays}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const res = await resp.json();
    const incomingPts = (res && Array.isArray(res.points)) ? res.points : [];
    const prevPts = (window.__lastNwData && Array.isArray(window.__lastNwData.points)) ? window.__lastNwData.points : [];
    if (incomingPts.length === 0 && prevPts.length > 0) return;
    window.__lastNwData = res;
    try { localStorage.setItem(`fin_nw_cache_${targetDays}`, JSON.stringify(res)); } catch (e) {}
    renderNetWorthChartFromData(res);
  } catch (e) { console.error('Net worth history error:', e); }
}


const ALERT_CODE_LABELS = {
  TCA: 'TCA · Altın Fonu',
  AFT: 'AFT · Teknoloji Fonu',
  ZBJ: 'ZBJ · Başak (Faiz)',
  GRAM_ALTIN: 'Gram Altın (₺)',
  USD: 'Dolar / TL (₺)'
};

const ALERT_COND_LABELS = {
  BELOW: 'Altına düşerse',
  ABOVE: 'Üstüne çıkarsa',
  DROP_PCT: 'Günlük düşüş %',
  DRAWDOWN_PCT: 'Zirveden düşüş %'
};

function formatAlertRuleText(alert) {
  const val = Number(alert && alert.threshold);
  const formatted = Number.isFinite(val) ? val.toLocaleString('tr-TR', { maximumFractionDigits: 4 }) : '0';
  if (alert && alert.condition === 'DROP_PCT') {
    return `Günlük %${formatted}+ düşerse`;
  }
  if (alert && alert.condition === 'DRAWDOWN_PCT') {
    return `30G Zirveden -%${formatted}+ düşerse`;
  }
  const condLabel = ALERT_COND_LABELS[alert && alert.condition] || 'Hedef';
  return `₺${formatted} ${condLabel.toLowerCase()}`;
}

function renderPriceAlerts(alerts) {
  const box = document.getElementById('price-alerts-list');
  const alertDockBadge = document.getElementById('dock-badge-alerts');
  if (alertDockBadge) {
    const cnt = Array.isArray(alerts) ? alerts.length : 0;
    alertDockBadge.classList.toggle('hidden', cnt === 0);
    alertDockBadge.innerText = String(cnt);
  }
  if (!box) return;
  if (!Array.isArray(alerts) || alerts.length === 0) {
    box.innerHTML = '<div class="text-slate-400 text-xs py-2">Henüz kurulu alarm yok. Yukarıdan tek tıkla ekleyebilirsin.</div>';
    return;
  }
  box.innerHTML = alerts.map(a => {
    const fundName = globalMarket?.funds?.[a.code]?.name;
    const codeLabel = ALERT_CODE_LABELS[a.code] || (fundName ? `${a.code} · ${fundName}` : (a.code || 'Varlık'));
    const condTone = a.condition === 'ABOVE'
      ? 'bg-emerald-500/12 text-emerald-300 border-emerald-500/25'
      : (a.condition === 'DRAWDOWN_PCT'
          ? 'bg-amber-500/12 text-amber-300 border-amber-500/25'
          : 'bg-rose-500/12 text-rose-300 border-rose-500/25');
    const noteText = a.note ? `<div class="text-xs text-slate-300 truncate mt-1">${escapeHtml(a.note)}</div>` : '';
    return `
      <div class="p-3.5 rounded-2xl bg-white/[0.03] border border-white/[0.08] flex items-center justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-semibold text-white text-sm">${escapeHtml(codeLabel)}</span>
            <span class="px-2.5 py-0.5 rounded-lg border text-xs font-mono ${condTone}">${escapeHtml(formatAlertRuleText(a))}</span>
          </div>
          ${noteText}
        </div>
        <button type="button" data-alert-id="${escapeHtml(String(a.id))}" onclick="deletePriceAlert(this.dataset.alertId)" class="w-9 h-9 rounded-xl bg-white/[0.04] hover:bg-rose-500/15 text-slate-400 hover:text-rose-300 border border-white/[0.08] flex items-center justify-center flex-shrink-0 transition" title="Alarmı Sil">
          <i class="fa-solid fa-trash-can text-xs"></i>
        </button>
      </div>
    `;
  }).join('');
}

async function fetchPriceAlerts() {
  const box = document.getElementById('price-alerts-list');
  if (!box) return;
  try {
    const res = await fetch('/api/alerts');
    if (!res.ok) return;
    const data = await res.json();
    renderPriceAlerts((data && data.alerts) || []);
  } catch (e) {
    console.error('Price alerts error:', e);
  }
}

async function submitPriceAlert(code, condition, threshold, note) {
  const res = await fetch('/api/alerts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, condition, threshold, note: note || '' })
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error((data && data.detail) || 'Alarm eklenemedi.');
  }
  await fetchPriceAlerts();
  showToast('Fiyat alarmı kuruldu! Şart gerçekleşince Telegram\'dan haber vereceğim.');
}

async function quickCreatePriceAlert(code, condition, threshold, note) {
  try {
    await submitPriceAlert(code, condition, Number(threshold), note || '');
  } catch (e) {
    showToast(e.message || 'Alarm eklenirken hata oluştu.', true);
  }
}

async function createCustomPriceAlert(e) {
  e.preventDefault();
  const codeEl = document.getElementById('alert-code');
  const condEl = document.getElementById('alert-cond');
  const thrEl = document.getElementById('alert-threshold');
  const code = codeEl ? codeEl.value : 'TCA';
  const condition = condEl ? condEl.value : 'BELOW';
  const threshold = parseFloat(thrEl ? thrEl.value : '');
  if (!Number.isFinite(threshold) || threshold <= 0) {
    showToast('Lütfen 0\'dan büyük geçerli bir hedef değer gir.', true);
    return;
  }
  try {
    await submitPriceAlert(code, condition, threshold, '');
    if (thrEl) thrEl.value = '';
  } catch (e) {
    showToast(e.message || 'Alarm eklenirken hata oluştu.', true);
  }
}

async function deletePriceAlert(alertId) {
  if (!alertId) return;
  try {
    const res = await fetch(`/api/alerts/${encodeURIComponent(alertId)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Alarm silinemedi.');
    await fetchPriceAlerts();
    showToast('Fiyat alarmı kaldırıldı.');
  } catch (e) {
    showToast(e.message || 'Alarm silinemedi.', true);
  }
}

// AKILLI YARDIMCI ARAÇLAR (1: TEFAS 13:30 Valör Sayacı, 3: Aylık Bütçe Karnesi, 5: KYK Kaldıraç)
let __valorCountdownTimer = null;
function startValorCountdown(secondsLeft, cutoffState) {
  if (__valorCountdownTimer) {
    clearInterval(__valorCountdownTimer);
    __valorCountdownTimer = null;
  }
  const lblEl = document.getElementById('valor-countdown-label');
  const clkEl = document.getElementById('valor-countdown-clock');
  if (!clkEl) return;
  if (lblEl) {
    lblEl.innerText = cutoffState === 'OPEN'
      ? '13:30 Kesime Kalan'
      : (cutoffState === 'AFTER_CUTOFF' ? 'Kesim Geçti · Yarın Seansa' : 'Hafta Sonu · İlk Seansa');
  }
  let rem = Math.max(0, Number(secondsLeft || 0));
  const drawClock = () => {
    const d = Math.floor(rem / 86400);
    const h = Math.floor((rem % 86400) / 3600);
    const m = Math.floor((rem % 3600) / 60);
    const s = rem % 60;
    const pad = (n) => String(n).padStart(2, '0');
    clkEl.innerText = d > 0 ? `${d}g ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
    clkEl.className = cutoffState === 'OPEN'
      ? 'text-sm font-bold text-emerald-300 font-mono'
      : 'text-sm font-bold text-amber-300 font-mono';
  };
  drawClock();
  __valorCountdownTimer = setInterval(() => {
    if (rem > 0) rem -= 1;
    drawClock();
  }, 1000);
}


function quickAlarmFromRadar(code) {
  const upper = String(code || '').toUpperCase();
  switchTab('tools');
  setTimeout(() => {
    const cEl = document.getElementById('alert-code');
    const condEl = document.getElementById('alert-cond');
    const thrEl = document.getElementById('alert-threshold');
    if (cEl) {
      if (!Array.from(cEl.options).some(o => o.value === upper)) {
        const opt = document.createElement('option');
        opt.value = upper;
        opt.text = `${upper} · TEFAS Fonu`;
        cEl.appendChild(opt);
      }
      cEl.value = upper;
    }
    if (condEl) condEl.value = 'DRAWDOWN_PCT';
    if (thrEl) thrEl.value = '3';
    showToast(`${upper} için -%3 alarm şablonu Araçlar sekmesinde hazırlandı!`, 'info');
  }, 180);
}

function renderFundSideBySideCompare() {
  const box = document.getElementById('radar-compare-result');
  const selA = document.getElementById('cmp-fund-a');
  const selB = document.getElementById('cmp-fund-b');
  if (!box || !selA || !selB) return;
  const fMap = globalMarket?.funds || {};
  const allCodes = getCatalogFundCodes(fMap, globalPortfolio?.funds || []);
  [selA, selB].forEach((sel, idx) => {
    const cur = sel.value;
    const known = Array.from(sel.options).map(o => o.value);
    allCodes.forEach(c => {
      if (!known.includes(c)) {
        const o = document.createElement('option');
        o.value = c;
        o.text = c;
        sel.appendChild(o);
      }
    });
    if (cur) sel.value = cur;
    else if (idx === 1 && allCodes.length > 1) sel.value = allCodes[1];
  });
  const codeA = selA.value;
  const codeB = selB.value;
  const a = fMap[codeA] || {};
  const b = fMap[codeB] || {};
  const fmtDelta = v => {
    const n = Number(v || 0);
    return `<span class="${n >= 0 ? 'text-emerald-300' : 'text-rose-300'} font-bold">${n >= 0 ? '+' : ''}%${n.toFixed(2)}</span>`;
  };
  const diff30 = Number(a.monthly_yield_pct || 0) - Number(b.monthly_yield_pct || 0);
  const leader = diff30 >= 0 ? codeA : codeB;
  box.innerHTML = `
    <div class="flex flex-wrap items-center gap-3">
      <span class="px-2.5 py-1 rounded-lg bg-indigo-500/15 border border-indigo-400/30 text-indigo-200 font-bold">${escapeHtml(codeA)}: ₺${Number(a.price || 0).toFixed(4)}</span>
      <span>1G: ${fmtDelta(a.daily_yield_pct)}</span>
      <span>30G: ${fmtDelta(a.monthly_yield_pct)}</span>
    </div>
    <span class="text-slate-400 font-sans">30G Lideri: <strong class="text-emerald-300 font-mono">${escapeHtml(leader)}</strong> (${diff30 >= 0 ? '+' : ''}%${Math.abs(diff30).toFixed(2)} fark)</span>
    <div class="flex flex-wrap items-center gap-3">
      <span class="px-2.5 py-1 rounded-lg bg-amber-500/15 border border-amber-400/30 text-amber-200 font-bold">${escapeHtml(codeB)}: ₺${Number(b.price || 0).toFixed(4)}</span>
      <span>1G: ${fmtDelta(b.daily_yield_pct)}</span>
      <span>30G: ${fmtDelta(b.monthly_yield_pct)}</span>
    </div>
  `;
}


function prefillMinus3Alert() {
  const cEl = document.getElementById('alert-code');
  const condEl = document.getElementById('alert-cond');
  const thrEl = document.getElementById('alert-threshold');
  if (!cEl || !condEl || !thrEl) return;
  const code = cEl.value;
  let curPrice = 0;
  if (code === 'GRAM_ALTIN') curPrice = Number(globalMarket?.macro?.gram_altin_try || 0);
  else if (code === 'USD') curPrice = Number(globalMarket?.macro?.usd_try || 0);
  else curPrice = Number(globalMarket?.funds?.[code]?.price || 0);
  if (curPrice <= 0) {
    showToast('Canlı fiyat henüz yüklenmedi.', 'warn');
    return;
  }
  condEl.value = 'BELOW';
  const dec = curPrice < 10 ? 4 : 2;
  thrEl.value = (curPrice * 0.97).toFixed(dec);
  showToast(`${code} için güncel fiyatın %3 altı (₺${thrEl.value}) hazırlandı!`, 'info');
}

