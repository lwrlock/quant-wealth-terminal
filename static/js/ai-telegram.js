async function shutdownDashboard() {
  const ok = await showAppConfirm({
    title: 'Finans Terminalini Kapat',
    message: 'Sunucu durdurulacak ve arka plandaki tüm veri/bot süreçleri güvenli biçimde sonlandırılacaktır. Devam edilsin mi?',
    confirmText: 'Terminali Kapat',
    tone: 'danger'
  });
  if (!ok) return;
  try {
    await fetch('/api/shutdown', { method: 'POST' });
    document.body.innerHTML = `
      <div style="height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; background:#030712; color:#f1f5f9; font-family:'Plus Jakarta Sans',sans-serif; text-align:center; padding:20px;">
        <div style="width:72px; height:72px; border-radius:20px; background:rgba(244,63,94,0.1); border:1px solid rgba(244,63,94,0.3); display:flex; align-items:center; justify-content:center; margin-bottom:1.5rem; font-size:2rem; color:#f43f5e;">
          <i class="fa-solid fa-power-off"></i>
        </div>
        <h2 style="font-size:1.6rem; font-weight:800; margin-bottom:0.5rem; color:#f8fafc; letter-spacing:-0.02em;">Terminal Güvenle Kapatıldı</h2>
        <p style="color:#94a3b8; font-size:0.95rem; max-width:460px; line-height:1.6; margin-bottom:1.8rem;">Arka plandaki tüm Python ve sunucu süreçleri sonlandırıldı. Pil ve RAM %100 serbest bırakıldı. İstediğin zaman masaüstündeki <strong>Finans Terminali</strong> simgesine tıklayarak tekrar açabilirsin.</p>
        <button onclick="window.close()" style="background:linear-gradient(135deg, #6366f1, #4f46e5); color:white; border:none; padding:12px 30px; border-radius:12px; cursor:pointer; font-weight:700; font-size:0.9rem; box-shadow:0 0 20px rgba(99,102,241,0.4);">Sekmeyi Kapat</button>
      </div>
    `;
  } catch (e) {
    showToast("Kapatma sinyali gönderildi.", "info");
  }
}


function renderCouncilData(res) {
  if (!res || !res.council) return;
  const c = res.council;

  // 1. Üst Özet Kartları
  if (document.getElementById('council-qwen') && c.qwen3) {
    document.getElementById('council-qwen').innerText = c.qwen3.verdict || c.qwen3.analysis;
  }
  if (document.getElementById('council-deepseek') && c.deepseek) {
    document.getElementById('council-deepseek').innerText = c.deepseek.verdict || c.deepseek.analysis;
  }
  if (document.getElementById('council-macro') && c.claude_macro) {
    document.getElementById('council-macro').innerText = c.claude_macro.verdict || c.claude_macro.analysis;
  }
  if (document.getElementById('council-verdict') && c.consensus) {
    document.getElementById('council-verdict').innerHTML = `
      <p><strong>Konsensüs:</strong> ${escapeHtml(c.consensus.summary).replace(/\n/g, '<br>')}</p>
    `;
  }

  // 2. Canlı Arena - Aktif Konu Başlığı
  if (document.getElementById('arena-active-topic') && res.topic) {
    document.getElementById('arena-active-topic').innerText = res.topic;
  }

  // 3. Canlı Arena - 4 Model İsim/Rozet + Düşünce Süreci (CoT) & Kararlar
  const arenaMap = [
    ['qwen3', 'qwen'],
    ['deepseek', 'deepseek'],
    ['claude_macro', 'macro'],
    ['furkan_ai', 'furkan'],
  ];
  arenaMap.forEach(([apiKey, uiKey]) => {
    const ag = c[apiKey];
    if (!ag) return;
    const codeEl = document.getElementById(`arena-${uiKey}-code`);
    const titleEl = document.getElementById(`arena-${uiKey}-title`);
    const badgeEl = document.getElementById(`arena-${uiKey}-badge`);
    const timeEl = document.getElementById(`arena-${uiKey}-time`);
    const thinkEl = document.getElementById(`arena-${uiKey}-think`);
    const verdEl = document.getElementById(`arena-${uiKey}-verdict`);
    if (codeEl && ag.short_code) codeEl.innerText = ag.short_code;
    if (titleEl && ag.name) titleEl.innerText = ag.name;
    if (badgeEl && ag.badge) badgeEl.innerText = ag.badge;
    if (timeEl) timeEl.innerText = ag.execution_time || '1.0s';
    if (thinkEl) thinkEl.innerText = ag.think || '';
    if (verdEl) verdEl.innerText = ag.verdict || ag.analysis || '';
  });

  // 4. Canlı Arena - Büyük Konsensüs Adımları ve Mala Anlatır Gibi Özet
  if (c.consensus) {
    if (document.getElementById('arena-consensus-summary')) {
      document.getElementById('arena-consensus-summary').innerText = c.consensus.summary;
    }
    const stepsContainer = document.getElementById('arena-consensus-steps');
    if (stepsContainer && Array.isArray(c.consensus.steps)) {
      stepsContainer.innerHTML = c.consensus.steps.map((st, idx) => `
        <div class="p-3.5 rounded-xl bg-[#080e24] border border-white/[0.08] text-[13px] text-slate-200 flex items-start gap-3 shadow-inner">
          <span class="w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">${idx+1}</span>
          <span class="leading-relaxed">${escapeHtml(st)}</span>
        </div>
      `).join('');
    }
  }

  if (res.news) {
    renderNewsRadar(res.news);
  }
}

// 4 LLM Canlı Konsensüs ve Ortak Kararı Çek
async function fetchLiveCouncilVerdict() {
  try {
    const res = await fetch('/api/council/live-verdict').then(r => r.json());
    renderCouncilData(res);
  } catch (err) {
    console.error("Council fetch error:", err);
  }
}

// Canlı Müzakereyi Tetikle (4 LLM CoT & Konsensüs)
async function triggerCouncilDebate(customTopic) {
  const inputEl = document.getElementById('council-custom-topic');
  const topic = (customTopic || (inputEl ? inputEl.value.trim() : '')) || 'Canlı Portföy Durumu: ZBJ Nema, TCA Altın ve Büyüme Stratejisi';
  if (inputEl) inputEl.value = topic;

  const btnText = document.getElementById('council-btn-text');
  const btnIcon = document.getElementById('council-btn-icon');
  if (btnText) btnText.innerText = '4 LLM Müzakere Ediyor...';
  if (btnIcon) btnIcon.className = 'fa-solid fa-circle-notch fa-spin text-xs';

  if (document.getElementById('arena-active-topic')) {
    document.getElementById('arena-active-topic').innerText = topic;
  }

  // Arena'ya yumuşak kaydır (Kullanıcı anında sonucu görür)
  const arena = document.getElementById('council-debate-arena');
  if (arena) {
    arena.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Düşünce Süreci Loading Göstergeleri
  ['qwen', 'deepseek', 'macro', 'furkan'].forEach(m => {
    const th = document.getElementById(`arena-${m}-think`);
    if (th) th.innerText = 'Modeller veriyi tarıyor, matematiksel ve risk denklemlerini kuruyor...\n[Chain-of-Thought Düşünce Süreci Devrede]';
    const vd = document.getElementById(`arena-${m}-verdict`);
    if (vd) vd.innerText = 'Düşünce süreci tamamlanınca nihai karar buraya dökülecek...';
  });

  try {
    const res = await fetch('/api/council/debate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ topic })
    }).then(r => r.json());

    renderCouncilData(res);
    const warnEl = document.getElementById('council-llm-warning');
    if (warnEl) warnEl.classList.toggle('hidden', Boolean(res.live_llm_used || res.gemini_configured));
    if (res.live_llm_used) {
      showToast("4 LLM gerçek zamanlı derin müzakereyi tamamladı.");
    } else if (res.llm_reason === 'missing_key') {
      showToast("Bu sunucuda Gemini API Key yok — sabit kural şablonu gösterildi. Telegram Bot sekmesinden key ekleyebilirsiniz.", "warn");
    } else {
      showToast("Gemini API yanıt vermediği için kural şablonu gösterildi. Biraz sonra tekrar deneyin.", "warn");
    }
  } catch (e) {
    console.error("Council debate error:", e);
    showToast("Konsey müzakere sunucusuna erişilemedi.");
  } finally {
    if (btnText) btnText.innerText = 'Konseyi Topla (4 LLM Müzakeresi)';
    if (btnIcon) btnIcon.className = 'fa-solid fa-gavel text-xs';
  }
}

function setQuickTopic(topicText) {
  const inputEl = document.getElementById('council-custom-topic');
  if (inputEl) inputEl.value = topicText;
  triggerCouncilDebate(topicText);
}

async function sendDebateToTelegram() {
  const topic = document.getElementById('arena-active-topic') ? document.getElementById('arena-active-topic').innerText : 'Konsey Kararı';
  const verdict = document.getElementById('arena-consensus-summary') ? document.getElementById('arena-consensus-summary').innerText : '';
  if (!verdict) {
    showToast("Henüz bir konsey kararı üretilmedi.");
    return;
  }
  showToast("Telegram'a iletiliyor...");
  try {
    const res = await fetch('/api/council/send-to-telegram', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ topic, verdict })
    }).then(r => r.json());
    if (res.status === 'ok') {
      showToast("Karar Telegram botuna (@YardimcikoleBot) iletildi.");
    } else {
      showToast("İletilemedi: " + (res.message || "Hata"), "error");
    }
  } catch (e) {
    showToast("Telegram bağlantı hatası.", "error");
  }
}

function copyConsensusToClipboard() {
  const summary = document.getElementById('arena-consensus-summary') ? document.getElementById('arena-consensus-summary').innerText : '';
  if (!summary) return;
  navigator.clipboard.writeText(summary).then(() => {
    showToast("Konsensüs eylem planı panoya kopyalandı.");
  });
}


async function fetchLiveNews(force = false) {
  const icon = document.getElementById('news-refresh-icon');
  if (icon) icon.classList.add('fa-spin');
  try {
    const res = await fetch('/api/news').then(r => r.json());
    if (res && res.news) {
      renderNewsRadar(res.news);
    }
  } catch (err) {
    console.error("News fetch error:", err);
  } finally {
    if (icon) icon.classList.remove('fa-spin');
  }
}

function renderNewsRadar(news) {
  const container = document.getElementById('live-news-grid');
  if (!container) return;
  if (!news || news.length === 0) {
    container.innerHTML = '<div class="p-3.5 rounded-xl bg-[#070c1e] border border-white/[0.08] text-slate-400 text-xs col-span-3">Piyasa haber akışı taranıyor...</div>';
    return;
  }

  container.innerHTML = news.slice(0, 6).map((n, i) => {
    const srcName = String(n.source || 'Ekonomi');
    const srcColor = srcName.includes('Bloomberg') ? 'text-amber-400 border-amber-500/30 bg-amber-500/10' :
                     (srcName.includes('Investing') ? 'text-sky-400 border-sky-500/30 bg-sky-500/10' : 'text-rose-400 border-rose-500/30 bg-rose-500/10');
    const safeUrl = safeHttpUrl(n.link);
    return `
      <div class="p-4 rounded-xl bg-[#070c1e] border border-white/[0.08] hover:border-purple-500/30 transition flex flex-col justify-between space-y-2.5 group shadow-inner">
        <div class="space-y-2">
          <div class="flex items-center justify-between text-xs">
            <span class="px-2.5 py-0.5 rounded-md ${srcColor} font-bold">${escapeHtml(srcName)}</span>
            <span class="text-slate-400 font-mono">${escapeHtml(n.time || 'Canlı')}</span>
          </div>
          <h4 class="text-sm font-bold text-white group-hover:text-purple-300 transition-colors line-clamp-2 leading-snug">
            ${escapeHtml(n.title)}
          </h4>
          <p class="text-xs text-slate-300 line-clamp-2 leading-relaxed">
            ${escapeHtml(n.description || '')}
          </p>
        </div>
        <div class="pt-2.5 border-t border-white/[0.06] flex items-center justify-between gap-2 text-xs">
          <button type="button" data-news-title="${escapeHtml(n.title)}" onclick="askCouncilAboutNews(this.dataset.newsTitle)" class="px-2.5 py-1 rounded-lg bg-purple-500/15 hover:bg-purple-500/28 border border-purple-400/30 text-purple-200 font-semibold transition">
            <i class="fa-solid fa-gavel text-xs mr-1"></i>Konseye Sor
          </button>
          ${safeUrl ? `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="text-slate-300 hover:text-white flex items-center gap-1.5 transition font-medium">Habere Git <i class="fa-solid fa-arrow-up-right-from-square text-xs"></i></a>` : ''}
        </div>
      </div>
    `;
  }).join('');
}

// ======================================================================
// TELEGRAM BOT VE NLP MODÜLÜ
// ======================================================================
async function fetchTelegramStatus() {
  try {
    const res = await fetch('/api/telegram/status').then(r => r.json());
    const dot = document.getElementById('tg-status-dot');
    const text = document.getElementById('tg-status-text');
    const badge = document.getElementById('tg-status-badge');
    const navDot = document.getElementById('tg-nav-dot');
    const tokenInput = document.getElementById('tg-token-input');
    const authInfo = document.getElementById('tg-auth-chats-info');

    if (res.token_configured) {
      const botUser = res.bot_info?.username ? `@${res.bot_info.username}` : 'Bot Aktif';
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
      if (text) text.innerText = `Bağlandı (${botUser})`;
      if (badge) badge.className = 'px-3 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-semibold flex items-center gap-2';
      if (navDot) navDot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
      
      if (tokenInput && !tokenInput.value) {
        tokenInput.placeholder = '•••••••••••••••••••••••• (Kayıtlı & Aktif)';
      }
    } else {
      if (dot) dot.className = 'w-2 h-2 rounded-full bg-amber-400';
      if (text) text.innerText = 'Token Bekleniyor';
      if (badge) badge.className = 'px-3 py-1 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 text-xs font-semibold flex items-center gap-2';
      if (navDot) navDot.className = 'w-2 h-2 rounded-full bg-slate-500';
    }

    if (authInfo) {
      const chats = res.authorized_chats || [];
      if (chats.length > 0) {
        authInfo.innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check mr-1"></i> Yetkili Sohbet ID: ${chats.join(', ')}</span> (Telefona bildirim hazır)`;
      } else {
        authInfo.innerHTML = `<span class="text-amber-400 font-semibold"><i class="fa-solid fa-clock mr-1"></i> Yetkili Sohbet: Bekleniyor</span> (Bota Telegram'dan /start yazınca otomatik eklenecek)`;
      }
    }
    await loadAiStatus();
  } catch (err) {
    console.error("Telegram status error:", err);
  }
}

async function loadAiStatus() {
  try {
    const res = await fetch('/api/ai/status').then(r => r.json());
    if (res && res.info) {
      const match = res.info.match(/Aktif Motor:\* (.*?)\n/);
      if (match && document.getElementById('ai-active-badge')) {
        document.getElementById('ai-active-badge').innerText = match[1];
      }
    }
  } catch (e) {}
}

async function saveAiKey() {
  const input = document.getElementById('ai-key-input');
  if (!input) return;
  const key = input.value.trim();
  try {
    const res = await fetch('/api/ai/save-key', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key})
    }).then(r => r.json());
    showToast(res.message || "Yapay Zeka anahtarı kaydedildi!", "success");
    input.value = "";
    await loadAiStatus();
  } catch (e) {
    showToast("Anahtar kaydedilirken hata oluştu: " + e, "error");
  }
}

async function saveTelegramConfig() {
  const token = document.getElementById('tg-token-input').value.trim();
  if (!token) {
    showToast("Lütfen @BotFather'dan aldığınız API tokenini girin.", "warn");
    return;
  }
  try {
    const res = await fetch('/api/telegram/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({bot_token: token, enabled: true})
    }).then(r => r.json());

    showToast("Telegram botu başarıyla yapılandırıldı ve arka plan dinleyicisi başlatıldı.");
    fetchTelegramStatus();
  } catch (err) {
    showToast("Ayar kaydedilirken hata oluştu: " + err, "error");
  }
}

function toggleTokenVisibility() {
  const inp = document.getElementById('tg-token-input');
  const icon = document.getElementById('tg-eye-icon');
  if (inp.type === 'password') {
    inp.type = 'text';
    icon.className = 'fa-solid fa-eye-slash text-xs';
  } else {
    inp.type = 'password';
    icon.className = 'fa-solid fa-eye text-xs';
  }
}

async function sendTelegramTest() {
  try {
    const res = await fetch('/api/telegram/test', { method: 'POST' }).then(r => r.json());
    if (res.message_sent) {
      showToast("Test bildirimi telefonunuza başarıyla gönderildi.");
    } else {
      showToast("Bildirim gönderilemedi. Bot tokenini girdiğinizden ve bota Telegram'dan /start yazdığınızdan emin olun.", "warn");
    }
  } catch (err) {
    showToast("Test gönderim hatası: " + err, "error");
  }
}

function setSimInput(text) {
  document.getElementById('tg-sim-input').value = text;
  runSimulateTelegram();
}

window.__tgCmdHistory = window.__tgCmdHistory || [];
async function runSimulateTelegram() {
  const text = document.getElementById('tg-sim-input').value.trim();
  if (!text) return;
  const out = document.getElementById('tg-sim-output');
  out.innerText = "Komut analiz ediliyor...";

  if (!window.__tgCmdHistory.includes(text)) {
    window.__tgCmdHistory = [text, ...window.__tgCmdHistory].slice(0, 5);
    const hBox = document.getElementById('tg-history-chips');
    if (hBox) {
      hBox.classList.remove('hidden');
      hBox.classList.add('flex');
      hBox.innerHTML = `<span class="text-slate-400">Son:</span>` + window.__tgCmdHistory.map(c =>
        `<button type="button" data-cmd="${escapeHtml(c)}" onclick="setSimInput(this.dataset.cmd)" class="px-2.5 py-0.5 rounded-lg bg-white/[0.06] hover:bg-indigo-500/20 border border-white/[0.1] text-slate-200 font-mono transition">${escapeHtml(c)}</button>`
      ).join('');
    }
  }

  try {
    const res = await fetch('/api/telegram/simulate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    }).then(r => r.json());

    out.innerText = res.reply || "Cevap üretilemedi.";
    // Eğer fon veya işlem yapıldıysa tabloyu güncelle
    if (text.includes('aldım') || text.includes('harcadım') || text.includes('virman') || text.includes('yattı')) {
      refreshAllData();
    }
  } catch (err) {
    out.innerText = "Simülasyon hatası: " + err;
  }
}

async function triggerTelegramAlert(type) {
  try {
    showToast("Bildirim Telegram botuna iletiliyor...");
    const res = await fetch('/api/telegram/trigger-alert', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ type })
    }).then(r => r.json());
    if (res.status === 'ok') {
      showToast(res.message);
    } else {
      showToast(res.message || "Bildirim iletilemedi.", "warn");
    }
  } catch (err) {
    showToast("Bildirim gönderim hatası: " + err, "error");
  }
}


window.__chatHistory = window.__chatHistory || [];

function syncChatModelPref(val) {
  const s1 = document.getElementById('chat-model-select');
  const s2 = document.getElementById('model-select');
  if (s1 && s1.value !== val) s1.value = val;
  if (s2 && s2.value !== val) s2.value = val;
}

function formatAiTextHtml(raw) {
  let safe = escapeHtml(raw || '');
  safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong class="text-white font-semibold">$1</strong>');
  safe = safe.replace(/(^|\s)\*([^*\n]+?)\*/g, '$1<em class="text-indigo-200">$2</em>');
  safe = safe.replace(/`([^`\n]+?)`/g, '<code class="px-1.5 py-0.5 rounded bg-white/10 text-sky-300 font-mono text-xs">$1</code>');
  safe = safe.replace(/\n/g, '<br>');
  return safe;
}

async function handleChat(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  const chatSel = document.getElementById('chat-model-select');
  const sideSel = document.getElementById('model-select');
  const model = (chatSel ? chatSel.value : (sideSel ? sideSel.value : 'auto')) || 'auto';

  appendMessage('SEN', text, false);
  input.value = '';

  const loadId = appendMessage('YAPAY ZEKÂ', 'Derin analiz ve düşünme başlatıldı... (0s)', true);
  const tStart = Date.now();
  const timerId = setInterval(() => {
    const el = document.getElementById(loadId);
    if (!el) return;
    const sec = Math.floor((Date.now() - tStart) / 1000);
    const bodyEl = el.querySelector('.ai-msg-body');
    if (bodyEl) bodyEl.innerText = `Piyasa, valör ve portföy verileri düşünülüyor... (${sec}s)`;
  }, 1000);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message: text,
        model: model,
        history: window.__chatHistory.slice(-6)
      })
    }).then(r => r.json());

    clearInterval(timerId);
    const el = document.getElementById(loadId);
    if (el) el.remove();

    const replyTxt = res.reply || 'Yanıt alınamadı.';
    const modelLabel = `${res.model_used || model}${res.elapsed_s ? ' · ' + res.elapsed_s + 's' : ''}`;
    appendMessage(`DANIŞMAN [${modelLabel}]`, replyTxt, true, res.thinking || '');

    window.__chatHistory.push({ role: 'user', content: text });
    window.__chatHistory.push({ role: 'assistant', content: replyTxt });
    if (window.__chatHistory.length > 12) {
      window.__chatHistory = window.__chatHistory.slice(-12);
    }
  } catch (err) {
    clearInterval(timerId);
    const el = document.getElementById(loadId);
    if (el) el.remove();
    appendMessage('DANIŞMAN', 'Bağlantı zaman aşımına uğradı veya sunucuya ulaşılamadı.', true);
  }
}

function quickPrompt(p) {
  document.getElementById('chat-input').value = p;
  document.getElementById('chat-form').dispatchEvent(new Event('submit'));
}

function appendMessage(sender, text, isAi, thinkingText = '') {
  const box = document.getElementById('chat-box');
  const id = 'msg-' + Math.random().toString(36).substr(2, 9);
  const div = document.createElement('div');
  div.id = id;
  div.className = `p-3.5 rounded-xl border leading-relaxed shadow-sm text-sm ${isAi ? 'bg-[#070c1e] border-white/[0.08] text-slate-200' : 'bg-indigo-600/20 border-indigo-500/40 text-indigo-100 ml-4'}`;
  let thinkBlock = '';
  if (isAi && thinkingText && thinkingText.trim().length > 0) {
    thinkBlock = `
      <details class="mb-2.5 rounded-lg bg-[#040814] border border-indigo-500/20 px-3 py-2 text-xs text-slate-300">
        <summary class="cursor-pointer font-semibold text-indigo-300 select-none">Düşünme Sürecini Göster</summary>
        <div class="mt-2 whitespace-pre-wrap font-mono leading-relaxed text-slate-300">${escapeHtml(thinkingText)}</div>
      </details>
    `;
  }
  div.innerHTML = `<span class="block mb-1.5 font-bold ${isAi ? 'text-indigo-400' : 'text-indigo-300'} text-xs tracking-wide">${escapeHtml(sender)}:</span>${thinkBlock}<div class="ai-msg-body">${isAi ? formatAiTextHtml(text) : escapeHtml(text).replace(/\n/g, '<br>')}</div>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return id;
}


async function fetchAiConfigStatus() {
  try {
    const cfg = await fetch('/api/ai/config').then(r => r.json());
    const badge = document.getElementById('ai-cloud-badge');
    const sub = document.getElementById('ai-engine-subtitle');
    if (badge) {
      if (cfg.cloud_ready) {
        badge.className = 'px-3 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-xs font-mono font-semibold';
        badge.innerText = `Aktif: ${cfg.active_cloud_providers.join(' + ')}`;
      } else if (cfg.local_ollama_available) {
        badge.className = 'px-3 py-1 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 text-xs font-mono font-semibold';
        badge.innerText = `Yerel Ollama (${cfg.local_models.length} model) • Cloud Key yok`;
      } else {
        badge.className = 'px-3 py-1 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 text-xs font-mono font-semibold';
        badge.innerText = 'Cloud Key Gerekli (Aşağıdan Ekle)';
      }
    }
    const cWarn = document.getElementById('council-llm-warning');
    if (cWarn) cWarn.classList.toggle('hidden', Boolean(cfg.cloud_ready || cfg.local_ollama_available));
    const cPill = document.getElementById('council-llm-status-pill');
    if (cPill) {
      cPill.innerHTML = cfg.cloud_ready
        ? '<i class="fa-solid fa-brain mr-1.5"></i> Gemini AI Aktif'
        : (cfg.local_ollama_available ? '<i class="fa-solid fa-brain mr-1.5"></i> Yerel Ollama Hazır' : '<i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Kural Modu (Key Yok)');
    }
    if (sub) {
      sub.innerText = cfg.cloud_ready
        ? `Bulut Aktif: ${cfg.active_cloud_providers[0]}`
        : (cfg.local_ollama_available ? `Yerel LLM (${cfg.local_models.length} model)` : 'Cloud LLM Key bekleniyor');
    }
    const setSt = (id, ok, masked) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.className = ok ? 'text-xs text-emerald-400 font-medium' : 'text-xs text-slate-400';
      el.innerText = ok ? `Kayıtlı (${masked})` : 'Eklenmedi';
    };
    setSt('ai-gemini-status', cfg.gemini_configured, cfg.gemini_masked);
  } catch (e) {}
}

async function saveCloudAiConfig(e) {
  e.preventDefault();
  const payload = {};
  const g = document.getElementById('ai-cfg-gemini')?.value.trim();
  if (g) payload.gemini_api_key = g;
  const res = await fetch('/api/ai/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (res.ok) {
    const el = document.getElementById('ai-cfg-gemini');
    if (el) el.value = '';
    await fetchAiConfigStatus();
    showToast("Cloud LLM ayarları kaydedildi ve bulutla eşitlendi.");
  } else {
    showToast("Ayarlar kaydedilemedi.");
  }
}

async function testCloudAiEngine() {
  showToast("Yapay zekâ bağlantısı test ediliyor, lütfen bekle...");
  try {
    const res = await fetch('/api/ai/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model: document.getElementById('chat-model-select')?.value || 'auto' })
    }).then(r => r.json());
    if (res.status === 'ok') {
      showToast(`Bağlantı BAŞARILI! (${res.provider} / ${res.model_used} · ${res.elapsed_s}s)`);
    } else {
      showToast(`Bulut/Ollama yanıt vermedi (${res.provider}). Geçerli API Key eklediğinden emin ol.`);
    }
  } catch (e) {
    showToast("AI test isteği başarısız.");
  }
}


window.addEventListener('DOMContentLoaded', async () => {
  updateHeaderClock();
  setInterval(updateHeaderClock, 15000);
  initPrivacyMode();
  if (typeof syncDockLiquidPill === 'function') {
    requestAnimationFrame(() => syncDockLiquidPill(document.querySelector('#sidebar-right nav .sidebar-btn.active')));
  }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
  const isAuth = await checkAuthStatus();
  if (isAuth) {
    if (typeof bootstrapTerminal === 'function') {
      await bootstrapTerminal();
    } else {
      if (typeof loadAppPreferences === 'function') await loadAppPreferences(true);
      refreshAllData();
    }
    initProjectionChart(Number(window.appPrefs?.defaultMonthlyDeposit || 5500));
    fetchLiveNews();
    fetchTelegramStatus();
    fetchAiConfigStatus();
  } else {
    if (typeof loadAppPreferences === 'function') await loadAppPreferences(false);
  }
  if (typeof bindRichHoverPopovers === 'function') bindRichHoverPopovers();
  if (typeof rescheduleDataPolling === 'function') rescheduleDataPolling();
  setInterval(() => {
    if (document.hidden) return;
    const overlay = document.getElementById('auth-lock-overlay');
    if (overlay && overlay.classList.contains('hidden')) {
      fetchLiveNews();
    }
  }, 300000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && (!window.appPrefs || window.appPrefs.refreshOnTabFocus !== false)) {
      const overlay = document.getElementById('auth-lock-overlay');
      if (overlay && overlay.classList.contains('hidden')) fetchData();
    }
  });
});

function applyPersistenceStatusPayload(res) {
  if (!res) return;
  const badge = document.getElementById('persistence-mode-badge');
  const detail = document.getElementById('persistence-detail-text');
  const healthEl = document.getElementById('health-url-text');
  if (healthEl && res.health_url) healthEl.innerText = res.health_url;
  if (badge) {
    badge.innerText = res.turso_configured ? 'Turso Bulut Sync Aktif' : (res.is_render ? 'Oto-Kurtarma Kasası Aktif' : 'Yerel SQLite (Kalıcı)');
    badge.className = `px-3 py-1 rounded-full border text-xs font-mono font-semibold ${res.turso_configured ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : (res.is_render ? 'bg-sky-500/15 text-sky-300 border-sky-500/30' : 'bg-slate-800 text-slate-200 border-white/[0.08]')}`;
  }
  if (detail) {
    detail.innerText = res.turso_configured
      ? `Her işlem Turso bulut veritabanına otomatik eşitleniyor. Son sync: ${res.last_sync_at || res.last_restore_at || 'Aktif'}`
      : 'Render geçici diskinde işlemlerin deploy sonrası korunması için tarayıcı içi Oto-Kurtarma Kasası aktif. Çoklu cihaz eşitlemesi için TURSO_DATABASE_URL + TURSO_AUTH_TOKEN tanımlayabilirsiniz.';
  }
}

async function fetchPersistenceStatus() {
  try {
    const res = await fetch('/api/system/persistence').then(r => r.json());
    applyPersistenceStatusPayload(res);
  } catch (e) {}
}

async function handleBackupRestoreFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const ok = await showAppConfirm({
    title: 'JSON Yedeğini Geri Yükle',
    message: 'Seçtiğiniz JSON yedeği mevcut tüm hesapların, fonların ve defter kayıtlarının üzerine yazılacaktır. Devam edilsin mi?',
    confirmText: 'Yedeği Yükle',
    tone: 'warning'
  });
  if (!ok) {
    input.value = '';
    return;
  }
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const res = await fetch('/api/backup/restore', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    }).then(r => r.json());
    if (res.status === 'ok') {
      showToast(res.message || 'Yedek geri yüklendi.');
      refreshAllData();
    } else {
      showToast(res.detail || 'Yedek yüklenemedi.', 'error');
    }
  } catch (err) {
    showToast('Geçersiz JSON yedek dosyası: ' + err, 'error');
  } finally {
    input.value = '';
  }
}

function copyHealthUrl() {
  const txt = document.getElementById('health-url-text')?.innerText || '/api/health';
  navigator.clipboard.writeText(txt).then(() => showToast('Health check URL kopyalandı!'));
}

// Esc tuşu açık modalları kapatır
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (typeof closeAppDialog === 'function') closeAppDialog(null);
    if (typeof closePreferencesModal === 'function') closePreferencesModal();
    if (typeof closeBankSyncModal === 'function') closeBankSyncModal();
    if (typeof closeTxModal === 'function') closeTxModal();
    if (typeof closeEditTxModal === 'function') closeEditTxModal();
    if (typeof closeAccountModal === 'function') closeAccountModal();
    if (typeof closeChangePinModal === 'function') closeChangePinModal();
    if (typeof closeCommandPalette === 'function') closeCommandPalette();
  }
});


function copyCouncilDebate() {
  const topic = document.getElementById('arena-active-topic')?.innerText || 'Yatırım Konseyi';
  const consensus = document.getElementById('arena-consensus-summary')?.innerText || '';
  const txt = `[Yatırım Konseyi] ${topic}\n\n${consensus || 'Henüz konsey tartışması başlatılmadı.'}`.trim();
  navigator.clipboard.writeText(txt).then(() => {
    showToast('Konsey tartışma özeti panoya kopyalandı!');
  }).catch(() => showToast('Kopyalanamadı.', 'warn'));
}

function askCouncilAboutNews(newsTitle) {
  if (!newsTitle) return;
  setQuickTopic(`Güncel haber ışığında portföy stratejisi: "${newsTitle}" — ZBJ, TCA ve AFT dağılımı nasıl olmalı?`);
}


function copyTelegramSimOutput() {
  const txt = document.getElementById('tg-sim-output')?.innerText || '';
  if (!txt) return;
  navigator.clipboard.writeText(txt).then(() => {
    showToast('Bot önizleme yanıtı kopyalandı!');
  }).catch(() => showToast('Kopyalanamadı.', 'warn'));
}

// Üst Kaydırma İlerleme Çubuğu, Yukarı Çık Butonu, Ağ Durumu, Ticker Tıklama & Sekme Hafızası
(function initUxErgonomics() {
  const bar = document.getElementById('scroll-progress-bar');
  const topBtn = document.getElementById('back-to-top-btn');
  let scrollTicking = false;
  const onScrollFrame = () => {
    scrollTicking = false;
    const doc = document.documentElement;
    const maxY = Math.max(1, (doc.scrollHeight || 1) - (window.innerHeight || 1));
    const ratio = Math.min(1, Math.max(0, window.scrollY / maxY));
    if (bar) bar.style.transform = `scaleX(${ratio.toFixed(4)})`;
    if (topBtn) topBtn.classList.toggle('is-hidden', window.scrollY < 320);
  };
  window.addEventListener('scroll', () => {
    if (!scrollTicking) {
      scrollTicking = true;
      requestAnimationFrame(onScrollFrame);
    }
  }, { passive: true });

  const syncNetPill = () => {
    const pill = document.getElementById('net-status-pill');
    if (!pill) return;
    const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
    pill.classList.toggle('hidden', !offline);
    pill.classList.toggle('inline-flex', offline);
  };
  window.addEventListener('online', () => {
    syncNetPill();
    if (typeof showToast === 'function') showToast('İnternet bağlantısı yeniden sağlandı, veriler güncelleniyor.', 'info');
    if (typeof fetchData === 'function') fetchData(true);
  });
  window.addEventListener('offline', () => {
    syncNetPill();
    if (typeof showToast === 'function') showToast('Bağlantı kesildi — çevrimdışı önbellek görünüyor.', 'warn');
  });

  window.addEventListener('DOMContentLoaded', () => {
    if (localStorage.getItem('fin_dock_pinned') === '1' && typeof toggleSidebarPin === 'function') {
      toggleSidebarPin(true);
    }
    setInterval(updateFreshnessBadge, 10000);
    syncNetPill();

    document.querySelectorAll('.ticker-card[data-ticker]').forEach(card => {
      card.addEventListener('click', () => {
        const t = String(card.dataset.ticker || '').toUpperCase();
        const clickAct = (window.appPrefs && window.appPrefs.tickerClickAction) || 'buy';
        if (t === 'GOLD' || t === 'USD' || clickAct === 'radar') {
          if (typeof switchTab === 'function') switchTab('radar');
        } else if (typeof quickBuyFromRadar === 'function') {
          quickBuyFromRadar(t);
        }
      });
    });

    const hashTabRaw = (window.location.hash || '').replace(/^#/, '').trim();
    const hashTab = hashTabRaw === 'goals' ? 'terminal' : (hashTabRaw === 'splitter' ? 'tools' : hashTabRaw);
    const prefTab = (window.appPrefs && window.appPrefs.defaultTab && window.appPrefs.defaultTab !== 'last') ? window.appPrefs.defaultTab : '';
    const savedTabRaw = hashTab || prefTab || localStorage.getItem('fin_last_tab') || '';
    const savedTab = savedTabRaw === 'goals' ? 'terminal' : (savedTabRaw === 'splitter' ? 'tools' : savedTabRaw);
    const validTabs = ['terminal', 'ledger', 'radar', 'tools', 'council', 'telegram'];
    if (savedTab && savedTab !== 'terminal' && validTabs.includes(savedTab) && typeof switchTab === 'function') {
      requestAnimationFrame(() => switchTab(savedTab));
    }
  });

  window.addEventListener('hashchange', () => {
    const hashTabRaw = (window.location.hash || '').replace(/^#/, '').trim();
    const hashTab = hashTabRaw === 'goals' ? 'terminal' : (hashTabRaw === 'splitter' ? 'tools' : hashTabRaw);
    const validTabs = ['terminal', 'ledger', 'radar', 'tools', 'council', 'telegram'];
    if (validTabs.includes(hashTab) && typeof switchTab === 'function') {
      switchTab(hashTab);
    }
  });
})();

// =========================================================================
// AKILLI PORTFÖY DENGELEYİCİ & NAKİT DAĞITICI + TEMEL ÇALIŞMA TERCİHLERİ
// =========================================================================

const CMDK_ACTIONS = [
  { id: 'view-terminal', group: 'Görünümler', title: 'Ana Panel — Portföy & Hedef', hint: 'Özet', icon: 'fa-solid fa-chart-line text-indigo-300', keywords: 'ana panel portfoy ozet kumbara hedef', run: () => switchTab('terminal') },
  { id: 'view-ledger', group: 'Görünümler', title: 'Defter & Kasa — İşlemler ve Hesaplar', hint: 'Muhasebe', icon: 'fa-solid fa-book-bookmark text-cyan-300', keywords: 'defter kasa hesap harcama gelir virman islem', run: () => switchTab('ledger') },
  { id: 'view-radar', group: 'Görünümler', title: 'Fon Radarı — TEFAS Canlı Kıyaslama', hint: 'Analiz', icon: 'fa-solid fa-satellite-dish text-purple-300', keywords: 'fon radar tefas kiyasla getiri', run: () => switchTab('radar') },
  { id: 'view-tools', group: 'Görünümler', title: 'Araçlar & Plan — Rebalance, Paylaştırıcı, Stopaj', hint: 'Planlama', icon: 'fa-solid fa-wand-magic-sparkles text-teal-300', keywords: 'araclar plan dagit stopaj rebalance alarm nemalanma', run: () => switchTab('tools') },
  { id: 'view-council', group: 'Görünümler', title: 'AI Konsey — Haberler & 4 Model Arena', hint: 'Danışman', icon: 'fa-solid fa-brain text-indigo-400', keywords: 'ai konsey haber arena gemini soru', run: () => switchTab('council') },
  { id: 'view-telegram', group: 'Bağlantı', title: 'Telegram Bot Asistanı', hint: 'Bildirim', icon: 'fa-brands fa-telegram text-sky-400', keywords: 'telegram bot bildirim mobil webhook', run: () => switchTab('telegram') },
  { id: 'act-tx', group: 'Hızlı Eylem', title: 'Yeni İşlem / Fon Al-Sat / Virman', hint: 'Modal Aç', icon: 'fa-solid fa-plus text-emerald-300', keywords: 'yeni islem al sat virman harcama ekle', run: () => { if (typeof openTxModal === 'function') openTxModal(); } },
  { id: 'act-buy-zbj', group: 'Hızlı Fon', title: 'ZBJ Alım Emri Aç (Para Piyasası)', hint: 'PPF', icon: 'fa-solid fa-bolt text-cyan-300', keywords: 'zbj alim ppf para piyasasi', run: () => { if (typeof quickBuyFromRadar === 'function') quickBuyFromRadar('ZBJ', 0); } },
  { id: 'act-buy-tca', group: 'Hızlı Fon', title: 'TCA Alım Emri Aç (Altın Katılım)', hint: 'Altın', icon: 'fa-solid fa-coins text-amber-300', keywords: 'tca alim altin', run: () => { if (typeof quickBuyFromRadar === 'function') quickBuyFromRadar('TCA', 0); } },
  { id: 'act-buy-aft', group: 'Hızlı Fon', title: 'AFT Alım Emri Aç (Yeni Teknolojiler)', hint: 'Nasdaq', icon: 'fa-solid fa-rocket text-indigo-300', keywords: 'aft alim teknoloji nasdaq', run: () => { if (typeof quickBuyFromRadar === 'function') quickBuyFromRadar('AFT', 0); } },
  { id: 'act-prefs', group: 'Sistem', title: 'Görünüm & Tercih Ayarları', hint: 'Ayarlar', icon: 'fa-solid fa-sliders text-indigo-300', keywords: 'ayarlar tercih tema yogunluk kontrast hedef yenileme', run: () => { if (typeof openPreferencesModal === 'function') openPreferencesModal(); } },
  { id: 'act-print', group: 'Sistem', title: 'Resmi Hesap Özeti Yazdır / PDF', hint: 'Rapor', icon: 'fa-solid fa-print text-emerald-300', keywords: 'yazdir pdf rapor ekstre ozet', run: () => { if (typeof openOfficialStatementPrint === 'function') openOfficialStatementPrint(); } },
  { id: 'act-refresh', group: 'Sistem', title: 'TEFAS & Kur Verilerini Şimdi Güncelle', hint: 'Canlı Çek', icon: 'fa-solid fa-arrows-rotate text-emerald-300', keywords: 'guncelle yenile tefas dolar kur', run: () => { if (typeof refreshAllData === 'function') refreshAllData(true); } },
  { id: 'act-privacy', group: 'Sistem', title: 'Gizlilik Modunu Değiştir (Tutarları Gizle/Göster)', hint: 'Maske', icon: 'fa-solid fa-eye-slash text-slate-300', keywords: 'gizlilik tutar gizle maske', run: () => { if (typeof togglePrivacyMode === 'function') togglePrivacyMode(); } },
  { id: 'act-backup', group: 'Sistem', title: 'Tam Veri Yedeği İndir (JSON)', hint: 'İndir', icon: 'fa-solid fa-download text-slate-300', keywords: 'yedek backup json indir disari aktar', run: () => { window.location.href = '/api/backup/download'; } }
];

let __cmdkFiltered = [...CMDK_ACTIONS];
let __cmdkActiveIdx = 0;

function renderCommandPaletteResults() {
  const box = document.getElementById('cmdk-results');
  if (!box) return;
  if (__cmdkFiltered.length === 0) {
    box.innerHTML = `<div class="py-8 text-center text-xs text-slate-400">Eşleşen komut bulunamadı. Başka bir kelime dene (örn: ZBJ, defter, yedek).</div>`;
    return;
  }
  box.innerHTML = __cmdkFiltered.map((item, idx) => {
    const active = idx === __cmdkActiveIdx;
    return `
      <button type="button" data-cmdk-idx="${idx}" onclick="executeCommandItem(${idx})" onmouseenter="__cmdkActiveIdx=${idx};renderCommandPaletteResults()" class="w-full flex items-center justify-between gap-3 px-3.5 py-2.5 rounded-xl text-left transition border ${active ? 'bg-indigo-500/20 border-indigo-400/45 text-white shadow-[0_0_20px_rgba(99,102,241,0.18)]' : 'bg-transparent border-transparent text-slate-300 hover:bg-white/[0.04]'}">
        <div class="flex items-center gap-3 min-w-0">
          <div class="w-8 h-8 rounded-lg bg-[#070c1e] border border-white/[0.08] flex items-center justify-center shrink-0">
            <i class="${escapeHtml(item.icon)} text-xs"></i>
          </div>
          <div class="min-w-0">
            <div class="text-xs sm:text-[13px] font-bold truncate">${escapeHtml(item.title)}</div>
            <div class="text-[11px] text-slate-400">${escapeHtml(item.group)}</div>
          </div>
        </div>
        <span class="px-2 py-0.5 rounded-md bg-white/[0.06] border border-white/[0.08] text-[10px] font-mono text-slate-300 shrink-0">${escapeHtml(item.hint)}</span>
      </button>
    `;
  }).join('');
}

function filterCommandPalette(query) {
  const q = String(query || '').toLocaleLowerCase('tr-TR').trim();
  __cmdkFiltered = !q
    ? [...CMDK_ACTIONS]
    : CMDK_ACTIONS.filter(item =>
        (item.title + ' ' + item.group + ' ' + item.keywords).toLocaleLowerCase('tr-TR').includes(q)
      );
  __cmdkActiveIdx = 0;
  renderCommandPaletteResults();
}

function openCommandPalette() {
  const modal = document.getElementById('cmdk-modal');
  const inp = document.getElementById('cmdk-input');
  if (!modal) return;
  modal.classList.remove('hidden');
  __cmdkFiltered = [...CMDK_ACTIONS];
  __cmdkActiveIdx = 0;
  if (inp) {
    inp.value = '';
    setTimeout(() => inp.focus(), 20);
  }
  renderCommandPaletteResults();
}

function closeCommandPalette() {
  const modal = document.getElementById('cmdk-modal');
  if (modal) modal.classList.add('hidden');
}

function executeCommandItem(idx) {
  const item = __cmdkFiltered[idx];
  if (!item) return;
  closeCommandPalette();
  try { item.run(); } catch (e) { console.error('CMDK action failed:', e); }
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && String(e.key || '').toLowerCase() === 'k') {
    e.preventDefault();
    const modal = document.getElementById('cmdk-modal');
    if (modal && modal.classList.contains('hidden')) openCommandPalette();
    else closeCommandPalette();
    return;
  }
  const modal = document.getElementById('cmdk-modal');
  if (!modal || modal.classList.contains('hidden')) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    closeCommandPalette();
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (__cmdkFiltered.length > 0) {
      __cmdkActiveIdx = (__cmdkActiveIdx + 1) % __cmdkFiltered.length;
      renderCommandPaletteResults();
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (__cmdkFiltered.length > 0) {
      __cmdkActiveIdx = (__cmdkActiveIdx - 1 + __cmdkFiltered.length) % __cmdkFiltered.length;
      renderCommandPaletteResults();
    }
  } else if (e.key === 'Enter') {
    e.preventDefault();
    executeCommandItem(__cmdkActiveIdx);
  }
});
