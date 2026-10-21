import { AIMessage, HumanMessage } from "@langchain/core/messages";
import { describe, expect, it, vi } from "vitest";

import { ManagerApi } from "./api";
import { ApiError, describeError } from "./errors";
import { conversationMarkdown, formatUsd, textOf, usageItems } from "./messages";
import { runStatus } from "./runStatus";
import { parseBlock, readSse } from "./sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("sse", () => {
  it("parses events split across chunks and skips keep-alives", async () => {
    const body = streamOf(
      'event: progress\ndata: {"a"',
      ":1}\n\n: keep-alive\n\n",
      "data: x\r\n\r\n",
    );
    const events = [];
    for await (const message of readSse(body)) events.push(message);
    expect(events).toEqual([
      { event: "progress", data: '{"a":1}' },
      { event: "message", data: "x" },
    ]);
  });

  it("joins multi-line data", () => {
    expect(parseBlock("data: a\ndata: b")).toEqual({ event: "message", data: "a\nb" });
    expect(parseBlock(": only a comment")).toBeNull();
  });
});

describe("errors", () => {
  it("maps manager error codes to Hebrew and keeps the detail", () => {
    const text = describeError(new ApiError("missing_secret", "missing OPENROUTER_API_KEY", 412));
    expect(text).toContain("חסרים מפתחות");
    expect(text).toContain("OPENROUTER_API_KEY");
  });

  it("recognises provider errors coming from the agent", () => {
    expect(describeError(new Error("Error code: 402 - insufficient credits"))).toContain("קרדיט");
    expect(describeError(new Error("429 Too Many Requests"))).toContain("יותר מדי בקשות");
    expect(describeError("plain")).toBe("plain");
  });
});

describe("messages", () => {
  const ai = new AIMessage({
    id: "m1",
    content: [
      { type: "text", text: "Lorem " },
      { type: "text", text: "ipsum" },
    ],
    usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    response_metadata: { model_name: "foo/bar" },
  });

  it("extracts text from content blocks", () => {
    expect(textOf(ai)).toBe("Lorem ipsum");
    expect(textOf(new HumanMessage("foo"))).toBe("foo");
  });

  it("collects usage from instances and stored dicts", () => {
    const stored = {
      type: "ai",
      id: "m2",
      usage_metadata: { input_tokens: 1, output_tokens: 2 },
      response_metadata: {},
    };
    expect(
      usageItems([new HumanMessage("x"), ai, stored, { type: "ai", id: "m3" }], "fallback/model"),
    ).toEqual([
      { message_id: "m1", model: "foo/bar", input_tokens: 10, output_tokens: 5 },
      { message_id: "m2", model: "fallback/model", input_tokens: 1, output_tokens: 2 },
    ]);
  });

  it("exports a markdown transcript", () => {
    const markdown = conversationMarkdown("Foo", [new HumanMessage("שלום"), ai]);
    expect(markdown).toContain("# שיחה עם Foo");
    expect(markdown).toContain("## משתמש\n\nשלום");
    expect(markdown).toContain("## אייג'נט\n\nLorem ipsum");
  });

  it("formats costs", () => {
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(0.000123)).toBe("$0.00012");
    expect(formatUsd(1.5)).toBe("$1.50");
  });
});

describe("runStatus", () => {
  const base = {
    isLoading: true,
    hasInterrupt: false,
    lastRole: "human",
    lastHasText: true,
    runningTools: [],
  };

  it("follows the run", () => {
    expect(runStatus({ ...base, isLoading: false }).phase).toBe("idle");
    expect(runStatus(base).phase).toBe("starting");
    expect(runStatus({ ...base, lastRole: "ai" }).phase).toBe("writing");
    expect(runStatus({ ...base, runningTools: ["search"] }).label).toContain("search");
    expect(runStatus({ ...base, isLoading: false, hasInterrupt: true }).phase).toBe("waiting");
  });
});

describe("ManagerApi", () => {
  it("sends the token and maps API errors", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer t");
      return new Response(JSON.stringify({ error: { code: "agent_not_found", message: "nope" } }), {
        status: 404,
      });
    });
    const api = new ManagerApi("http://m", "t", fetchImpl as typeof fetch);
    await expect(api.getAgent("foo_agent")).rejects.toMatchObject({
      code: "agent_not_found",
      status: 404,
    });
    expect(fetchImpl).toHaveBeenCalledWith("http://m/agents/foo_agent", expect.anything());
  });

  it("reports network failures with a stable code", async () => {
    const api = new ManagerApi("http://m", "t", (() =>
      Promise.reject(new TypeError("Failed to fetch"))) as typeof fetch);
    await expect(api.listAgents()).rejects.toMatchObject({ code: "network" });
  });

  it("follows a job's SSE stream to the end", async () => {
    const body = streamOf(
      'event: progress\ndata: {"step":"extracting","message":"x","detail":false,"timestamp":"t"}\n\n',
      'event: done\ndata: {"id":"j","kind":"install","status":"succeeded","result":null,"error":null}\n\n',
    );
    const api = new ManagerApi("http://m", "t", (async () => new Response(body)) as typeof fetch);
    const seen: string[] = [];
    const job = await api.followJob("j", (event) => seen.push(event.step));
    expect(seen).toEqual(["extracting"]);
    expect(job.status).toBe("succeeded");
  });
});
