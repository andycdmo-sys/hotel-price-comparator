/**
 * Google Apps Script 免費雲端轉發器（讓朋友在網頁按「💾 儲存我的清單」後，自動存進 GitHub 並觸發抓價）
 *
 * 設定步驟：
 * 1. 前往 https://script.google.com 點擊「新專案」，貼上整份程式碼。
 * 2. 在 GitHub 產生一組僅有 Actions & Contents 權限的 Fine-grained Personal Access Token (PAT)。
 * 3. 在 Apps Script 左側齒輪「專案設定」→「指令碼屬性 (Script Properties)」新增：
 *      屬性名稱：GITHUB_TOKEN   ｜ 值：您的 GitHub PAT
 * 4. 點擊右上角「部署」→「新增部署作業」→ 類型選「網頁應用程式」：
 *      - 執行身分：我
 *      - 誰可以存取：所有人
 * 5. 複製取得的「網頁應用程式 URL (https://script.google.com/macros/s/.../exec)」貼回給 Antigravity 寫入網頁即可！
 */

const GITHUB_OWNER = "andycdmo-sys";
const GITHUB_REPO = "hotel-price-comparator";
const WORKFLOW_FILE = "daily_price_check.yml";
const USER_ID_PATTERN = /^[A-Za-z0-9_-]{2,32}$/;

function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, message: "Hotel Price Comparator Relay Ready" }))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    const rawBody = (e && e.postData && e.postData.contents) ? e.postData.contents : "";
    if (!rawBody) {
      return jsonResponse({ ok: false, error: "Empty request body" });
    }

    const profileObj = JSON.parse(rawBody);
    const userId = String(profileObj.user_id || "").trim();
    if (!USER_ID_PATTERN.test(userId)) {
      return jsonResponse({ ok: false, error: "Invalid user_id format" });
    }

    const token = PropertiesService.getScriptProperties().getProperty("GITHUB_TOKEN");
    if (!token) {
      return jsonResponse({ ok: false, error: "Missing GITHUB_TOKEN in Script Properties" });
    }

    const apiUrl =
      "https://api.github.com/repos/" +
      GITHUB_OWNER + "/" + GITHUB_REPO +
      "/actions/workflows/" + WORKFLOW_FILE + "/dispatches";

    const payload = {
      ref: "main",
      inputs: {
        user_id: userId,
        profile_json: JSON.stringify(profileObj)
      }
    };

    const resp = UrlFetchApp.fetch(apiUrl, {
      method: "post",
      contentType: "application/json",
      headers: {
        Authorization: "Bearer " + token,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    const code = resp.getResponseCode();
    if (code >= 200 && code < 300) {
      return jsonResponse({ ok: true, user_id: userId });
    }
    return jsonResponse({
      ok: false,
      error: "GitHub API error (" + code + "): " + resp.getContentText()
    });
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) });
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
