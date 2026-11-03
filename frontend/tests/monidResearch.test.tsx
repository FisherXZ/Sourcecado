import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PersonFileView } from "../src/PersonFile";
import { livingBrief } from "./livingBrief";

vi.mock("../src/api", async importOriginal => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  getPerson: async () => ({ person: { person_id: "person_1", first_name: "Maya", last_name: "Chen", version: 1, sequence_state: null },
    brief: livingBrief(), timeline: [], versions: [], sourcing_chat: { session_id: "session_1" },
    meeting_evidence: { attached: [], proposed: [], rejected: [] } }),
}));
vi.mock("../src/Tasks", () => ({ TASKS_CHANGED: "sourcecado:tasks-changed", TaskList: () => null }));
vi.mock("../src/EnrichmentPanel", () => ({ EnrichmentPanel: () => null }));
afterEach(() => vi.unstubAllGlobals());

it("lets the director review exact person-bound input and cost before one lookup approval", async () => {
  const calls: Array<{url:string;body:any}> = [];
  const quote = { kind: "monid_research", quote_id: "quote_1", person_id: "person_1", provider: "exa", endpoint: "search",
    input: { body: { query: "Maya Acme recent research", numResults: 3 } }, quoted_cost_usd: "0.01", max_cost_usd: "0.02", currency: "USD" };
  vi.stubGlobal("fetch", vi.fn(async (url, options: RequestInit = {}) => {
    calls.push({url:String(url),body:options.body ? JSON.parse(String(options.body)) : null});
    let body: any = {};
    if (String(url).includes("/catalog")) body = { items: [{ provider:"exa", endpoint:"search", displayName:"Web search", supported:true }] };
    if (String(url).endsWith("/quote")) body = { quote };
    if (String(url).endsWith("/approvals")) body = { item:{id:"approval_1",resource:quote} };
    if (String(url).includes("/inbox/")) body = { ok:true,result:{status:"completed",output:{title:"Sample source"},actual_cost_usd:null,untrusted:true,approval_id:"approval_1"} };
    return {ok:true,json:async()=>body};
  }));
  render(<PersonFileView personId="person_1" />);
  await screen.findByRole("heading", { name:"Monid research" });
  fireEvent.change(screen.getByLabelText("Search Monid catalog"),{target:{value:"web search"}});
  fireEvent.click(screen.getByRole("button",{name:"Find Monid sources"}));
  fireEvent.click(await screen.findByRole("button",{name:"Use exa search"}));
  fireEvent.change(screen.getByLabelText("Research query"),{target:{value:quote.input.body.query}});
  expect(screen.getByLabelText("Maximum USD for this lookup")).toHaveValue(null);
  fireEvent.change(screen.getByLabelText("Maximum USD for this lookup"),{target:{value:"0.02"}});
  fireEvent.click(screen.getByRole("button",{name:"Review Monid lookup"}));
  await screen.findByText("USD 0.01",{exact:true});
  expect(calls.some(call=>call.url.includes("/inbox/"))).toBe(false);
  expect(screen.getByText(/"numResults": 3/)).toBeVisible();
  fireEvent.click(screen.getByRole("button",{name:"Request lookup approval"}));
  fireEvent.click(await screen.findByRole("button",{name:"Allow one lookup"}));
  await screen.findByText("Actual cost: Unknown");
  expect(screen.getByText("Sample source",{exact:true})).toBeVisible();
  expect(calls.filter(call=>call.url.includes("/inbox/"))).toEqual([{url:expect.stringContaining("/v1/inbox/approval_1"),body:{decision:"allow",scope:"once",actor:"director"}}]);
  expect(screen.queryByRole("button",{name:/Always allow/})).toBeNull();
});

it("shows the person's saved research after reopening without contacting Monid", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    calls.push(String(url));
    const body = String(url).endsWith("/monid/results")
      ? { results: [{ approval_id:"approval_9", status:"completed", remote_provider:"exa", endpoint:"search", actual_cost_usd:"0.01",
          saved_at:"2026-11-02T12:00:00Z", output:{ results:[{ title:"Saved platform post", url:"https://example.test/post", text:"Saved text" }] } }] }
      : {};
    return { ok:true, json:async()=>body };
  }));
  render(<PersonFileView personId="person_1" />);
  expect(await screen.findByText("Saved platform post")).toBeVisible();
  expect(screen.getByText("Actual cost: USD 0.01")).toBeVisible();
  expect(screen.getByText(/^Saved research/)).toBeVisible();
  expect(calls.some(url=>url.includes("monid.ai")||url.includes("/inbox/")||url.includes("/quote"))).toBe(false);
});
