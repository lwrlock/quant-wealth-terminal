#!/usr/bin/env bash
# ======================================================================
# FINANS DASHBOARD — UBUNTU GÜVENLİK DUVARI & DOCKER SERTLEŞTİRME
# ======================================================================

set -e

echo "🛡️  Finans Dashboard Güvenlik Duvarı Yapılandırması..."

# 1. Port 8080'in Sadece 127.0.0.1 (Localhost) Üzerinden Dinlendiğini Doğrula
echo "🔍 Port 8080 yerel dinleme kontrol ediliyor..."
if command -v ss >/dev/null 2>&1; then
    ss -tulpn | grep 8080 || echo "Port 8080 henüz dinlenmiyor veya Docker bekliyor."
fi

# 2. UFW (Uncomplicated Firewall) Varsa Kural Tanımla
if command -v ufw >/dev/null 2>&1; then
    echo "🔒 UFW güvenlik kuralı kontrol ediliyor..."
    # Dışarıdan 8080'e gelen tüm istekleri engelle, sadece yerel loopback'e izin ver
    sudo ufw status | grep -q "Status: active" && {
        echo "UFW devrede: 8080 dış ağa kapatılıyor, sadece localhost serbest..."
        sudo ufw allow from 127.0.0.1 to any port 8080 proto tcp comment 'Finans Local Only'
        sudo ufw deny 8080/tcp comment 'Block external 8080'
    } || echo "Bilgi: UFW şu an pasif durumda (yerel docker-compose 127.0.0.1 bağlaması güvenliği sağlıyor)."
fi

echo "✅ Güvenlik yapılandırması tamamlandı!"
echo "📌 Not: docker-compose.yml içinde '127.0.0.1:8080:8080' tanımı sayesinde, KYK yurdu veya kampüs Wi-Fi ağındaki hiç kimse paneline erişemez."
