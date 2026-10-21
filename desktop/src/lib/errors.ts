/** Error from the manager API, carrying its stable error code. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const MESSAGES: Record<string, string> = {
  invalid_api_key: "מפתח ה-API נדחה על ידי OpenRouter. כדאי לבדוק שהעתקת אותו במלואו.",
  missing_secret: "חסרים מפתחות שהאייג'נט צריך. אפשר להזין אותם בהגדרות האייג'נט.",
  registry_unavailable: "אין חיבור לקטלוג האייג'נטים. כדאי לבדוק את החיבור לאינטרנט.",
  invalid_registry: "הקטלוג שהתקבל אינו תקין. נסו שוב מאוחר יותר.",
  download_failed: "ההורדה נכשלה. כדאי לבדוק את החיבור לאינטרנט ולנסות שוב.",
  unsafe_url: "הקישור להורדה אינו מאושר, ולכן ההורדה נחסמה.",
  invalid_archive: "קובץ האייג'נט פגום או שאינו תואם למה שפורסם בקטלוג.",
  invalid_manifest: "קובץ ההגדרות של האייג'נט (manifest.json) אינו תקין.",
  install_failed: "ההתקנה נכשלה. הפרטים מופיעים ביומן ההתקנה.",
  agent_already_installed: "הגרסה הזו כבר מותקנת.",
  agent_running: "יש לעצור את האייג'נט לפני עדכון.",
  agent_not_found: "האייג'נט לא נמצא.",
  agent_start_failed: "האייג'נט לא הצליח לעלות. הפרטים מופיעים ביומן של האייג'נט.",
  no_free_port: "אין פורט פנוי להפעלת אייג'נט נוסף. כדאי לעצור אייג'נטים שאינם בשימוש.",
  secret_store_unavailable: "אין גישה למאגר הסיסמאות של מערכת ההפעלה.",
  upstream_error: "השירות החיצוני לא הגיב. נסו שוב בעוד רגע.",
  invalid_request: "הבקשה אינה תקינה.",
  network: "אין חיבור למנהל האייג'נטים.",
};

/** A clear Hebrew message, followed by the technical detail when it adds information. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const friendly = MESSAGES[error.code];
    return friendly ? `${friendly} (${error.message})` : error.message;
  }
  const text = error instanceof Error ? error.message : String(error);
  if (/401|unauthori[sz]ed/i.test(text)) return "המפתח של OpenRouter אינו תקין או שפג תוקפו.";
  if (/402|insufficient|credits/i.test(text)) return "נגמר הקרדיט בחשבון OpenRouter.";
  if (/429|rate.?limit/i.test(text)) return "יותר מדי בקשות למודל. המתינו רגע ונסו שוב.";
  if (/failed to fetch|networkerror|load failed/i.test(text)) return MESSAGES.network ?? text;
  return text;
}
