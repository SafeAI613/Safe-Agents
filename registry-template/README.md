# Safe-Agents Registry

הקטלוג המרכזי של אייג'נטים לפלטפורמת Safe-Agents Desktop.
האפליקציה קוראת את `registry.json` מהענף `main`. כל מה שמופיע כאן – מוצג בחנות וניתן להתקנה.

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

## מה ה-CI בודק

| בדיקה | למה |
|---|---|
| JSON Schema, מזהים וגרסאות ייחודיים, `latest` היא הגרסה הגבוהה | מבנה תקין |
| גרסה שפורסמה לא השתנתה | מי שכבר התקין מקבל בדיוק את מה שנבדק |
| הורדה + sha256 | הקובץ לא הוחלף |
| המניפסט שבתוך ה-zip זהה ל-`manifest_url` ולרשומה | החנות מציגה את מה שבאמת יותקן |
| התקנה מלאה + הפעלת השרת וטעינת הגרף | האייג'נט באמת עובד |

אפשר להריץ את אותה בדיקה מקומית:

```bash
uv tool install "safe-agents-manager @ git+https://github.com/SafeAI613/Safe-Agents#subdirectory=manager"
safe-agents-manager validate-registry registry.json --install
```

## הגדרות חובה בריפו

- **Branch protection** על `main`: חובת PR, חובת review מ-CODEOWNERS, חובת הצלחת `validate-registry`, ללא force push.
- ה-workflow רץ על `pull_request` עם הרשאות קריאה בלבד וללא secrets, כי הוא מריץ את קוד האייג'נט. אין להחליף ל-`pull_request_target`.
