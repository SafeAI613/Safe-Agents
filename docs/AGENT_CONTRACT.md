# חוזה אייג'נט (Agent Package Contract) – גרסה 1

מסמך זה מגדיר מה אייג'נט חייב לקיים כדי שיהיה ניתן להתקין ולהריץ אותו בפלטפורמת Safe-Agents Desktop.
המפתח אחראי על הלוגיקה והכלים בלבד. הפלטפורמה אחראית על ההתקנה, הסביבה, השרת והממשק.

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
| `source_url` | כן | קישור ל-zip של tag (לא ענף!) |
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

### קבצים
הממשק מעתיק קבצים שנגררו לתיקייה `inbox/` של האייג'נט ושולח את הנתיב המוחלט בתוך הודעת המשתמש – זהה למצב CLI.

### עלויות
אין צורך לחשב עלויות באייג'נט. הפלטפורמה מחשבת מ-`usage_metadata` של הודעות ה-AI ומטבלת התמחור של OpenRouter.

## 4. פרסום בקטלוג
1. יצירת tag (למשל `v1.0.0`) בריפו של האייג'נט.
2. חישוב `sha256` של ה-zip של ה-tag (ה-hash נכתב ב-`registry.json` בלבד – לא במניפסט, כי המניפסט נמצא בתוך ה-zip עצמו).
3. פתיחת PR לריפו הקטלוג עם רשומה חדשה ב-`registry.json`.
4. ה-CI מאמת את המניפסט, ה-hash, ההתקנה וטעינת הגרף. מנהל מאשר merge.
