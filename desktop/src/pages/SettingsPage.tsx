import { useState, type FormEvent } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { LogsDialog } from "../components/LogsDialog";
import { useApi } from "../lib/context";
import { formatUsd } from "../lib/messages";
import { useAsync } from "../lib/useAsync";

export function SettingsPage({ onKeyRemoved }: { onKeyRemoved: () => void }) {
  const api = useApi();
  const settings = useAsync(() => api.settings(), [api]);
  const keyInfo = useAsync(() => api.openRouterStatus(), [api]);
  const usage = useAsync(() => api.usage(), [api]);
  const about = useAsync(() => api.about(), [api]);
  const [showLog, setShowLog] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [langsmith, setLangsmith] = useState("");
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState<string>();

  async function act(action: () => Promise<unknown>, message: string) {
    setError(undefined);
    setNotice(undefined);
    try {
      await action();
      setNotice(message);
      settings.reload();
      keyInfo.reload();
    } catch (reason) {
      setError(reason);
    }
  }

  async function copySupportInfo() {
    // Never includes keys: the manager does not return them and logs never contain them.
    const log = await api.managerLog(50).catch(() => ({ lines: [] as string[] }));
    const text = [
      `Safe Agents ${about.data?.version ?? "?"}`,
      `data: ${about.data?.data_dir ?? "?"}`,
      `user agent: ${navigator.userAgent}`,
      "",
      ...log.lines,
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setNotice("הפרטים הועתקו. אפשר להדביק אותם בפנייה לתמיכה.");
    } catch (reason) {
      setError(reason);
    }
  }

  function replaceKey(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await api.setOpenRouterKey(newKey);
      setNewKey("");
    }, "המפתח עודכן.");
  }

  function saveLangsmith(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await api.setLangSmithKey(langsmith);
      setLangsmith("");
    }, "מפתח LangSmith נשמר. הניטור יופעל בהפעלה הבאה של כל אייג'נט.");
  }

  return (
    <section className="stack">
      <h1>הגדרות</h1>
      <ErrorBanner error={error ?? settings.error} />
      {notice && <p className="banner banner-ok">{notice}</p>}

      <article className="card stack">
        <h2>OpenRouter</h2>
        <ErrorBanner error={keyInfo.error} onRetry={keyInfo.reload} />
        {keyInfo.data && (
          <dl className="facts">
            <dt>שם המפתח</dt>
            <dd>{keyInfo.data.label ?? "—"}</dd>
            <dt>שימוש עד כה</dt>
            <dd>{keyInfo.data.usage_usd != null ? formatUsd(keyInfo.data.usage_usd) : "—"}</dd>
            <dt>מגבלה</dt>
            <dd>
              {keyInfo.data.limit_usd != null ? formatUsd(keyInfo.data.limit_usd) : "ללא מגבלה"}
            </dd>
          </dl>
        )}
        <form className="row" onSubmit={replaceKey}>
          <input
            type="password"
            dir="ltr"
            autoComplete="off"
            aria-label="מפתח OpenRouter חדש"
            placeholder="מפתח חדש: sk-or-..."
            value={newKey}
            onChange={(event) => setNewKey(event.target.value)}
          />
          <button type="submit" disabled={!newKey.trim()}>
            החלפה
          </button>
        </form>
        <button
          type="button"
          className="danger"
          onClick={() => void act(api.deleteOpenRouterKey, "המפתח נמחק.").then(onKeyRemoved)}
        >
          מחיקת המפתח
        </button>
      </article>

      <article className="card stack">
        <h2>ניטור עם LangSmith (אופציונלי)</h2>
        <p className="muted">
          כשמוגדר מפתח, כל אייג'נט שולח ל-LangSmith את מהלך הריצה שלו, כולל תוכן ההודעות, לצורך
          ניטור ובדיקה. אל תפעילו את האפשרות אם אינכם רוצים שתוכן השיחות יישלח לשירות הזה.
        </p>
        <p>מצב: {settings.data?.langsmith_key_set ? "מופעל" : "כבוי"}</p>
        <form className="row" onSubmit={saveLangsmith}>
          <input
            type="password"
            dir="ltr"
            autoComplete="off"
            aria-label="מפתח LangSmith"
            placeholder="lsv2_..."
            value={langsmith}
            onChange={(event) => setLangsmith(event.target.value)}
          />
          <button type="submit" disabled={!langsmith.trim()}>
            שמירה
          </button>
        </form>
        {settings.data?.langsmith_key_set && (
          <button
            type="button"
            className="danger"
            onClick={() => void act(api.deleteLangSmithKey, "הניטור כובה.")}
          >
            כיבוי הניטור
          </button>
        )}
      </article>

      <article className="card stack">
        <h2>שימוש ועלויות</h2>
        <ErrorBanner error={usage.error} />
        {usage.data && (
          <>
            <p>
              סה"כ: <strong>{formatUsd(usage.data.cost_usd)}</strong> ·{" "}
              {usage.data.input_tokens.toLocaleString("he-IL")} טוקנים בקלט ·{" "}
              {usage.data.output_tokens.toLocaleString("he-IL")} בפלט
            </p>
            {usage.data.by_model.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>מודל</th>
                    <th>קריאות</th>
                    <th>טוקנים</th>
                    <th>עלות</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.data.by_model.map((row) => (
                    <tr key={row.model}>
                      <td dir="ltr">{row.model}</td>
                      <td>{row.calls}</td>
                      <td>{(row.input_tokens + row.output_tokens).toLocaleString("he-IL")}</td>
                      <td>{row.priced ? formatUsd(row.cost_usd) : "לא ידוע"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </article>

      <article className="card stack" aria-label="אודות ותמיכה">
        <h2>אודות ותמיכה</h2>
        {about.data && (
          <dl className="facts">
            <dt>גרסה</dt>
            <dd dir="ltr">{about.data.version}</dd>
            <dt>תיקיית נתונים</dt>
            <dd dir="ltr">{about.data.data_dir}</dd>
            <dt>יומנים</dt>
            <dd dir="ltr">{about.data.logs_dir}</dd>
            <dt>ייצוא שיחות</dt>
            <dd dir="ltr">{about.data.exports_dir}</dd>
          </dl>
        )}
        <div className="actions">
          <button type="button" onClick={() => setShowLog(true)}>
            יומן המערכת
          </button>
          <button type="button" onClick={() => void copySupportInfo()}>
            העתקת פרטים לתמיכה
          </button>
        </div>
      </article>
      {showLog && <LogsDialog onClose={() => setShowLog(false)} />}
    </section>
  );
}
