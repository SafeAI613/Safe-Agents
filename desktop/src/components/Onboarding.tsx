import { useState, type FormEvent } from "react";

import { useApi } from "../lib/context";
import { ErrorBanner } from "./ErrorBanner";

/** First run: the OpenRouter key every agent uses. Validated before it is saved. */
export function Onboarding({ onDone }: { onDone: () => void }) {
  const api = useApi();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await api.setOpenRouterKey(key);
      onDone();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="onboarding">
      <h1>ברוכים הבאים ל-Safe Agents</h1>
      <p>
        כל האייג'נטים במערכת משתמשים במודלים דרך <bdi>OpenRouter</bdi>. כדי להתחיל, הדביקו את מפתח
        ה-API שלכם. המפתח נשמר במאגר הסיסמאות המאובטח של מערכת ההפעלה ולא נשלח לשום מקום מלבד
        OpenRouter.
      </p>
      <form onSubmit={submit} className="stack">
        <label htmlFor="openrouter-key">מפתח OpenRouter</label>
        <input
          id="openrouter-key"
          type="password"
          dir="ltr"
          autoComplete="off"
          placeholder="sk-or-..."
          value={key}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <ErrorBanner error={error} />
        <button type="submit" className="primary" disabled={busy || !key.trim()}>
          {busy ? "בודק את המפתח..." : "שמירה והמשך"}
        </button>
      </form>
    </main>
  );
}
