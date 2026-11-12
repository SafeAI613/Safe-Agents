# Safe-Agents Desktop – מסמך אפיון ותכנון

## Context
מפתחים בונים אייג'נטים עצמאיים לפי מסמך ההנחיות (LangGraph + OpenRouter, מבנה `agent/graph.py`, `manifest.json` בשורש).
המטרה: אפליקציית Desktop שבה משתמש קצה **מעיין בקטלוג → מוריד → מתקין → מפעיל** כל אייג'נט, בלי לגעת בקוד, ב-Python או בטרמינל.
מסמך זה הוא הבסיס לפיתוח בריפו `Safe-Agents`.

החלטות ארכיטקטורה שנסגרו:
| נושא | החלטה |
|---|---|
| מקור אייג'נטים | קטלוג מרכזי – ריפו GitHub עם `registry.json`, הוספה ב-PR + CI ולידציה + אישור מנהל |
| תלויות | venv מבודד **ושרת langgraph נפרד לכל אייג'נט** (פורט משלו) |
| Desktop | Tauri 2 + React (TypeScript) + Python sidecar (FastAPI) |
| Python | מגיע עם המערכת: `uv` מצורף בבינארי, מוריד Python 3.12 מבודד בהפעלה ראשונה |
| מפתח OpenRouter | המשתמש מזין פעם אחת; נשמר ב-OS keychain; מוזרק כ-env לכל אייג'נט |
| UI אייג'נט | UI גנרי אחיד (צ'אט, סטרימינג, HITL, קבצים, עלויות) המונע ע"י ה-manifest |

> הערה טכנית: בגלל ההחלטה "שרת לכל אייג'נט", לא מעדכנים `langgraph.json` אחד משותף – המערכת **מייצרת `langgraph.json` לכל אייג'נט** בתיקייתו ומרימה עבורו `langgraph dev`. הקליינט ניגש עם `useStream` (חבילת `@langchain/react`, לשעבר `useStream` מ-`@langchain/langgraph-sdk/react`) לכתובת `http://127.0.0.1:<port>` של האייג'נט.

---

## 1. ארכיטקטורה

```
┌────────────────────────── Tauri App ───────────────────────────┐
│  React UI (Vite + TS)                                          │
│   ├─ Store (קטלוג)  ├─ My Agents  ├─ Chat (useStream)  ├─ Settings │
│        │ HTTP + SSE (localhost, token) – גם הצ'אט   │
│        ▼                                                        │
│  Rust core (Tauri)                    langgraph dev :2101       │
│   ├─ token + סטטוס ל-UI                langgraph dev :2102 ...  │
│   └─ spawn/kill sidecar                   ▲  (token של המנהל בלבד)│
│        ▼                                  │ spawn + proxy        │
│  Agent Manager (Python FastAPI sidecar, PyInstaller) ───────────┘
│   ├─ Registry + policy client  ├─ Installer (download/verify/venv)│
│   ├─ Process supervisor (ports, health, logs)                    │
│   ├─ Content guard: proxy לכל אייג'נט + בדיקת תחום ומדיניות       │
│   └─ Usage/Cost ledger (SQLite)                                  │
└────────────────────────────────────────────────────────────────┘
          │ HTTPS                                   │ HTTPS
          ▼                                         ▼
  GitHub: registry.json + policy.json        openrouter.ai / LangSmith
          + releases
```

### שומר התוכן (Content guard)
הממשק לא מדבר ישירות עם שרתי האייג'נטים. המנהל משמש reverse proxy ל-API של LangGraph
(`/agents/{id}/lg/...`), ומפתח הגישה לשרת האייג'נט לא יוצא ממנו.

| מה עובר | מה נבדק | מה קורה בחסימה |
|---|---|---|
| הודעת משתמש (`run.start`) | התאמה לתפקיד האייג'נט (מהמניפסט) + `policy.json` | ההודעה לא נשלחת לאייג'נט; הממשק מציג למה |
| טקסט של האייג'נט (סטרימינג) | `policy.json` | ההודעה מוחזקת עד שהיא שלמה ונבדקה; טקסט חסום מוחלף בהודעה קבועה |
| `values`, בקשות אישור, מצב שיחה, היסטוריה | `policy.json` | אותו דבר – גם בפתיחה מחדש של שיחה |

- מודל בדיקה קבוע אחד (`classifier_model`), טמפרטורה 0. תוצאות נשמרות לפי hash של הטקסט והכללים.
- **Fail closed:** אם הבדיקה לא זמינה (אין רשת, אין קרדיט) – ההודעה לא נשלחת והתשובה לא מוצגת.
- `policy.json` נמשך מריפו הקטלוג כמו הקטלוג (ETag, עותק שמור), ועם ברירת מחדל מובנית.
- בקטלוג: `scope-gate` שולח לאייג'נט שאלות מחוץ לתחום ודורש שיעור סירוב מינימלי.

**עקרון:** Rust מינימלי (חלון, הרמת sidecar, token). המפתחות נשמרים ב-OS keychain דרך ספריית `keyring` של Python בתוך ה-Manager. כל הלוגיקה ב-Python (Agent Manager) – שפה מוכרת לסטודנטים, ו-`uv` קל לשליטה ממנה.

### תיקיות נתונים במחשב המשתמש (`%APPDATA%/SafeAgents` / `~/.local/share/SafeAgents`)
```
SafeAgents/
├─ runtime/python/            # Python מבודד שהותקן ע"י uv
├─ agents/<agent_id>/<version>/
│   ├─ src/                    # קוד האייג'נט שחולץ
│   ├─ .venv/                  # סביבה מבודדת
│   ├─ langgraph.json          # נוצר אוטומטית
│   └─ .langgraph_api/         # state של השרת (threads, checkpoints)
├─ db/manager.sqlite           # installed, ports, usage
└─ logs/<agent_id>.log
```

---

## 2. חוזה האייג'נט (Agent Package Contract)

המפתח לא כותב UI ולא קובץ langgraph.json. הוא חייב רק:
1. מבנה התיקיות מהמסמך (`agent/graph.py` וכו').
2. `requirements.txt` (או `pyproject.toml`) – **חייב** לכלול `langgraph`.
3. `manifest.json` לפי נספח 1 **+ הרחבת `runtime`** (חובה לפלטפורמה):

```json
"runtime": {
  "manifest_version": 1,
  "graph_entry": "agent/graph.py:graph",
  "graph_id": "foo_agent",
  "python": ">=3.11,<3.13",
  "source_url": "https://github.com/foo/bar/releases/download/v1.0.0/foo-agent.zip",
  "input_schema": "messages",
  "accepts_files": [".pdf", ".txt"],
  "hitl": true,
  "env_required": ["OPENROUTER_API_KEY"],
  "env_optional": ["LANGSMITH_API_KEY", "TAVILY_API_KEY"],
  "mcp_servers": [{ "name": "foo", "auth": [{"key": "FOO_TOKEN", "label": "Token", "secret": true}] }],
  "default_model": "meta-llama/llama-3.1-70b-instruct"
}
```

**מוסכמות ריצה שהאייג'נט חייב לכבד (מתועד ב-`docs/AGENT_CONTRACT.md`):**
- קריאת מפתח מ-`os.environ["OPENROUTER_API_KEY"]` בלבד, ומודל מ-`configurable.model` (ברירת מחדל `default_model`).
- State עם `messages` (MessagesState) כדי שה-UI הגנרי יעבוד.
- HITL דרך `interrupt()` עם payload בפורמט אחיד: `{"type":"approval","action":"…","details":…}` → UI מציג כרטיס אישור, resume עם `{"approved": bool, "edited": …}`.
- קבצים: ה-UI מעתיק קובץ לתיקיית `inbox/` של האייג'נט ושולח נתיב מוחלט בהודעה (תואם "מתן נתיב" מה-CLI).
- עלויות: האייג'נט לא מחשב – המערכת מחשבת מ-`usage_metadata` בהודעות AI ומתמחור OpenRouter (`/api/v1/models`).

---

## 3. קטלוג מרכזי (ריפו `safe-agents-registry`)
- `registry.json`: מערך רשומות `{id, name, description, creator_name?, latest, versions:[{version, manifest_url, source_url, sha256}]}` (סכמה: `schemas/registry.schema.json`).
- **קבצי האייג'נט הם Release assets** – לא ה-zip ש-GitHub מייצר מ-tag, שאינו מובטח להישאר זהה ברמת הבייט.
- מפתח מוסיף אייג'נט ב-PR. ה-CI (`safe-agents-manager validate-registry --install`) בודק: סכמה; שגרסה שפורסמה לא השתנתה (immutability); הורדה + sha256; שהמניפסט בתוך ה-zip זהה ל-`manifest_url` ולרשומה; התקנה מלאה + smoke test. רץ על `pull_request` עם הרשאות קריאה בלבד וללא secrets.
- מנהל מאשר merge (CODEOWNERS + branch protection). האפליקציה מושכת את `registry.json` (raw) עם ETag cache; כשאין רשת – מוצג העותק האחרון (`stale`).
- הורדות מותרות רק ב-https ורק מ-hosts של GitHub (`github.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`); כל redirect נבדק.

## 4. תהליכים מרכזיים

### 4.1 Bootstrap (הפעלה ראשונה)
1. Tauri מרים את ה-sidecar (Agent Manager) על פורט אקראי + token אקראי (מועבר ל-UI בלבד).
2. Manager בודק `runtime/python`; אם חסר – `uv python install 3.12 --install-dir …` עם פס התקדמות.
3. אשף: הזנת מפתח OpenRouter → ולידציה מול `GET /api/v1/key` → שמירה ב-keychain (ספריית `keyring` של Python).

### 4.2 התקנת אייג'נט (state machine)
`queued → downloading → verifying(sha256) → extracting → validating(manifest) → creating_venv → installing_deps → generating_config → smoke_test → installed` (כל שלב עם rollback לתיקייה זמנית ומחיקה בכשל).
- `uv venv --python <runtime>` → `uv pip install -r requirements.txt "langgraph-cli[inmem]"`.
- יצירת `langgraph.json`:
  ```json
  {"dependencies": ["./src"], "graphs": {"<graph_id>": "./src/<graph_entry>"}, "env": {}}
  ```
- Smoke test: הרמת שרת, `GET /ok`, `GET /assistants/search` שמכיל את ה-graph_id, כיבוי.
- התקדמות נשלחת ל-UI ב-SSE.

### 4.3 הפעלה
- Supervisor מקצה פורט פנוי מטווח (2100–2199), מריץ:
  `.venv/bin/langgraph dev --host 127.0.0.1 --port <p> --no-browser --no-reload --config langgraph.json`
  עם env מוזרק: `OPENROUTER_API_KEY`, מפתחות MCP/אופציונליים מה-keychain, `LANGSMITH_*` אם הוגדר.
- Health-check עד `/ok` → מצב `running`. Lazy start (בכניסה לצ'אט) + כיבוי אחרי X דקות חוסר פעילות + כיבוי כולל בסגירת האפליקציה (Job Object ב-Windows כדי שלא יישארו תהליכים יתומים).

### 4.4 צ'אט (UI גנרי)
```tsx
const stream = useStream({ apiUrl: `http://127.0.0.1:${port}`, assistantId: graphId,
  threadId, onThreadId: setThreadId });
// stream.messages, stream.interrupt → <ApprovalCard/>, stream.submit(null,{command:{resume}})
```
- סטרימינג שלבי נודים (`streamMode: ["values","updates","messages"]`) → פס "Entering Node: …".
- פאנל צד: MCPs פעילים + הזנת אוטנטיקציה (לפי `mcp_servers`), עלות/טוקנים לריצה ולשיחה, כפתור עזרה (מתוך `examples_of_suitable_questions` + README), ייצוא תקציר שיחה.
- הודעות שגיאה ממופות לקטגוריות (מפתח לא תקין 401, אין קרדיט 402, rate limit 429, קריסת שרת אייג'נט → הצגת סוף הלוג + "הפעל מחדש").

### 4.5 עדכון / הסרה
- בדיקת `latest` בקטלוג → התקנה צד-לצד לתיקיית גרסה חדשה → החלפה אטומית → מחיקת ישנה (rollback אפשרי).
- הסרה: עצירת תהליך, מחיקת תיקייה, מחיקת סודות ייעודיים מה-keychain.

---

## 5. API של Agent Manager (FastAPI, localhost בלבד, Bearer token)
| Method | Path | תיאור |
|---|---|---|
| GET | `/registry?refresh=` | קטלוג |
| GET | `/agents` | מותקנים + סטטוס |
| POST | `/agents/{id}/install` · `/update` | התחלת job |
| GET | `/jobs/{job_id}/events` | SSE התקדמות |
| DELETE | `/agents/{id}` | הסרה |
| POST | `/agents/{id}/start` · `/stop` | ← `{graph_id, proxy_path}` |
| * | `/agents/{id}/lg/...` | ה-API של LangGraph דרך שומר התוכן (הצ'אט) |
| GET | `/policy` | מדיניות התוכן הפעילה ומקורה |
| GET | `/agents/{id}/logs?tail=` | לוגים |
| PUT | `/agents/{id}/secrets` | סודות MCP/אופציונליים (נשמרים ב-OS keychain) |
| POST | `/usage` · GET `/usage?agent=` | רישום ושליפת עלויות |
| GET | `/models/pricing` | תמחור OpenRouter (cache 24h) |

---

## 6. אבטחה (סטנדרט תעשייה – חובה)
- **קוד אייג'נט הוא קוד לא-מהימן**: אישור התקנה מציג `required_permissions` (כמו הרשאות אפליקציה במובייל); התקנה רק מהקטלוג המאושר; אימות sha256; אין `shell=True`.
- כל השרתים קשורים ל-`127.0.0.1` בלבד; ה-Manager דורש token; CSP של Tauri מאפשר `connect-src` רק ל-localhost ול-openrouter/langsmith.
- טלמטריה של `langgraph-cli` מכובה (`LANGGRAPH_CLI_NO_ANALYTICS=1`) – אין שליחת נתוני שימוש ללא הסכמה.
- סודות אך ורק ב-OS keychain, לעולם לא בקבצי `.env` על הדיסק, לא בלוגים (masking).
- הגנת Zip-Slip בחילוץ, מגבלת גודל הורדה.
- שלב עתידי (מחוץ ל-MVP): הרצה תחת משתמש מוגבל / sandbox.

---

## 7. מבנה הריפו `Safe-Agents`
```
Safe-Agents/
├─ docs/  SPEC.md · AGENT_CONTRACT.md · ARCHITECTURE.md (+ דיאגרמות)
├─ schemas/manifest.schema.json · registry.schema.json
├─ desktop/            # Tauri 2
│   ├─ src-tauri/      # Rust: main.rs, lib.rs, manager.rs (sidecar)
│   └─ src/            # React: pages/Store, MyAgents, Chat, Settings; hooks/useAgent.ts
├─ manager/            # Python FastAPI sidecar (uv project)
│   ├─ app/ api.py · registry.py · installer.py · supervisor.py · ports.py · usage.py · db.py
│   └─ tests/
├─ registry-template/  # registry.json + .github/workflows/validate.yml
├─ examples/foo-agent/ # אייג'נט דוגמה מינימלי עם interrupt, לבדיקות E2E
└─ .github/workflows/  # CI: lint, tests, build installers (Windows .msi)
```

סטנדרטים: TypeScript strict + ESLint + Prettier; Python ruff + mypy + pytest; Rust clippy; Conventional Commits; טסטים לכל שלב ב-state machine של ההתקנה.

---

## 8. שלבי פיתוח (Milestones)
| # | שלב | תוצר |
|---|---|---|
| M0 | מסמכים וחוזים | `SPEC.md`, `AGENT_CONTRACT.md`, JSON Schemas, אייג'נט דוגמה |
| M1 | Manager headless | התקנה מ-zip מקומי, venv, langgraph.json, start/stop, טסטים |
| M2 | Registry | ריפו קטלוג + CI ולידציה, הורדה + sha256 |
| M3 | Desktop shell | Tauri + sidecar + keychain + אשף מפתח ✅ |
| M4 | Store + My Agents | התקנה עם פס התקדמות, עדכון, הסרה ✅ |
| M5 | Chat גנרי | useStream, סטטוס ריצה, HITL, קבצים, שגיאות ✅ |
| M6 | עלויות ו-MCP | ledger, פאנל MCP, ייצוא שיחה ✅ |
| M7 | הקשחה והפצה | Job Objects, PID registry, idle-shutdown, sidecar, Release workflow ✅ (חתימת קוד – דורשת תעודה) |

---

---

## 10. החלטות שהשתנו במהלך המימוש

| נושא | בתכנון | בפועל | למה |
|---|---|---|---|
| שמירת מפתחות | Rust `keyring` | Python `keyring` בתוך ה-Manager | כל הלוגיקה במקום אחד; Rust נשאר מינימלי |
| `sha256` | בתוך `manifest.json` | רק ב-`registry.json` | המניפסט נמצא בתוך ה-zip שה-hash מחושב עליו |
| מקור ה-zip | "Download ZIP" של tag | Release asset בלבד | ה-zip האוטומטי של GitHub אינו מובטח להישאר זהה ברמת הבייט |
| אבטחת שרת האייג'נט | localhost בלבד | + token לכל הפעלה (`platform_auth.py`) + CORS מוגבל | ברירת המחדל של `langgraph dev` היא `CORS *` ללא אימות – כל דף אינטרנט יכל להפעיל את האייג'נט |
| תצוגת "באיזה נוד" | שם הנוד בזמן אמת | סטטוס ריצה: עובד / מנסח תשובה / מפעיל כלי X / ממתין לאישור | בריצות פרוטוקול v2, `langgraph-api 0.16` שולח רק `lifecycle`, `messages`, `values`, `input` (נבדק מול השרת). כשהשרת ישלח `tasks`, אפשר להוסיף שמות נודים |
| ניקוי תהליכים | Job Object (Windows) | + PID registry (macOS/Linux) + עצירה כש-stdin נסגר או כשהאפליקציה נעלמת | Manager שנהרג ב-SIGKILL לא יכול לנקות; המנהל הבא עוצר את מה שנשאר |
| טלמטריה | – | `LANGGRAPH_CLI_NO_ANALYTICS=1` | `langgraph dev` שולח נתוני שימוש כברירת מחדל |
| LangSmith | מפתח לכל אייג'נט | מפתח גלובלי אחד, opt-in, עם הסבר פרטיות | ניטור הוא יכולת פלטפורמה; התוכן נשלח לשירות חיצוני רק בהסכמה |
| עצירת אייג'נט | SIGTERM / CTRL_BREAK | `platform_launcher.py`: ‏`shutdown` ב-stdin או סגירת stdin ← כיבוי מסודר של השרת | ב-Windows אין console משותף ולכן CTRL_BREAK לא מגיע; כיבוי בכוח מאבד עד 10 שניות של שיחה |
| חלונות console ב-Windows | – | `CREATE_NO_WINDOW` לכל תהליך-בן | אחרת כל אייג'נט ופקודת uv פותחים חלון שחור |
| היסטוריית שיחות | לא נדרש | רשימת שיחות לכל אייג'נט; המצב מועתק לגרסה החדשה בעדכון | שימוש אמיתי דורש לחזור לשיחות; `langgraph dev` שומר בתיקיית העבודה |
| התקנה מקובץ | – | ZIP מהאפליקציה, עם אזהרה | בדיקה עצמית למפתחים והפצה פנימית |
| כלי מפתח | – | `new-agent` · `check` · `package` | הדרך הקצרה מאייג'נט לפרסום בקטלוג, עם zip שחוזר על אותו hash |
| הגבלת תוכן | באחריות כל אייג'נט | שומר תוכן בפלטפורמה (proxy) + `policy.json` בקטלוג + `scope-gate` | אי אפשר לסמוך על קוד של מפתחים: הפלטפורמה צריכה לאכוף תחום ומדיניות בעצמה |
| סטרימינג טקסט | מילה אחרי מילה | כל הודעה מוצגת אחרי שנבדקה | אסור שטקסט חסום יופיע ולו לרגע; שלבי הריצה עדיין מוצגים בזמן אמת |

