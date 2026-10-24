import { INSTALL_STEPS, STEP_LABELS } from "../lib/labels";
import type { InstallEvent } from "../lib/types";

type StepState = "pending" | "current" | "done" | "failed";

/** Step list driven by the install job's progress events. */
export function InstallProgress({ events }: { events: InstallEvent[] }) {
  const summary = events.filter((event) => !event.detail);
  const final = summary.at(-1);
  const failed = final?.step === "failed";
  const installed = final?.step === "installed";
  // A local ZIP install has no download step.
  const steps = summary.some((event) => event.step === "downloading")
    ? INSTALL_STEPS
    : INSTALL_STEPS.filter((step) => step !== "downloading");
  const lastStep = summary.filter((event) => event.step !== "failed").at(-1)?.step;
  const lastIndex = lastStep ? steps.indexOf(lastStep) : -1;
  const percent = summary.filter((e) => e.step === "downloading" && e.message.endsWith("%")).at(-1);
  const details = events.filter((event) => event.detail).map((event) => event.message);
  const working = !installed && !failed && lastStep === "creating_env";

  function stateOf(index: number): StepState {
    if (installed || index < lastIndex) return "done";
    if (index > lastIndex) return "pending";
    return failed ? "failed" : "current";
  }

  return (
    <div className="install-progress" aria-live="polite">
      <ol className="steps">
        {steps.map((step, index) => (
          <li key={step} className={`step step-${stateOf(index)}`}>
            {STEP_LABELS[step]}
            {step === "downloading" && stateOf(index) === "current" && percent
              ? ` ${percent.message}`
              : ""}
          </li>
        ))}
      </ol>
      {working && (
        <p className="muted" aria-live="polite">
          בהתקנה הראשונה השלב הזה מוריד את Python והספריות ויכול לקחת כמה דקות.
          {details.length > 0 && (
            <>
              <br />
              <bdi dir="ltr" data-testid="install-activity">
                {details.at(-1)}
              </bdi>
            </>
          )}
        </p>
      )}
      {failed && <p className="banner banner-error">{final.message}</p>}
      {installed && <p className="banner banner-ok">{final.message}</p>}
      {details.length > 0 && (
        <details>
          <summary>יומן התקנה מפורט</summary>
          <pre className="log" dir="ltr">
            {details.join("\n")}
          </pre>
        </details>
      )}
    </div>
  );
}
