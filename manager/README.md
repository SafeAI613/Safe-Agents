# Safe-Agents Manager (M1)

תהליך Python ללא ממשק (headless) שמתקין, מריץ ומנהל אייג'נטים של LangGraph.
בהמשך (M3) אפליקציית ה-Desktop (Tauri) תריץ אותו כ-sidecar ותדבר איתו דרך ה-API המקומי.

## מה הוא עושה

1. **התקנה** מקובץ zip: אימות sha256 → חילוץ בטוח → בדיקת `manifest.json` → יצירת סביבת Python
   מבודדת עם `uv` → יצירת `langgraph.json` → בדיקת הפעלה (smoke test) → רישום ב-SQLite.
   כל כשל מבטל את ההתקנה ומוחק את כל מה שנוצר.
2. **הרצה**: שרת `langgraph dev` נפרד לכל אייג'נט, על פורט פנוי מהטווח 2100–2199,
   קשור ל-`127.0.0.1` בלבד, עם סביבה מינימלית ורק המפתחות שהאייג'נט הצהיר עליהם.
3. **API מקומי** (FastAPI) עם Bearer token, כולל סטרימינג של התקדמות ההתקנה (SSE).

## דרישות

- [uv](https://docs.astral.sh/uv/) (Python לאייג'נטים מורד אוטומטית ע"י uv לתוך תיקיית הנתונים).

## שימוש ב-CLI

```bash
cd manager
uv sync

uv run safe-agents-manager install ../path/to/agent.zip [--sha256 <hex>]
uv run safe-agents-manager list
OPENROUTER_API_KEY=... uv run safe-agents-manager run foo_agent   # Ctrl+C לעצירה
uv run safe-agents-manager uninstall foo_agent
SAFE_AGENTS_TOKEN=... uv run safe-agents-manager serve --port 0
```

`serve` מדפיס שורת JSON אחת כשהוא מוכן: `{"event": "ready", "port": 54321}`
(ואם לא הועבר `SAFE_AGENTS_TOKEN` – גם `token` שנוצר אקראית).

| משתנה סביבה | תפקיד |
|---|---|
| `SAFE_AGENTS_HOME` | תיקיית הנתונים (ברירת מחדל: תיקיית הנתונים של מערכת ההפעלה) |
| `SAFE_AGENTS_UV` | נתיב ל-uv (ברירת מחדל: ליד קובץ ההפעלה, ואז PATH) |
| `SAFE_AGENTS_TOKEN` | הטוקן שה-API דורש |
| `OPENROUTER_API_KEY` וכו' | ב-M1 מפתחות נקראים מהסביבה; ב-M3 יגיעו מה-keychain |

## API

| Method | Path | תיאור |
|---|---|---|
| GET | `/health` | ללא טוקן |
| GET | `/agents` · `/agents/{id}` | רשימה / פרטים + סטטוס + url |
| POST | `/agents/install` | `{"archive_path", "sha256"?}` ← 202 + job |
| GET | `/jobs/{id}` · `/jobs/{id}/events` | סטטוס / SSE (`progress`, `done`) |
| POST | `/agents/{id}/start` · `/stop` | הפעלה (← `url`, `graph_id`) / עצירה |
| GET | `/agents/{id}/logs?tail=200` | סוף הלוג |
| DELETE | `/agents/{id}` | הסרה |

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
