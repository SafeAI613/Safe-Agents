# Safe-Agents Manager

תהליך Python ללא ממשק (headless) שמתקין, מריץ ומנהל אייג'נטים של LangGraph.
בהמשך (M3) אפליקציית ה-Desktop (Tauri) תריץ אותו כ-sidecar ותדבר איתו דרך ה-API המקומי.

## מה הוא עושה

1. **קטלוג (M2)**: קריאת `registry.json` מהקטלוג המרכזי עם מטמון ETag (עובד גם offline),
   הורדה מאובטחת (https בלבד, רשימת hosts מותרים של GitHub, מגבלת גודל, אימות sha256),
   וזיהוי עדכונים זמינים.
2. **התקנה** מקובץ zip: אימות sha256 → חילוץ בטוח → בדיקת `manifest.json` → יצירת סביבת Python
   מבודדת עם `uv` → יצירת `langgraph.json` → בדיקת הפעלה (smoke test) → רישום ב-SQLite.
   כל כשל מבטל את ההתקנה ומוחק את כל מה שנוצר.
3. **הרצה**: שרת `langgraph dev` נפרד לכל אייג'נט, על פורט פנוי מהטווח 2100–2199,
   קשור ל-`127.0.0.1` בלבד, עם סביבה מינימלית ורק המפתחות שהאייג'נט הצהיר עליהם.
4. **API מקומי** (FastAPI) עם Bearer token, כולל סטרימינג של התקדמות ההתקנה (SSE).

## דרישות

- [uv](https://docs.astral.sh/uv/) (Python לאייג'נטים מורד אוטומטית ע"י uv לתוך תיקיית הנתונים).

## שימוש ב-CLI

```bash
cd manager
uv sync

uv run safe-agents-manager catalog [--refresh]                 # מה יש בקטלוג
uv run safe-agents-manager install foo_agent [--version 1.0.0]  # התקנה/עדכון מהקטלוג
uv run safe-agents-manager install ../path/to/agent.zip [--sha256 <hex>]  # מקובץ מקומי
uv run safe-agents-manager list
OPENROUTER_API_KEY=... uv run safe-agents-manager run foo_agent   # Ctrl+C לעצירה
uv run safe-agents-manager uninstall foo_agent
SAFE_AGENTS_TOKEN=... uv run safe-agents-manager serve --port 0

# בדיקת registry.json (ה-CI של ריפו הקטלוג מריץ את זה על כל PR)
uv run safe-agents-manager validate-registry registry.json --base base.json --install
```

`serve --exit-on-stdin-close --parent-pid <pid>` (כך מפעיל אותו ה-Desktop) עוצר את כל האייג'נטים ויוצא כש-stdin נסגר, כשמתקבלת השורה `shutdown`, או כשתהליך האב נעלם.
`serve` מדפיס שורת JSON אחת כשהוא מאזין: `{"event": "ready", "port": 54321}`
(ואם לא הועבר `SAFE_AGENTS_TOKEN` – גם `token` שנוצר אקראית).

| משתנה סביבה | תפקיד |
|---|---|
| `SAFE_AGENTS_HOME` | תיקיית הנתונים (ברירת מחדל: תיקיית הנתונים של מערכת ההפעלה) |
| `SAFE_AGENTS_UV` | נתיב ל-uv (ברירת מחדל: ליד קובץ ההפעלה, ואז PATH) |
| `SAFE_AGENTS_TOKEN` | הטוקן שה-API דורש |
| `SAFE_AGENTS_REGISTRY_URL` | כתובת הקטלוג (https), או נתיב לקובץ מקומי בזמן פיתוח |
| `SAFE_AGENTS_SECRETS` | `memory` – סודות בזיכרון במקום OS keychain (בדיקות/CI) |
| `SAFE_AGENTS_CORS_ORIGINS` | origins נוספים מלבד ה-webview (למשל `http://localhost:1420` בפיתוח) |
| `SAFE_AGENTS_OPENROUTER_URL` · `SAFE_AGENTS_EXPORT_DIR` | החלפת כתובת OpenRouter ותיקיית הייצוא (בדיקות E2E) |
| `OPENROUTER_API_KEY` וכו' | ב-M1 מפתחות נקראים מהסביבה; ב-M3 יגיעו מה-keychain |

## API

| Method | Path | תיאור |
|---|---|---|
| GET | `/health` | ללא טוקן |
| GET | `/agents` · `/agents/{id}` | רשימה / פרטים + סטטוס + url |
| POST | `/agents/install` | `{"archive_path", "sha256"?}` ← 202 + job |
| GET | `/registry?refresh=false` | הקטלוג + `installed_version` + `update_available` + `stale` |
| POST | `/registry/{id}/install` | `{"version"?}` ← 202 + job (התקנה או עדכון) |
| GET | `/jobs/{id}` · `/jobs/{id}/events` | סטטוס / SSE (`progress`, `done`) |
| POST | `/agents/{id}/start` · `/stop` | הפעלה (← `url`, `graph_id`) / עצירה |
| GET | `/agents/{id}/logs?tail=200` | סוף הלוג |
| DELETE | `/agents/{id}` | הסרה (כולל המפתחות של האייג'נט) |
| GET/PUT | `/agents/{id}/secrets` | שדות המפתחות (env + MCP) עם `set`; עדכון (ערך ריק = מחיקה) |
| POST | `/agents/{id}/files` | העלאת קובץ ל-`inbox` (multipart) ← נתיב מלא |
| GET | `/agents/{id}/readme` · POST `/agents/{id}/heartbeat` | מדריך המפתח · "האייג'נט בשימוש" |
| GET | `/registry/{id}/manifest` | המניפסט שפורסם – הרשאות ויכולות לפני התקנה |
| GET | `/settings` · PUT/DELETE `/settings/openrouter-key` · `/settings/langsmith-key` | הגדרות ומפתחות גלובליים (OpenRouter נבדק לפני שמירה) |
| GET | `/settings/openrouter-key/status` · `/models/pricing` | שימוש ומגבלה בחשבון · מחירי מודלים (מטמון 24 שעות) |
| POST/GET | `/usage` | רישום טוקנים לפי הודעה (ללא כפילויות) · סיכום עלויות |
| POST | `/exports` | שמירת שיחה לתיקיית ההורדות |

שגיאות מוחזרות בפורמט אחיד: `{"error": {"code": "missing_secret", "message": "..."}}`.

## פיתוח

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy                     # strict
uv run pytest                   # בדיקות יחידה (מהירות, ללא רשת)
uv run pytest -m integration    # uv + langgraph dev אמיתיים עם examples/foo-agent
```

## מבנה הקוד

| מודול | אחריות |
|---|---|
| `secret_store.py` | OS keychain (keyring), טווח גלובלי/אייג'נט |
| `openrouter.py` · `usage.py` | בדיקת מפתח ומחירים · ספר עלויות |
| `files.py` | העלאות, ייצוא, README |
| `lifecycle.py` · `orphans.py` · `winjob.py` | עצירה כשהאפליקציה נסגרת · PID registry · Job Object ב-Windows |
| `registry.py` | פענוח ואימות הקטלוג, לקוח עם מטמון ETag ו-fallback offline |
| `download.py` | הורדת https עם רשימת hosts, בדיקת כל redirect, מגבלת גודל, sha256 |
| `registry_validate.py` | בדיקת שינוי בקטלוג ל-CI (immutability, הורדה, השוואת מניפסטים, התקנה) |
| `archive.py` | sha256, חילוץ zip בטוח (Zip-Slip, symlinks, zip bomb, דילוג על `.env`) |
| `manifest.py` | טעינה ואימות מול JSON Schema |
| `layout.py` | מבנה תיקיית אייג'נט ויצירת `langgraph.json` |
| `envbuilder.py` | יצירת venv והתקנת תלויות עם uv |
| `installer.py` | שלבי ההתקנה + rollback + ניקוי שאריות |
| `supervisor.py` | הפעלה, בדיקת בריאות ועצירה של שרתי האייג'נטים |
| `store.py` | רישום האייג'נטים המותקנים (SQLite) |
| `jobs.py` | הרצת התקנות ברקע ושמירת אירועי התקדמות |
| `service.py` | `AgentManager` – נקודת כניסה אחת ל-API ול-CLI |
| `api.py` / `cli.py` | שכבות הממשק |
