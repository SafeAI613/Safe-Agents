import type { InstallStep } from "./types";

export const STEP_LABELS: Record<InstallStep, string> = {
  downloading: "הורדה",
  verifying: "אימות חתימה (sha256)",
  extracting: "חילוץ הקבצים",
  validating: "בדיקת קובץ ההגדרות",
  creating_env: "יצירת סביבת Python והתקנת ספריות",
  generating_config: "הגדרת שרת האייג'נט",
  smoke_test: "בדיקת הפעלה",
  activating: "רישום האייג'נט",
  installed: "הותקן",
  failed: "נכשל",
};

export const INSTALL_STEPS: InstallStep[] = [
  "downloading",
  "verifying",
  "extracting",
  "validating",
  "creating_env",
  "generating_config",
  "smoke_test",
  "activating",
];

export const PERMISSION_LABELS: Record<string, string> = {
  local_file_read: "קריאת קבצים מהמחשב",
  local_file_write: "יצירה ושינוי של קבצים במחשב",
  sandbox_code_execution: "הרצת קוד",
  network_access: "גישה לאינטרנט",
};

export const FEATURE_LABELS: Record<string, string> = {
  rag: "חיפוש במסמכים (RAG)",
  web_search: "חיפוש באינטרנט",
  code_execution: "הרצת קוד",
  mcp_support: "חיבור לשירותים חיצוניים (MCP)",
};
