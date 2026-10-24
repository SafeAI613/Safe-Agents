# רשימת עלייה לאוויר

הצעדים שנשארו כדי שמשתמשים יוכלו להוריד את Safe Agents ולהתקין אייג'נטים. לפי הסדר.

## 1. גישה ל-GitHub (מנהל/ת הארגון)

- [ ] להתקין את Claude GitHub App לארגון `SafeAI613` עם גישת כתיבה ל-`Safe-Agents` ‏(https://github.com/apps/claude/installations/select_target), **או** לדחוף ידנית מהגיבוי:
  ```bash
  git clone safe-agents-*.bundle -b claude/stoic-newton-4hwp5c Safe-Agents
  cd Safe-Agents && git remote set-url origin https://github.com/SafeAI613/Safe-Agents.git
  git push -u origin claude/stoic-newton-4hwp5c
  ```
- [ ] לפתוח PR מהענף ל-`main`, לבדוק שה-CI ירוק (כולל Windows), ולמזג.

## 2. ריפו הקטלוג

- [ ] ליצור ריפו **ציבורי** ריק `SafeAI613/safe-agents-registry` (האפליקציה קוראת את `registry.json` בלי הזדהות).
- [ ] לדחוף את התוכן המוכן:
  ```bash
  git clone safe-agents-registry.bundle -b main safe-agents-registry
  cd safe-agents-registry && git remote set-url origin https://github.com/SafeAI613/safe-agents-registry.git
  git push -u origin main
  ```
- [ ] ב-`.github/CODEOWNERS` להחליף את `@SafeAI613/platform-maintainers` בצוות/משתמשים אמיתיים.
- [ ] Branch protection על `main`: חובת PR, חובת review מ-CODEOWNERS, חובת הצלחת `validate-registry`, בלי force push.
- [ ] אחרי ש-`Safe-Agents` מוזג ל-`main` – ה-workflow של הקטלוג מתקין משם את הבודק (`MANAGER_REF: main`). מומלץ לנעול לתגית (`v0.1.0`) אחרי ה-Release הראשון.

## 3. בדיקה ידנית אחת עם שירותים אמיתיים

בסביבת הפיתוח הכול נבדק מול OpenRouter ו-GitHub מדומים. פעם אחת לפני ההפצה:
- [ ] מפתח OpenRouter אמיתי: הפעלה ראשונה, שיחה עם מודל בתשלום ועם מודל חינמי, בדיקה שהעלות מוצגת.
- [ ] אייג'נט אמיתי מהקטלוג: Release asset ← רשומה ב-`registry.json` ← התקנה מהחנות.
- [ ] מחשב Windows נקי: התקנה, שאין חלונות שחורים, סגירה ופתיחה – השיחות נשמרות.

## 4. Release ראשון

- [ ] (מומלץ) תעודת חתימת קוד ל-Windows ו-Apple Developer ID ל-macOS – ראו `desktop/README.md`.
- [ ] `git tag v0.1.0 && git push origin v0.1.0` ← ה-workflow `release` בונה התקנות ל-Windows/macOS/Linux כ-Release בטיוטה.
- [ ] לבדוק את קבצי ההתקנה ולפרסם את ה-Release.

## 5. האייג'נט הראשון בקטלוג

- [ ] לבחור אייג'נט של סטודנט שעומד בחוזה, להריץ `safe-agents-manager check <dir> --install`.
- [ ] Release asset + `safe-agents-manager package` ← PR לקטלוג עם הרשומה שהפקודה מדפיסה.
- [ ] אחרי merge – לוודא שהוא מופיע בחנות באפליקציה.

## 6. לסטודנטים

- [ ] להפיץ את `docs/AGENT_CONTRACT.md` ואת הפקודות `new-agent` / `check` / `package`.
- [ ] להבהיר: פרסום רק כ-Release asset, ו-`stream_usage=True` כדי שהעלות תוצג.
