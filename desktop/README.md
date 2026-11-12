# Safe Agents Desktop (M3–M7)

מדריך למשתמש הקצה: [`docs/USER_GUIDE.md`](../docs/USER_GUIDE.md).

אפליקציית Desktop להורדה, התקנה והפעלה של אייג'נטים: **Tauri 2** (חלון ו-sidecar) + **React/TypeScript** (ממשק) + **Agent Manager** ב-Python (כל הלוגיקה, ראו [`../manager`](../manager)).

```
React (webview) ──HTTP + token──▶ Agent Manager (sidecar) ──spawn──▶ langgraph dev לכל אייג'נט
       └──────────── useStream + token לכל הפעלה ─────────────────────▲
Rust: מפעיל את ה-Manager עם token אקראי, מעביר ל-UI כתובת+token, ובסגירה מבקש shutdown מסודר
```

## מסכים

| מסך            | מה יש בו                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------- |
| הפעלה ראשונה   | הזנת מפתח OpenRouter – נבדק מול OpenRouter לפני שמירה                                                   |
| חנות           | קטלוג, תצוגת הרשאות לפני התקנה, התקנה/עדכון עם התקדמות חיה, התקנה מקובץ ZIP                             |
| האייג'נטים שלי | פתיחת שיחה, מפתחות ו-MCP לכל אייג'נט, יומן, עצירה, הסרה, סימון עדכון זמין                               |
| שיחה           | סטרימינג, סטטוס ריצה, כרטיס אישור (HITL), צירוף קבצים, בחירת מודל, עזרה, ייצוא שיחה, עלות, שיחות קודמות |
| הגדרות         | מצב המפתח והשימוש ב-OpenRouter, LangSmith (opt-in), עלויות לפי מודל, אודות ותמיכה                       |

## פיתוח

דרישות: Node 22, Rust stable, [uv](https://docs.astral.sh/uv/). ב-Linux גם `libwebkit2gtk-4.1-dev`.

```bash
cd desktop
npm install
# ה-Manager רץ מקוד המקור (לא צריך לבנות sidecar בזמן פיתוח):
SAFE_AGENTS_DEV_MANAGER="uv run --project ../manager safe-agents-manager" npm run tauri dev
```

ב-Windows (PowerShell):

```powershell
$env:SAFE_AGENTS_DEV_MANAGER="uv run --project ../manager safe-agents-manager"
npm run tauri dev
```

נתיב יחסי בפקודה נפתר מתיקיית `desktop/`. נתיב עם רווחים לא נתמך.

אפשר גם לעבוד על הממשק בדפדפן רגיל מול Manager שרץ בנפרד:

```bash
SAFE_AGENTS_TOKEN=dev SAFE_AGENTS_CORS_ORIGINS=http://localhost:1420 \
  uv run --project ../manager safe-agents-manager serve --port 23700
VITE_MANAGER_URL=http://127.0.0.1:23700 VITE_MANAGER_TOKEN=dev npm run dev
```

## בדיקות

```bash
npm run format:check && npm run lint && npm run typecheck
npm test                 # Vitest – לוגיקה ורכיבים
npx playwright test      # E2E: ממשק אמיתי מול Manager ואייג'נט אמיתיים (LLM מדומה + OpenRouter מדומה)
cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

בדיקות ה-E2E מרימות לבד את כל השרתים (`e2e/start-manager.sh`): תיקיית נתונים חדשה, התקנת `examples/foo-agent` מ-zip, סודות בזיכרון ו-OpenRouter מדומה.

## אריזה והפצה

```bash
# 1. ה-Manager כקובץ הפעלה אחד (PyInstaller) + uv נעול עם בדיקת sha256 → src-tauri/binaries/
uv run --project ../manager python ../manager/scripts/build_sidecar.py
# 2. האפליקציה עם שני ה-sidecars
npx tauri build --config src-tauri/tauri.release.conf.json
```

ה-workflow `.github/workflows/release.yml` עושה זאת ב-Windows, macOS ו-Linux כשדוחפים tag (`v0.1.0`) ומעלה את קבצי ההתקנה ל-Release בטיוטה.

### חתימת קוד (לפני הפצה ציבורית)

בלי חתימה Windows SmartScreen ו-macOS Gatekeeper מציגים אזהרה בהפעלה הראשונה.

- **Windows:** תעודת Code Signing (OV/EV); מגדירים `bundle.windows.certificateThumbprint` או `signCommand` ב-`tauri.release.conf.json`.
- **macOS:** Apple Developer ID; הסודות `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` ב-GitHub Secrets (tauri-action משתמש בהם אוטומטית).

## אבטחה

| שכבה           | מה נעשה                                                                            |
| -------------- | ---------------------------------------------------------------------------------- |
| Manager API    | `127.0.0.1` בלבד, Bearer token אקראי מה-Rust, CORS רק ל-webview                    |
| שרתי אייג'נטים | token חדש לכל הפעלה (`platform_auth.py` שנוצר אוטומטית), CORS רק ל-webview         |
| Webview        | CSP: חיבור רק ל-`127.0.0.1`; אין ל-JS הרשאות shell (רק `core:default`)             |
| מפתחות         | OS keychain; ה-API לעולם לא מחזיר ערך של מפתח                                      |
| קבצים          | שמות מנוקים, סיומות לפי המניפסט, מגבלת גודל, תיקיית `inbox` של האייג'נט            |
| תהליכים        | Job Object (Windows), PID registry (macOS/Linux), עצירה כשהאפליקציה נסגרת או קורסת |
