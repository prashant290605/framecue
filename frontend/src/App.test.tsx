import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { resetConnectionForTests } from "./lib/storage";
import { timecode } from "./lib/time";
import { file, json, mockFetch, sampleDoc, sampleDocWithAi } from "./test/helpers";

const user = () => userEvent.setup();
const ready = () => screen.findByRole("region", { name: "Recommendations" }, { timeout: 10_000 });

async function analyzeWith(doc = sampleDoc()) {
  const fetchMock = mockFetch({ analyze: () => json(doc) });
  const u = user();
  render(<App />);
  const input = await screen.findByTestId("screenplay-input");
  await u.upload(input, file("pack.zip", "PK"));
  await u.click(screen.getByRole("button", { name: /^Analyze/ }));
  await ready();
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
    expect(screen.queryByRole("region", { name: "Recommendations" })).not.toBeInTheDocument();
  });

  it("runs the fictional sample through the real API contract", async () => {
    const { calls } = mockFetch({ analyze: () => json(sampleDoc()) });
    const u = user();
    render(<App />);
    await u.click(await screen.findByRole("button", { name: /Try the fictional sample/ }));
    await ready();
    const sent = calls.find((c) => c.url === "/api/analyze")!.init!.body as FormData;
    expect((sent.getAll("files") as File[]).map((f) => f.name)).toEqual([
      "harbor-light-01.txt",
      "harbor-light-02.txt",
      "harbor-light-03.txt",
    ]);
    expect(sent.get("ai_bundle")).toBeNull(); // no published sample bundle: plain sample only
    expect(calls.some((c) => c.url.endsWith(".ai-bundle.json"))).toBe(false); // never requested
  });

  it("sends the fictional sample's published AI bundle and labels its reviews as genuine", async () => {
    const bundle = { format: "promolens-ai-bundle-v1", entries: {}, failures: {} };
    const { calls } = mockFetch({ analyze: () => json(sampleDocWithAi()), sampleBundle: () => json(bundle) });
    const u = user();
    render(<App />);
    await u.click(await screen.findByRole("button", { name: /Try the fictional sample/ }));
    await ready();
    const sent = calls.find((c) => c.url === "/api/analyze")!.init!.body as FormData;
    expect((sent.get("ai_bundle") as File).name).toBe("harbor-light.ai-bundle.json");
    expect(screen.getByRole("tab", { name: /AI-reviewed/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(/Genuine reviews by/)).toHaveTextContent(/generated offline for this fictional sample/);
  });
});

describe("views and provenance", () => {
  it("shows the structural ranking plainly when there are no AI reviews", async () => {
    const { doc } = await analyzeWith();
    expect(screen.queryByRole("tablist", { name: "Ranking" })).not.toBeInTheDocument(); // one ranking only
    expect(screen.queryByRole("button", { name: /AI reviewed/ })).not.toBeInTheDocument(); // no "0 / N" chip
    expect(screen.getByText(/AI review not available/)).toBeInTheDocument();
    const options = within(list()).getAllByRole("option");
    expect(options.length).toBe(doc.views.coverage.primaries);
    expect(within(detail()).getByText("Structural score")).toBeInTheDocument();
    expect(within(detail()).getByText(/^Starts at /)).toBeInTheDocument(); // reasons in plain words
    expect(within(detail()).queryByText("AI score")).not.toBeInTheDocument();
    expect(within(detail()).getByRole("region", { name: "Score" })).not.toHaveTextContent(/_v1|heuristic/); // ids live in Details
  });

  it("shows genuine AI judgements on the 0–100 scale with evidence", async () => {
    const doc = sampleDocWithAi();
    await analyzeWith(doc);
    expect(screen.getByRole("tab", { name: /AI-reviewed/ })).toHaveAttribute("aria-selected", "true");
    const topId = doc.views.ai.order[0];
    const j = doc.ai_evaluations[topId];
    const d = detail();
    expect(within(d).getByText("AI score")).toBeInTheDocument();
    expect(within(d).getByText(String(j.weighted_score))).toBeInTheDocument();
    expect(within(d).getByText("/ 100")).toBeInTheDocument();
    expect(within(d).getByText(j.hook_explanation)).toBeInTheDocument();
    expect(within(d).getByText(j.cliffhanger_explanation)).toBeInTheDocument();
    expect(within(d).getAllByRole("button", { name: j.hook_event_ids[0] }).length).toBeGreaterThan(0);
    expect(screen.getByText(/candidates reviewed by AI so far/)).toBeInTheDocument();
  });

  it("marks AI availability in the full show without mixing scores", async () => {
    const doc = sampleDocWithAi();
    const { u } = await analyzeWith(doc);
    await u.click(screen.getByRole("tab", { name: /Full show/ }));
    expect(within(list()).getAllByLabelText("AI review available").length).toBeGreaterThan(0);
    expect(within(detail()).queryByText("AI score")).not.toBeInTheDocument();
    expect(within(detail()).getByText("Structural score")).toBeInTheDocument();
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
    await u.click(within(detail()).getByRole("button", { name: /Add note/ }));
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

  it("summarises AI coverage when some candidates have reviews", async () => {
    const doc = sampleDocWithAi();
    const { u } = await analyzeWith(doc);
    await u.click(screen.getByRole("button", { name: /AI reviewed/ }));
    expect(await screen.findByText(`${doc.coverage.ai_evaluated} of ${doc.coverage.total_candidates} candidates have a validated AI review. The rest are ranked by structure only.`)).toBeInTheDocument();
  });
});

describe("persistence", () => {
  it("restores the analysis and decisions after a refresh", async () => {
    const { u } = await analyzeWith();
    await u.click(within(list()).getAllByRole("option")[2]);
    await u.click(within(detail()).getByRole("button", { name: /Later/ }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^Saved$/));
    await act(() => new Promise((r) => setTimeout(r, 450))); // debounced review save
    const selectedBefore = within(detail()).getByRole("heading", { level: 2 }).textContent;
    cleanup(); // unmount, like closing the tab
    resetConnectionForTests(); // like a page reload: new connection, same database
    mockFetch();
    render(<App />);
    await ready();
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
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^Saved$/));
    await u.click(screen.getByRole("button", { name: "Analysis menu" }));
    await u.click(await screen.findByRole("menuitem", { name: /Delete analysis/ }));
    await u.click(await screen.findByRole("button", { name: /^Delete$/ }));
    expect(await screen.findByText("Analysis deleted from this browser.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Analyze a screenplay/ })).toBeInTheDocument();
    cleanup();
    resetConnectionForTests();
    mockFetch();
    render(<App />);
    expect(await screen.findByRole("heading", { name: /Analyze a screenplay/ })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Recent analyses" })).not.toBeInTheDocument();
  });

  it("lists saved analyses to reopen, rename and delete without uploading again", async () => {
    const { u, fetchMock } = await analyzeWith();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^Saved$/));
    await u.click(screen.getByRole("button", { name: "Analysis menu" }));
    await u.click(await screen.findByRole("menuitem", { name: /New analysis/ }));
    const recent = await screen.findByRole("list", { name: "Recent analyses" });
    expect(recent).toHaveTextContent("pack.zip");
    await u.click(within(recent).getByRole("button", { name: "Rename pack.zip" }));
    const field = within(recent).getByLabelText("Analysis name");
    await u.clear(field);
    await u.type(field, "Season one{Enter}");
    await waitFor(() => expect(within(recent).getByText("Season one")).toBeInTheDocument());
    await u.click(within(recent).getByText("Season one"));
    await ready();
    expect(screen.getByRole("heading", { level: 1, name: "Season one" })).toBeInTheDocument();
    expect(fetchMock.calls.filter((c) => c.url === "/api/analyze")).toHaveLength(1); // reopened, not re-analysed
    await u.click(screen.getByRole("button", { name: "Analysis menu" }));
    await u.click(await screen.findByRole("menuitem", { name: /New analysis/ }));
    const again = await screen.findByRole("list", { name: "Recent analyses" });
    await u.click(within(again).getByRole("button", { name: "Delete Season one" }));
    await u.click(within(again).getByRole("button", { name: "Confirm delete Season one" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Recent analyses" })).not.toBeInTheDocument());
  });

  it("offers the saved analysis when the same files are uploaded again", async () => {
    const { u, fetchMock } = await analyzeWith();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^Saved$/));
    await u.click(screen.getByRole("button", { name: "Analysis menu" }));
    await u.click(await screen.findByRole("menuitem", { name: /New analysis/ }));
    await u.upload(await screen.findByTestId("screenplay-input"), file("renamed.zip", "PK"));
    await u.click(screen.getByRole("button", { name: /^Analyze/ }));
    expect(await screen.findByText(/Already analyzed as/)).toHaveTextContent("pack.zip");
    await u.click(screen.getByRole("button", { name: "Open saved" }));
    await ready();
    expect(fetchMock.calls.filter((c) => c.url === "/api/analyze")).toHaveLength(1);
  });

  it("imports a saved analysis JSON locally without uploading it", async () => {
    const { calls } = mockFetch();
    const u = user();
    render(<App />);
    const doc = sampleDocWithAi();
    await u.upload(await screen.findByTestId("import-input"), file("saved.json", JSON.stringify(doc), "application/json"));
    await ready();
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
