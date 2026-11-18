#!/usr/bin/env bash
# ======================================================================
# Finans Terminali — İsteğe Bağlı Tek Tıkla Başlatıcı
# ======================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Masaüstü bildirimi gönder (kullanıcı tıklandığını anında anlasın)
notify-send "Finans Terminali" "Terminal açılıyor..." -i utilities-terminal 2>/dev/null || true

# Eğer zaten çalışıyorsa doğrudan tarayıcıyı aç
if curl -s --connect-timeout 1 http://127.0.0.1:8080/api/portfolio >/dev/null 2>&1; then
    notify-send "Finans Terminali" "Terminal zaten aktif! Tarayıcı açılıyor..." -i utilities-terminal 2>/dev/null || true
    xdg-open "http://127.0.0.1:8080" >/dev/null 2>&1 || firefox "http://127.0.0.1:8080" >/dev/null 2>&1 || true
    exit 0
fi

# Çalışmıyorsa arka planda başlat
nohup python3 app.py > /tmp/finans_terminal.log 2>&1 &
PID=$!
disown $PID 2>/dev/null || true

# Port hazır olana kadar bekle (maksimum 10 saniye)
for i in {1..20}; do
    if curl -s --connect-timeout 1 http://127.0.0.1:8080/api/portfolio >/dev/null 2>&1; then
        notify-send "Finans Terminali" "Terminal hazır! İyi çalışmalar." -i utilities-terminal 2>/dev/null || true
        xdg-open "http://127.0.0.1:8080" >/dev/null 2>&1 || firefox "http://127.0.0.1:8080" >/dev/null 2>&1 || true
        exit 0
    fi
    sleep 0.5
done

# Eğer açılamadıysa hata bildirimi at
notify-send "Finans Terminali" "Sunucu başlatılamadı! Log: /tmp/finans_terminal.log" -u critical 2>/dev/null || true
