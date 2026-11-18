#!/bin/bash
# Laptop Şarjdayken Kapağı Kapatınca Uyumama (7/24 Bot Çalıştırma) Ayarı
echo "==> Laptop şarjdayken kapağı kapatınca uyumama ayarı yapılıyor..."
sudo sed -i 's/#HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sudo sed -i 's/HandleLidSwitchExternalPower=suspend/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
echo "==> Tamamlandı! Artık laptop şarjdayken kapağı kapatsan bile ekran kapanır ama bot 7/24 çalışmaya devam eder."
