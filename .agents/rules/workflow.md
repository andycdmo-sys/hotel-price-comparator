---
trigger: always_on
---
# 專案協作與互動規範 (Project Workflow & Interaction Rules)

## 1. 語言與電腦授權說明規範
1. **全面使用繁體中文（zh-TW）**：
   - 所有對話回覆、說明、程式碼註解與錯誤訊息一律使用繁體中文。
2. **觸發電腦授權時的中文說明義務**：
   - 任何指令（例如需連網、存取工作區以外的檔案、安裝套件或操作 GitHub）預期會跳出電腦授權提示時，**必須先以繁體中文清楚說明「目前面臨的問題／執行目的」以及「為什麼需要這項授權」**。
   - 呼叫 `run_command` 等工具時，`toolSummary`（工具摘要標題）一律使用**繁體中文**撰寫，確保使用者在授權視窗中能直接看懂即將執行的動作。

## 2. 跨電腦雲端同步規範（Git / GitHub）
1. **開工前先同步**：
   - 本專案設有 GitHub Actions 排程（`.github/workflows/daily_price_check.yml`），會定期自動更新 `output/okinawa_2027_history.csv` 與 `docs/index.html`。
   - 每次在任何電腦開始編輯或設計前，請先執行 `git pull --rebase` 拉取雲端最新版本，避免產生衝突。
2. **完工後推回雲端**：
   - 修改並驗證完成後，將變更 `git add`、`git commit` 並 `git push` 回 `origin main`，確保其他電腦可接續開發。
3. **機密資料保護**：
   - 絕不將 `.env`、Proxy 帳密或任何通知金鑰／Token 提交至 Git；雲端排程所需的機密資訊一律存放於 GitHub Repository Secrets。

## 3. 開發與驗證規範
1. **Python 虛擬環境**：
   - 優先使用專案根目錄 `.venv` 內的 Python 執行測試與腳本（`.\.venv\Scripts\python.exe`）。
   - `pytest` 單元測試可直接於沙盒內執行（測試內不呼叫 `asyncio.run()`）；僅執行需要連網或實際啟動 Playwright 瀏覽器的命令時，因沙盒網路隔離與 `asyncio` 事件迴圈會建立本機 `socketpair`，執行前請以中文向使用者說明理由後再於沙盒外執行。
2. **品質檢查**：
   - 調整 `src/` 程式碼或追蹤清單後，務必執行 `.\.venv\Scripts\python.exe -m pytest -v` 確認測試全數通過。
   - 若調整 `OKINAWA_WATCHLIST` 或匯出格式，需同步確認 `docs/index.html` 可正常產出。
