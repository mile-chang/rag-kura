# 如何設定 Cloudflare Tunnel（內部筆記）

## 前言

Cloudflare Tunnel（舊稱 Argo Tunnel）可以讓你在不開放防火牆連接埠的情況下，將內部服務安全地暴露到公網。本筆記記錄了在 Ubuntu 22.04 伺服器上設定 Tunnel 的完整流程，供團隊內部參考。

## 環境資訊

- **伺服器 IP**：`192.168.10.87`（內部網段，不對外開放）
- **作業系統**：Ubuntu 22.04 LTS
- **Cloudflare 帳號**：ops-team@kura-corp.local
- **目標服務**：運行在 `http://localhost:8080` 的內部 Wiki

## 安裝 cloudflared

```bash
# 下載最新版 cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 驗證安裝
cloudflared --version
# 預期輸出：cloudflared version 2025.12.1
```

## 登入與建立 Tunnel

```bash
# 登入 Cloudflare（會開啟瀏覽器進行 OAuth 授權）
cloudflared tunnel login

# 建立名為 "internal-wiki" 的 Tunnel
cloudflared tunnel create internal-wiki
# 輸出範例：Created tunnel internal-wiki with id a3f1-b7c2-d4e5-f6a7

# 查看已建立的 Tunnel
cloudflared tunnel list
```

## 設定 DNS 路由

```bash
# 將 Tunnel 綁定到子網域
cloudflared tunnel route dns internal-wiki wiki.kura-corp.com
```

## 建立設定檔

在 `/etc/cloudflared/config.yml` 中寫入以下內容：

```yaml
tunnel: a3f1-b7c2-d4e5-f6a7
credentials-file: /root/.cloudflared/a3f1-b7c2-d4e5-f6a7.json

ingress:
  - hostname: wiki.kura-corp.com
    service: http://localhost:8080
  - service: http_status:404
```

## 啟動與設定開機自動執行

```bash
# 手動啟動測試
cloudflared tunnel run internal-wiki

# 安裝為 systemd 服務
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared

# 確認服務狀態
sudo systemctl status cloudflared
```

## 驗證

從外部網路存取 `https://wiki.kura-corp.com`，確認可以正常連線到內部 Wiki 服務。也可以在伺服器上用 `curl` 測試：

```bash
curl -I https://wiki.kura-corp.com
# 預期回應 HTTP/2 200
```

## 注意事項

1. 憑證檔案 (`*.json`) 務必妥善保管，洩漏等同於暴露 Tunnel 存取權。
2. 若伺服器位於 NAT 後方（如 `192.168.10.87`），無需設定任何 port forwarding。
3. 建議搭配 Cloudflare Access 設定零信任存取策略，限制可存取的使用者群組。
