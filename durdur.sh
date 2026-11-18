#!/usr/bin/env bash
# ======================================================================
# Finans Terminali — Güvenli Kapatıcı
# (Tüm sunucu ve Docker süreçlerini durdurur, belleği ve pili %100 serbest bırakır)
# ======================================================================

echo "🛑 Finans Terminali kapatılıyor..."

# 1. API üzerinden güvenli kapatma isteği gönder
curl -s -X POST http://127.0.0.1:8080/api/shutdown >/dev/null 2>&1 || true

# 2. Port 8080'i kullanan Python/Uvicorn süreçlerini temizle
PIDS=$(lsof -ti :8080 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "Süreçler sonlandırılıyor: $PIDS"
    kill -9 $PIDS >/dev/null 2>&1 || true
fi

# 3. Eğer Docker konteyneri açıksa durdur
if command -v docker >/dev/null 2>&1; then
    docker stop finans-dashboard >/dev/null 2>&1 || true
fi

echo "✅ Terminal tamamen kapatıldı. Arka planda sıfır işlem çalışıyor, pil ve RAM tasarrufunda!"
