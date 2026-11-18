#!/usr/bin/env bash
# Finans Terminali Başlatma Betiği

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=========================================="
echo "  Finans & Portföy Terminali Başlatılıyor"
echo "  Portföy: ZBJ + TZL | Model: furkan-finans-ai"
echo "=========================================="

if [ "$1" == "--docker" ]; then
    echo "Docker konteyneri derleniyor ve başlatılıyor..."
    docker compose up -d --build
    echo "Terminal hazır! Tarayıcıdan açabilirsiniz: http://localhost:8080"
else
    echo "Doğrudan Python ile başlatılıyor..."
    python3 app.py
fi
