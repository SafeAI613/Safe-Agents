import { useEffect, useRef, useState } from "react";

import { useApi } from "../lib/context";
import { ApiError } from "../lib/errors";
import type { InstallEvent, Job } from "../lib/types";
import { ErrorBanner } from "./ErrorBanner";
import { InstallProgress } from "./InstallProgress";

/** Starts an install job once and shows its live progress. */
export function InstallRun({
  start,
  onClose,
  onOpenAgents,
}: {
  start: () => Promise<Job>;
  onClose: () => void;
  onOpenAgents: () => void;
}) {
  const api = useApi();
  const [events, setEvents] = useState<InstallEvent[]>([]);
  const [phase, setPhase] = useState<"installing" | "done" | "failed">("installing");
  const [error, setError] = useState<unknown>();
  // React StrictMode runs effects twice in development; an install must start only once.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void (async () => {
      try {
        const job = await start();
        let reportedFailure = false;
        const finished = await api.followJob(job.id, (event) => {
          reportedFailure ||= event.step === "failed";
          setEvents((previous) => [...previous, event]);
        });
        setPhase(finished.status === "succeeded" ? "done" : "failed");
        // The step list shows failure events; this covers jobs that failed before sending one.
        if (finished.error && !reportedFailure) {
          setError(new ApiError(finished.error.code, finished.error.message, 0));
        }
      } catch (reason) {
        setError(reason);
        setPhase("failed");
      }
    })();
  }, [api, start]);

  return (
    <>
      <InstallProgress events={events} />
      <ErrorBanner error={error} />
      <div className="actions">
        {phase === "done" && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              onClose();
              onOpenAgents();
            }}
          >
            לאייג'נטים שלי
          </button>
        )}
        <button type="button" onClick={onClose} disabled={phase === "installing"}>
          סגירה
        </button>
      </div>
    </>
  );
}
