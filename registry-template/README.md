# Safe-Agents Registry

הקטלוג המרכזי של אייג'נטים לפלטפורמת Safe-Agents Desktop.
האפליקציה קוראת מהענף `main` שני קבצים:

| קובץ | מה הוא קובע |
|---|---|
| `registry.json` | אילו אייג'נטים מוצגים בחנות וניתנים להתקנה |
| `policy.json` | מדיניות התוכן של כל האייג'נטים: נושאים אסורים, מודל הבדיקה ושאלות הבדיקה לקטלוג |

> תיקייה זו היא **תבנית**. יש להעתיק אותה לריפו חדש בשם `safe-agents-registry`,
> ולהגדיר בו Branch protection (ראו למטה).

## הוספת אייג'נט / גרסה

1. פרסמו את האייג'נט לפי [חוזה האייג'נט – סעיף 4](https://github.com/SafeAI613/Safe-Agents/blob/main/docs/AGENT_CONTRACT.md#4-פרסום-בקטלוג).
2. פתחו PR שמוסיף רשומה (או גרסה לרשומה קיימת) ב-`registry.json`:

```json
{
  "registry_version": 1,
  "agents": [
    {
      "id": "foo_agent",
      "name": "Foo Agent",
      "description": "Lorem ipsum dolor sit amet.",
      "creator_name": "foo",
      "latest": "1.0.0",
      "versions": [
        {
          "version": "1.0.0",
          "manifest_url": "https://raw.githubusercontent.com/foo/foo-agent/v1.0.0/manifest.json",
          "source_url": "https://github.com/foo/foo-agent/releases/download/v1.0.0/foo-agent.zip",
          "sha256": "<64 תווי hex>"
        }
      ]
    }
  ]
}
```

3. ה-CI ירוץ אוטומטית. אם יש בעיה – היא תופיע כהערה על ה-PR.
4. מנהל/ת הפלטפורמה בודק/ת ומאשר/ת merge.

## מדיניות התוכן (`policy.json`)

כל הודעה של משתמש נבדקת באפליקציה לפני שהיא מגיעה לאייג'נט: היא צריכה להתאים לתפקיד של האייג'נט
(לפי `description`, `tasks_capable_of_performing`, `professional_fields` ו-`examples_of_suitable_questions`
במניפסט שלו) ולא לגעת באף נושא אסור. כל תשובה של אייג'נט נבדקת לפני שהיא מוצגת. תוכן חסום לא מוצג,
גם לא כשפותחים שיחה קודמת.

| שדה | תפקיד |
|---|---|
| `blocked_topics` | נושאים שאסורים לכל אייג'נט. תיאור קצר וברור לכל נושא (באנגלית – כך המודל הבודק מבין אותם הכי טוב) |
| `classifier_model` | מודל OpenRouter אחד וקבוע שבודק את כל ההודעות. נבחר מודל זול ומהיר |
| `scope_probes` | שאלות מחוץ לתחום ששולחים לאייג'נט לפני כניסה לקטלוג (לפחות 5) |
| `min_refusal_rate` | אחוז השאלות מחוץ לתחום שהאייג'נט חייב לסרב להן כדי להיכנס לקטלוג |

שינוי במדיניות נעשה ב-PR, כמו כל שינוי אחר. האפליקציה מושכת את הגרסה החדשה תוך כמה דקות. אם הקובץ
פגום – ה-CI נכשל, והאפליקציה ממשיכה עם המדיניות האחרונה שהייתה תקינה.

## בדיקת תחום (scope-gate) לפני merge

ה-workflow `scope-gate` מתקין את האייג'נט מה-PR, שולח לו את `scope_probes` ובודק שהוא מסרב לשאלות
שאינן בתחום שלו. שאלה שבמקרה נמצאת בתחום של האייג'נט (למשל מתכון – לאייג'נט בישול) מדולגת.

הבדיקה מריצה את קוד האייג'נט עם מפתח OpenRouter אמיתי, ולכן:

1. קוראים את הקוד של האייג'נט.
2. Actions ← `scope-gate` ← **Run workflow** ← מספר ה-PR.
3. מאשרים את ההרצה (סביבת `scope-gate` דורשת אישור), ובודקים שהיא עברה. הפירוט לכל שאלה נשמר
   כקובץ CSV בתוצרי ההרצה (`scope-reports`).

הבדיקה משתמשת ב-`policy.json` של `main`, כך ש-PR לא יכול להקל על הבדיקה של עצמו.

מפתחים יכולים להריץ את אותה בדיקה מקומית לפני שהם פותחים PR:

```bash
OPENROUTER_API_KEY=... safe-agents-manager check my-agent --scope --report evals/results/scope.csv
```

## מה ה-CI בודק

| בדיקה | למה |
|---|---|
| JSON Schema, מזהים וגרסאות ייחודיים, `latest` היא הגרסה הגבוהה | מבנה תקין |
| גרסה שפורסמה לא השתנתה | מי שכבר התקין מקבל בדיוק את מה שנבדק |
| הורדה + sha256 | הקובץ לא הוחלף |
| המניפסט שבתוך ה-zip זהה ל-`manifest_url` ולרשומה | החנות מציגה את מה שבאמת יותקן |
| התקנה מלאה + הפעלת השרת וטעינת הגרף | האייג'נט באמת עובד |
| `policy.json` תואם לסכמה | מדיניות פגומה לא נכנסת |
| (`scope-gate`, הרצה ידנית) האייג'נט מסרב לשאלות מחוץ לתחום | האייג'נט שומר על התפקיד שלו |

אפשר להריץ את אותה בדיקה מקומית:

```bash
uv tool install "safe-agents-manager @ git+https://github.com/SafeAI613/Safe-Agents#subdirectory=manager"
safe-agents-manager validate-registry registry.json --install
```

## הגדרות חובה בריפו

- **הריפו ציבורי**: האפליקציה קוראת את `registry.json` ואת `policy.json` בלי התחברות.
- **Branch protection** על `main`: חובת PR, חובת review מ-CODEOWNERS, חובת הצלחת `validate-registry`, ללא force push.
- ה-workflow `validate-registry` רץ על `pull_request` עם הרשאות קריאה בלבד וללא secrets, כי הוא מריץ את קוד האייג'נט. אין להחליף ל-`pull_request_target`.
- **Environment בשם `scope-gate`** (Settings ← Environments): חובת אישור של מנהל (Required reviewers), ו-secret בשם
  `OPENROUTER_GATE_KEY` – מפתח OpenRouter **נפרד** עם מגבלת אשראי נמוכה (למשל 2$). קוד האייג'נט שנבדק יכול
  לקרוא את המפתח, ולכן אסור להשתמש בו לשום דבר אחר.
