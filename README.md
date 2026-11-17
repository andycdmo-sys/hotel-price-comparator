# Hotel Price Comparator（Python + Playwright 飯店比價工具）

這是一個模組化的飯店比價專案架構，使用 **Python + Playwright** 開啟獨立的無痕 `BrowserContext`，支援從 `.env` 讀取 Proxy 設定（含帳號密碼驗證），並將各平台比價結果依價格由低到高排序後匯出為 Excel 友善的 CSV（UTF-8 BOM）。

## 專案結構

```text
hotel-price-comparator/
├── .env.example          # 環境變數範本
├── .env                  # 本機環境變數（已加入 .gitignore）
├── .gitignore
├── requirements.txt
├── main.py               # CLI 主程式進入點
├── src/
│   ├── config.py         # 讀取 .env 與 Proxy 設定
│   ├── models.py         # 房價結果資料模型 (HotelPriceResult)
│   ├── browser.py        # Playwright 無痕 BrowserContext 管理
│   ├── exporter.py       # CSV 匯出與價格排序
│   └── scrapers/
│       ├── base.py       # 爬蟲抽象基底類別 (BaseHotelScraper) 與錯誤隔離執行器
│       └── demo_hotel.py # 示範平台爬蟲（使用本機測試 DOM）
└── tests/                # pytest 單元測試
```

## 快速開始

### 1. 建立虛擬環境並安裝套件

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

### 2. 設定 `.env`（選用 Proxy）

專案已預先建立 `.env`，若需要透過 Proxy 連線，請編輯 `.env`：

```env
PROXY_SERVER=http://127.0.0.1:7890
PROXY_USERNAME=your_username
PROXY_PASSWORD=your_password
HEADLESS=true
TIMEOUT_MS=30000
DEFAULT_CURRENCY=TWD
```

若 `PROXY_SERVER` 留空，程式會自動以直連模式啟動無痕瀏覽器。

### 3. 執行比價程式

```powershell
python main.py --hotel "台北君悅酒店" --check-in 2026-12-01 --check-out 2026-12-02
```

常用參數：
- `--no-headless`：顯示瀏覽器視窗以利除錯
- `--output output/my_prices.csv`：自訂輸出 CSV 路徑

### 4. 執行單元測試

```powershell
pytest
```

## 如何擴充真實訂房網站爬蟲

1. 在 `src/scrapers/` 下新增平台檔案（例如 `booking.py`）。
2. 建立類別繼承 `BaseHotelScraper`，設定 `platform_name` 並實作 `fetch_price(self, context, hotel_name, check_in, check_out)`。
3. 在 `main.py` 的 `scrapers` 清單中加入該類別實例即可。
