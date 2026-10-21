export interface RunSnapshot {
  isLoading: boolean;
  hasInterrupt: boolean;
  /** Role and text length of the newest message. */
  lastRole: string | undefined;
  lastHasText: boolean;
  /** Names of tool calls that are still running. */
  runningTools: string[];
}

export interface RunStatus {
  phase: "idle" | "starting" | "tool" | "writing" | "waiting";
  label: string;
}

/**
 * The live stage of the current run, from what the agent server streams: the run's
 * lifecycle, token deltas of the answer, tool-call events and pending interrupts.
 */
export function runStatus(run: RunSnapshot): RunStatus {
  if (run.hasInterrupt) return { phase: "waiting", label: "ממתין לאישור שלך" };
  if (!run.isLoading) return { phase: "idle", label: "" };
  if (run.runningTools.length > 0) {
    return { phase: "tool", label: `מפעיל כלי: ${run.runningTools.join(", ")}` };
  }
  if (run.lastRole === "ai" && run.lastHasText) return { phase: "writing", label: "מנסח תשובה..." };
  return { phase: "starting", label: "האייג'נט עובד..." };
}
