# חוזה אייג'נט (Agent Package Contract) – גרסה 1

מסמך זה מגדיר מה אייג'נט חייב לקיים כדי שיהיה ניתן להתקין ולהריץ אותו בפלטפורמת Safe-Agents Desktop.
המפתח אחראי על הלוגיקה והכלים בלבד. הפלטפורמה אחראית על ההתקנה, הסביבה, השרת והממשק.

## 0. התחלה מהירה (כלי המפתח)

```bash
uv tool install "safe-agents-manager @ git+https://github.com/SafeAI613/Safe-Agents#subdirectory=manager"

safe-agents-manager new-agent my-agent --id my_agent --name "My Agent"   # שלד לפי החוזה, כולל CLI עם HITL
safe-agents-manager check my-agent             # בדיקת מניפסט, מבנה, מפתחות שנשכחו בקוד
safe-agents-manager check my-agent --install   # + התקנה אמיתית והפעלת השרת פעם אחת
OPENROUTER_API_KEY=... safe-agents-manager check my-agent --scope --report evals/results/scope.csv
                                               # + האם האייג'נט מסרב לשאלות מחוץ לתחום (כמו בקטלוג)
safe-agents-manager package my-agent           # zip נקי וזהה בכל הרצה + sha256 + רשומה לקטלוג
```

השלד כולל `agent/cli.py` שמדפיס `[INFO] Entering Node: ...` ומבקש אישור `[Y/N]` – בסיס למסלול ה-CLI של הקורס.

## 1. מבנה תיקיות חובה

```
my-llm-agent/
├── agent/
│   ├── __init__.py
│   ├── graph.py        # חושף משתנה graph = builder.compile()
│   ├── nodes.py
│   ├── tools.py
│   └── cli.py
├── evals/
├── manifest.json       # כולל את בלוק runtime (סעיף 2)
├── requirements.txt    # חייב לכלול langgraph
├── README.md
└── .env.example
```

- **אין** לצרף `langgraph.json` – הפלטפורמה מייצרת אותו.
- **אין** לצרף `.env` אמיתי או מפתחות כלשהם.
- אין לבצע `compile()` עם checkpointer מותאם – שרת LangGraph מספק checkpointer בעצמו.

## 2. בלוק `runtime` ב-manifest.json

בנוסף לשדות נספח 1, יש להוסיף:

| שדה | חובה | תיאור |
|---|---|---|
| `manifest_version` | כן | כרגע `1` |
| `graph_id` | כן | מזהה ייחודי, `snake_case`, משמש כ-`assistantId` |
| `graph_entry` | כן | `נתיב/לקובץ.py:שם_משתנה`, לדוגמה `agent/graph.py:graph` |
| `python` | כן | טווח גרסאות, לדוגמה `>=3.11,<3.13` |
| `source_url` | כן | קישור לקובץ zip שהועלה כ-**Release asset** (ראו סעיף 4) |
| `input_schema` | כן | כרגע רק `messages` |
| `accepts_files` | לא | סיומות קבצים נתמכות |
| `hitl` | לא | `true` אם הגרף משתמש ב-`interrupt()` |
| `env_required` / `env_optional` | לא | שמות משתני סביבה. `OPENROUTER_API_KEY` תמיד מסופק |
| `mcp_servers` | לא | רשימת MCP ושדות האוטנטיקציה שלהם |
| `default_model` | כן | מזהה מודל ב-OpenRouter |

הסכמה המלאה: [`schemas/manifest.schema.json`](../schemas/manifest.schema.json).

## 3. מוסכמות ריצה

### מפתח ומודל
```python
import os
from langchain_openai import ChatOpenAI

def get_llm(config):
    model = config.get("configurable", {}).get("model", "foo/bar-model")
    return ChatOpenAI(
        model=model,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
```

**חשוב:** אין לקרוא מפתחות או ליצור לקוח LLM ברמת המודול (בזמן `import`). הפלטפורמה טוענת את הגרף בבדיקת ההתקנה (smoke test) בלי מפתחות – יצירת הלקוח צריכה להתבצע בתוך הנוד, כמו בדוגמה.

### סביבת הריצה
האייג'נט מקבל סביבה מינימלית: משתני מערכת בסיסיים (PATH, TEMP וכו') ורק המפתחות שהוצהרו ב-`env_required` / `env_optional` / `mcp_servers`. משתנה שלא הוצהר – לא יגיע לאייג'נט.

### State
ה-State חייב להכיל `messages` (למשל `MessagesState`), כדי שהממשק הגנרי יציג את השיחה.

### HITL
```python
from langgraph.types import interrupt

decision = interrupt({
    "type": "approval",
    "action": "lorem_action",
    "details": {"foo": "bar"},
})
if not decision.get("approved"):
    ...
```
הממשק מחזיר `{"approved": bool, "edited": <ערך אופציונלי>}`.

### מה המשתמש רואה בזמן ריצה
הממשק מציג אוטומטית את מצב הריצה: "האייג'נט עובד", "מנסח תשובה" (בזמן סטרימינג של הטקסט), "מפעיל כלי: X" (לפי tool calls) ו"ממתין לאישור" (כש-`interrupt()` פעיל). אין צורך בקוד מיוחד באייג'נט – רק להשתמש ב-tools וב-`interrupt()` כרגיל.

### תחום ומדיניות תוכן
הפלטפורמה אוכפת שני כללים על כל אייג'נט, בלי קשר לקוד שלו:

1. **רק התפקיד של האייג'נט.** הודעת משתמש שאינה קשורה לתפקיד לא מגיעה לאייג'נט בכלל. התפקיד נקבע
   לפי השדות במניפסט: `description`, `tasks_capable_of_performing`, `professional_fields`,
   `examples_of_suitable_questions` ו-`target_audience`. לכן כדאי לכתוב אותם מדויק ומלא: תיאור צר מדי
   יגרום לחסימת שאלות לגיטימיות, ותיאור רחב מדי יאפשר שיחה כללית.
2. **מדיניות התוכן של הפלטפורמה** (`policy.json` בריפו הקטלוג): נושאים שאסורים לכל אייג'נט. הודעה או
   תשובה שנוגעות בהם נחסמות.

הבדיקה נעשית במודל קבוע דרך OpenRouter, לפני שההודעה מגיעה לאייג'נט ולפני שהתשובה מוצגת. העלות שלה
נכללת בעלות השיחה.

**מה האייג'נט צריך לעשות בעצמו:** לסרב בנימוס לשאלות מחוץ לתחום (GuardRail בגרף, כמו שהקורס דורש).
לפני כניסה לקטלוג הפלטפורמה שולחת לאייג'נט שאלות מחוץ לתחום ובודקת שהוא מסרב לרובן
(`check --scope`). התוצאה נשמרת כ-CSV, באותו פורמט של eval המקצועיות ואי-הזליגה.

**שימו לב:** בזמן ריצה התשובה מוצגת רק אחרי שהיא נבדקה. לכן הטקסט מופיע בבת אחת בסוף כל הודעה,
ולא מילה אחרי מילה. שלבי הריצה ("מנסח תשובה", "מפעיל כלי") עדיין מוצגים בזמן אמת.

### קבצים
הממשק מעתיק קבצים שנגררו לתיקייה `inbox/` של האייג'נט ושולח את הנתיב המוחלט בתוך הודעת המשתמש – זהה למצב CLI.

### עלויות
אין צורך לחשב עלויות באייג'נט. הפלטפורמה מחשבת מ-`usage_metadata` של הודעות ה-AI ומטבלת התמחור של OpenRouter.
כדי שהמודל יחזיר את מספר הטוקנים גם בסטרימינג, יש ליצור את הלקוח עם `stream_usage=True` (ראו `examples/foo-agent/agent/nodes.py`).

## 4. פרסום בקטלוג

### למה Release asset ולא "Download ZIP"
קבצי ה-zip ש-GitHub מייצר אוטומטית מ-tag (`archive/refs/tags/...`) **אינם מובטחים להישאר זהים ברמת הבייט**, ולכן ה-sha256 שלהם עלול להשתנות ולשבור את ההתקנה. קובץ שהועלה ל-Release הוא קובץ קבוע. לכן הפלטפורמה מורידה רק מ-Releases (הורדה מ-`codeload.github.com` נחסמת).

### שלבים
1. **אריזה** – zip של תיקיית האייג'נט (בלי `.venv`, `__pycache__`, `.env`):
   ```bash
   git archive --format=zip --prefix=foo-agent/ -o foo-agent.zip v1.0.0
   ```
2. **Release** – יצירת Release בשם התג (`v1.0.0`) והעלאת `foo-agent.zip` כ-asset.
   הקישור: `https://github.com/<user>/<repo>/releases/download/v1.0.0/foo-agent.zip` – זה ה-`source_url` במניפסט.
3. **Hash** – חישוב sha256 של הקובץ שהועלה:
   - Linux/macOS: `sha256sum foo-agent.zip`
   - Windows (PowerShell): `(Get-FileHash foo-agent.zip -Algorithm SHA256).Hash.ToLower()`

   ה-hash נכתב **רק** ב-`registry.json` – לא במניפסט, כי המניפסט נמצא בתוך ה-zip עצמו.
4. **PR לקטלוג** – הוספת רשומה ל-`registry.json` בריפו הקטלוג (ראו README של הקטלוג).
   `manifest_url` מצביע על המניפסט בתג: `https://raw.githubusercontent.com/<user>/<repo>/v1.0.0/manifest.json`.
5. **CI** – בודק סכמה, שה-hash תואם, שהמניפסט בתוך ה-zip זהה ל-`manifest_url` ולרשומה בקטלוג, ומתקין את האייג'נט בפועל (כולל טעינת הגרף). מנהל מאשר merge.

### כללים
- **גרסה שפורסמה לא משתנה לעולם.** תיקון = גרסה חדשה (`1.0.1`). ה-CI חוסם שינוי של גרסה קיימת.
- `name`, `description` ו-`version` ברשומה חייבים להיות זהים למניפסט.
