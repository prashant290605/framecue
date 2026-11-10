import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { resetConnectionForTests } from "./lib/storage";
import { timecode } from "./lib/time";
import { file, json, mockFetch, sampleDoc, sampleDocWithAi } from "./test/helpers";

const user = () => userEvent.setup();

async function analyzeWith(doc = sampleDoc()) {
  const fetchMock = mockFetch({ analyze: () => json(doc) });
  const u = user();
  render(<App />);
  const input = await screen.findByTestId("screenplay-input");
  await u.upload(input, file("pack.zip", "PK"));
  await u.click(screen.getByRole("button", { name: /^Analyze/ }));
  await screen.findByRole("tablist", { name: "Ranking" }, { timeout: 10_000 });
  return { u, fetchMock, doc };
}

const list = () => screen.getByRole("listbox");
const detail = () => screen.getByRole("main", { name: "Candidate detail" });

describe("upload", () => {
  it("analyzes an upload and opens the workspace", async () => {
    const { fetchMock, doc } = await analyzeWith();
    expect(fetchMock.calls.filter((c) => c.url === "/api/analyze")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "pack.zip" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/review");
    expect(screen.getByText(`${doc.summary.episodes} episodes · ${doc.summary.candidates} candidates`, { exact: false })).toBeInTheDocument();
  });

  it("rejects an unsupported file before calling the API", async () => {
    const fetchMock = mockFetch();
    const u = userEvent.setup({ applyAccept: false });
    render(<App />);
    await u.upload(await screen.findByTestId("screenplay-input"), file("notes.pdf"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not a supported file/);
    expect(fetchMock.calls.some((c) => c.url === "/api/analyze")).toBe(false);
  });

  it("shows API failures without leaving the upload screen", async () => {
    mockFetch({ analyze: () => json({ error: { code: "no_valid_episodes", message: "x" } }, 422) });
    const u = user();
    render(<App />);
    await u.upload(await screen.findByTestId("screenplay-input"), file("e.txt"));
    await u.click(screen.getByRole("button", { name: /^Analyze/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No valid screenplay episode/);
    expect(screen.queryByRole("tablist", { name: "Ranking" })).not.toBeInTheDocument();
  });

  it("runs the fictional sample through the real API contract", async () => {
    const { calls } = mockFetch({ analyze: () => json(sampleDoc()) });
    const u = user();
    render(<App />);
    await u.click(await screen.findByRole("button", { name: /Try the fictional sample/ }));
    await screen.findByRole("tablist", { name: "Ranking" }, { timeout: 10_000 });
    const sent = calls.find((c) => c.url === "/api/analyze")!.init!.body as FormData;
    expect((sent.getAll("files") as File[]).map((f) => f.name)).toEqual([
      "harbor-light-01.txt",
      "harbor-light-02.txt",
      "harbor-light-03.txt",
    ]);
  });
});

describe("views and provenance", () => {
  it("shows full-show candidates as unverified heuristic suggestions", async () => {
    const { doc } = await analyzeWith();
    expect(screen.getByRole("tab", { name: /Full show/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/Unverified suggestions ranked by the heuristic baseline/)).toBeInTheDocument();
    const options = within(list()).getAllByRole("option");
    expect(options.length).toBe(doc.views.coverage.primaries);
    expect(within(detail()).getByText("Heuristic priority")).toBeInTheDocument();
    expect(within(detail()).getByText("Unverified suggestion")).toBeInTheDocument();
    expect(within(detail()).queryByText("AI weighted score")).not.toBeInTheDocument();
    expect(within(detail()).getByText(/No AI review for this candidate/)).toBeInTheDocument();
  });

  it("explains an empty AI view and offers the full show", async () => {
    const { u } = await analyzeWith();
    await u.click(screen.getByRole("tab", { name: /AI-reviewed/ }));
    expect(screen.getByText("No AI-reviewed picks in this analysis")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Browse the full show/ }));
    expect(screen.getByRole("tab", { name: /Full show/ })).toHaveAttribute("aria-selected", "true");
  });

  it("shows genuine AI judgements on the 0–100 scale with evidence", async () => {
    const doc = sampleDocWithAi();
    await analyzeWith(doc);
    expect(screen.getByRole("tab", { name: /AI-reviewed/ })).toHaveAttribute("aria-selected", "true");
    const topId = doc.views.ai.order[0];
    const j = doc.ai_evaluations[topId];
    const d = detail();
    expect(within(d).getByText("AI weighted score")).toBeInTheDocument();
    expect(within(d).getByText(String(j.weighted_score))).toBeInTheDocument();
    expect(within(d).getByText("/ 100")).toBeInTheDocument();
    expect(within(d).getByText(j.hook_explanation)).toBeInTheDocument();
    expect(within(d).getByText(j.cliffhanger_explanation)).toBeInTheDocument();
    expect(within(d).getAllByRole("button", { name: j.hook_event_ids[0] }).length).toBeGreaterThan(0);
    expect(screen.getByText(/^Provisional:/)).toBeInTheDocument();
  });

  it("marks AI availability in the full show without mixing scores", async () => {
    const doc = sampleDocWithAi();
    const { u } = await analyzeWith(doc);
    await u.click(screen.getByRole("tab", { name: /Full show/ }));
    expect(within(list()).getAllByLabelText("AI review available").length).toBeGreaterThan(0);
    expect(within(detail()).queryByText("AI weighted score")).not.toBeInTheDocument();
  });
});

describe("inspection, filters and review", () => {
  it("selects candidates and shows exact timecodes", async () => {
    const { u, doc } = await analyzeWith();
    const second = within(list()).getAllByRole("option")[1];
    await u.click(second);
    const id = doc.views.coverage.order.filter((x) => doc.candidates.find((c) => c.candidate_id === x)!.coverage_rank)[1];
    const c = doc.candidates.find((x) => x.candidate_id === id)!;
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(within(detail()).getByRole("heading", { level: 2 })).toHaveTextContent(`${c.start_timestamp} → ${c.end_timestamp}`);
    await u.keyboard("{ArrowDown}");
    expect(within(list()).getAllByRole("option")[2]).toHaveAttribute("aria-selected", "true");
  });

  it("filters by search and episode, and clears filters from the empty state", async () => {
    const { u, doc } = await analyzeWith();
    await u.type(screen.getByRole("searchbox", { name: "Search candidates" }), "zzzz-nothing");
    expect(await screen.findByText("Nothing matches")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(within(list()).getAllByRole("option").length).toBe(doc.views.coverage.primaries);
    await u.click(screen.getByRole("button", { name: /^Filters/ }));
    await u.selectOptions(screen.getByLabelText("Episode"), "2");
    const shown = within(list()).getAllByRole("option");
    expect(shown.length).toBeGreaterThan(0);
    for (const o of shown) expect(o).toHaveTextContent(/^\d*EP 2/);
    expect(screen.getByRole("button", { name: "Remove filter Episode 2" })).toBeInTheDocument();
  });

  it("records decisions and notes, filters by decision, and shows them in the list", async () => {
    const { u } = await analyzeWith();
    await u.click(within(detail()).getByRole("button", { name: /Accept/ }));
    expect(within(detail()).getByRole("button", { name: /Accept/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(list()).getAllByLabelText("Accepted")).toHaveLength(1);
    await u.click(within(detail()).getByRole("button", { name: /Add a private note/ }));
    await u.type(within(detail()).getByLabelText("Private note"), "Use as lead promo");
    await u.click(screen.getByRole("button", { name: /^Filters/ }));
    await u.selectOptions(screen.getByLabelText("Your decision"), "accepted");
    expect(within(list()).getAllByRole("option")).toHaveLength(1);
    await u.keyboard("{Escape}");
    await u.click(within(detail()).getByRole("button", { name: /Accept/ }));
    expect(within(detail()).getByRole("button", { name: /Accept/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("supports keyboard decisions but not while typing", async () => {
    const { u } = await analyzeWith();
    list().focus();
    await u.keyboard("r");
    expect(within(detail()).getByRole("button", { name: /Reject/ })).toHaveAttribute("aria-pressed", "true");
    await u.click(screen.getByRole("searchbox", { name: "Search candidates" }));
    await u.keyboard("a");
    expect(within(detail()).getByRole("button", { name: /Accept/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("switches between cuts of the same moment", async () => {
    const { u } = await analyzeWith();
    await u.click(screen.getByRole("button", { name: /^Filters/ }));
    await u.click(screen.getByLabelText("Include alternative cuts"));
    await u.keyboard("{Escape}");
    const alt = within(list()).getAllByLabelText("Alternative cut")[0].closest("[role=option]") as HTMLElement;
    await u.click(alt);
    const cuts = within(detail()).getByRole("region", { name: "Alternative cuts" });
    const buttons = within(cuts).getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(1);
    expect(buttons[0]).toHaveAttribute("aria-current", "false"); // an alternative is open
    await u.click(buttons[0]);
    const after = within(detail()).getByRole("region", { name: "Alternative cuts" }); // re-mounted
    expect(within(after).getAllByRole("button")[0]).toHaveAttribute("aria-current", "true");
  });

  it("inspects timeline events and syncs with the excerpt", async () => {
    const { u } = await analyzeWith();
    const timeline = within(detail()).getByRole("figure", { name: /Screenplay timeline/ });
    const dialogue = within(timeline).getByRole("group", { name: "Dialogue events" });
    const bar = within(dialogue).getAllByRole("button")[0];
    expect(within(detail()).getByText(/Select an event to read it here/)).toBeInTheDocument();
    await u.click(bar);
    expect(bar).toHaveAttribute("aria-pressed", "true");
    const id = bar.getAttribute("data-event-id")!;
    expect(within(detail()).getAllByText(id).length).toBeGreaterThan(0); // inspector shows it
    const excerptLine = within(detail()).getAllByRole("button").find((b) => b.hasAttribute("data-excerpt-line") && b.dataset.active === "true");
    expect(excerptLine).toBeDefined(); // the excerpt highlights the same event
  });

  it("steps through timeline events in screenplay order with exact timecodes", async () => {
    const { u, doc } = await analyzeWith();
    const timeline = within(detail()).getByRole("figure", { name: /Screenplay timeline/ });
    const chips = within(timeline).getAllByRole("button");
    await u.click(within(detail()).getByRole("button", { name: "Next event" }));
    const first = chips.find((b) => b.getAttribute("aria-pressed") === "true")!;
    expect(first).toBeDefined(); // the first event inside the clip
    const firstId = first.getAttribute("data-event-id")!;
    await u.click(within(detail()).getByRole("button", { name: "Next event" }));
    const second = chips.find((b) => b.getAttribute("aria-pressed") === "true")!;
    const secondId = second.getAttribute("data-event-id")!;
    const order = doc.events.map((e) => e.id);
    expect(order.indexOf(secondId)).toBe(order.indexOf(firstId) + 1);
    const ev = doc.events.find((e) => e.id === secondId)!;
    const card = within(detail()).getByText(secondId).closest("div")!.parentElement!;
    expect(card).toHaveTextContent(`${timecode(ev.start_ms)} – ${timecode(ev.end_ms)}`);
  });

  it("explains a zero AI count when no bundle was supplied", async () => {
    const { u } = await analyzeWith();
    await u.click(screen.getByRole("button", { name: /AI reviewed/ }));
    expect(await screen.findByText(/No AI evaluation bundle was supplied with this analysis/)).toBeInTheDocument();
  });
});

describe("persistence", () => {
  it("restores the analysis and decisions after a refresh", async () => {
    const { u } = await analyzeWith();
    await u.click(within(list()).getAllByRole("option")[2]);
    await u.click(within(detail()).getByRole("button", { name: /Later/ }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved in this browser"));
    await act(() => new Promise((r) => setTimeout(r, 450))); // debounced review save
    const selectedBefore = within(detail()).getByRole("heading", { level: 2 }).textContent;
    cleanup(); // unmount, like closing the tab
    resetConnectionForTests(); // like a page reload: new connection, same database
    mockFetch();
    render(<App />);
    await screen.findByRole("tablist", { name: "Ranking" }, { timeout: 10_000 });
    expect(within(detail()).getByRole("heading", { level: 2 }).textContent).toBe(selectedBefore);
    expect(within(detail()).getByRole("button", { name: /Later/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps working in memory when storage is unavailable", async () => {
    vi.stubGlobal("indexedDB", undefined);
    resetConnectionForTests();
    await analyzeWith();
    expect(within(list()).getAllByRole("option").length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Not saved"));
  });

  it("deletes saved data on request", async () => {
    const { u } = await analyzeWith();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved in this browser"));
    await u.click(screen.getByRole("button", { name: "Analysis menu" }));
    await u.click(await screen.findByRole("menuitem", { name: /Delete saved analysis/ }));
    await u.click(await screen.findByRole("button", { name: /^Delete$/ }));
    expect(await screen.findByText("Saved analysis deleted from this browser.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Analyze a screenplay/ })).toBeInTheDocument();
    cleanup();
    resetConnectionForTests();
    mockFetch();
    render(<App />);
    expect(await screen.findByRole("heading", { name: /Analyze a screenplay/ })).toBeInTheDocument();
  });

  it("imports a saved analysis JSON locally without uploading it", async () => {
    const { calls } = mockFetch();
    const u = user();
    render(<App />);
    const doc = sampleDocWithAi();
    await u.upload(await screen.findByTestId("import-input"), file("saved.json", JSON.stringify(doc), "application/json"));
    await screen.findByRole("tablist", { name: "Ranking" }, { timeout: 10_000 });
    expect(calls.some((c) => c.url === "/api/analyze")).toBe(false);
    expect(await screen.findByText(/Nothing was uploaded/)).toBeInTheDocument();
  });

  it("refuses an invalid import", async () => {
    mockFetch();
    const u = user();
    render(<App />);
    await u.upload(await screen.findByTestId("import-input"), file("bad.json", JSON.stringify({ schema_version: "x" }), "application/json"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not a FrameCue analysis/);
  });
});
