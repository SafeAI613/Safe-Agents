import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { InstallEvent, InstallStep } from "../lib/types";
import { InstallProgress } from "./InstallProgress";

const event = (step: InstallStep, message: string = step, detail = false): InstallEvent => ({
  step,
  message,
  detail,
  timestamp: "t",
});

function stateOf(label: string): string {
  return screen.getByText(label, { exact: false }).className;
}

describe("InstallProgress", () => {
  afterEach(cleanup);

  it("marks finished, current and pending steps", () => {
    render(
      <InstallProgress
        events={[event("downloading"), event("downloading", "40%"), event("verifying")]}
      />,
    );
    expect(stateOf("הורדה")).toContain("step-done");
    expect(stateOf("אימות חתימה")).toContain("step-current");
    expect(stateOf("בדיקת הפעלה")).toContain("step-pending");
  });

  it("shows live uv output while the environment is created", () => {
    render(
      <InstallProgress
        events={[
          event("extracting"),
          event("creating_env"),
          event("creating_env", "Downloading cpython-3.12", true),
          event("creating_env", "Resolved 42 packages", true),
        ]}
      />,
    );
    expect(screen.getByText(/כמה דקות/)).toBeTruthy();
    expect(screen.getByTestId("install-activity").textContent).toBe("Resolved 42 packages");
  });

  it("shows the failing step and the error", () => {
    render(
      <InstallProgress
        events={[
          event("extracting"),
          event("creating_env"),
          event("creating_env", "uv output", true),
          event("failed", "uv failed"),
        ]}
      />,
    );
    expect(stateOf("יצירת סביבת")).toContain("step-failed");
    expect(screen.getByText("uv failed")).toBeTruthy();
    expect(screen.getByText("יומן התקנה מפורט")).toBeTruthy();
  });
});
