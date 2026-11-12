# רשימת עלייה לאוויר

הצעדים שנשארו כדי שמשתמשים יוכלו להוריד את Safe Agents ולהתקין אייג'נטים. לפי הסדר.

## 1. ריפו הפלטפורמה

- [x] הענף `claude/stoic-newton-4hwp5c` נדחף ל-`SafeAI613/Safe-Agents`.
- [ ] לפתוח PR מהענף ל-`main`, לבדוק שה-CI ירוק (כולל Windows), ולמזג.
- [ ] להפוך את `Safe-Agents` לציבורי, או לשנות את ה-workflows של הקטלוג כך שיתקינו את הבודק עם הרשאה:
  הם מתקינים אותו מ-`git+https://github.com/SafeAI613/Safe-Agents` בלי התחברות.

## 2. ריפו הקטלוג

- [x] `SafeAI613/safe-agents-registry` נוצר ונדחף (ענף `main`).
- [ ] להפוך אותו ל**ציבורי**: האפליקציה קוראת את `registry.json` ו-`policy.json` בלי הזדהות.
- [ ] לעבור על `policy.json`: הנושאים האסורים, מודל הבדיקה (`classifier_model` – לוודא שהוא קיים ב-OpenRouter)
  ושאלות הבדיקה לקטלוג.
- [ ] ב-`.github/CODEOWNERS` להחליף את `@SafeAI613/platform-maintainers` בצוות/משתמשים אמיתיים.
- [ ] Branch protection על `main`: חובת PR, חובת review מ-CODEOWNERS, חובת הצלחת `validate-registry`, בלי force push.
- [ ] Environment בשם `scope-gate` (Settings ← Environments): Required reviewers, ו-secret `OPENROUTER_GATE_KEY` –
  מפתח OpenRouter נפרד עם מגבלת אשראי נמוכה.
- [ ] אחרי ש-`Safe-Agents` מוזג ל-`main` – ה-workflows של הקטלוג מתקינים משם את הבודק (`MANAGER_REF: main`). מומלץ לנעול לתגית (`v0.1.0`) אחרי ה-Release הראשון.

## 3. בדיקה ידנית אחת עם שירותים אמיתיים

בסביבת הפיתוח הכול נבדק מול OpenRouter ו-GitHub מדומים. פעם אחת לפני ההפצה:
- [ ] מפתח OpenRouter אמיתי: הפעלה ראשונה, שיחה עם מודל בתשלום ועם מודל חינמי, בדיקה שהעלות מוצגת.
- [ ] סינון תוכן עם המודל האמיתי: שאלה בתחום עוברת, שאלה מחוץ לתחום נחסמת, ונושא מ-`blocked_topics` נחסם.
  כדאי לנסות כמה ניסוחים לכל סוג – המודל הבודק מכריע לפי התיאורים ב-`policy.json` ובמניפסט.
- [ ] אייג'נט אמיתי מהקטלוג: Release asset ← רשומה ב-`registry.json` ← התקנה מהחנות.
- [ ] מחשב Windows נקי: התקנה, שאין חלונות שחורים, סגירה ופתיחה – השיחות נשמרות.

## 4. Release ראשון

- [ ] (מומלץ) תעודת חתימת קוד ל-Windows ו-Apple Developer ID ל-macOS – ראו `desktop/README.md`.
- [ ] `git tag v0.1.0 && git push origin v0.1.0` ← ה-workflow `release` בונה התקנות ל-Windows/macOS/Linux כ-Release בטיוטה.
- [ ] לבדוק את קבצי ההתקנה ולפרסם את ה-Release.

## 5. האייג'נט הראשון בקטלוג

- [ ] לבחור אייג'נט של סטודנט שעומד בחוזה, להריץ `safe-agents-manager check <dir> --scope`.
- [ ] Release asset + `safe-agents-manager package` ← PR לקטלוג עם הרשומה שהפקודה מדפיסה.
- [ ] להריץ `scope-gate` על ה-PR ולוודא שעבר.
- [ ] אחרי merge – לוודא שהוא מופיע בחנות באפליקציה.

## 6. לסטודנטים

- [ ] להפיץ את `docs/AGENT_CONTRACT.md` ואת הפקודות `new-agent` / `check` / `package`.
- [ ] להבהיר: פרסום רק כ-Release asset, ו-`stream_usage=True` כדי שהעלות תוצג.
