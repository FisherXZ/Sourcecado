import { expect, it } from "vitest";
import { approvalView } from "../src/chat/approvalModel";

it("shows exact Monid input and quote", () => {
  const part = { toolName: "monid_research", args: { quote_id: "quote_1" }, providerMetadata: { sourcecado: { resource: {
    kind: "monid_research", provider: "exa", endpoint: "search", person_id: "person_1",
    input: { body: { query: "Maya Acme", numResults: 3 } }, quoted_cost_usd: "0.01", max_cost_usd: "0.02", currency: "USD",
  } } } } as Parameters<typeof approvalView>[0];
  const view = approvalView(part);
  expect(view.rows.find(row => row.label === "Provider")?.value).toBe("exa");
  expect(view.rows.find(row => row.label === "Endpoint")?.value).toBe("search");
  expect(view.rows.find(row => row.label === "Request data")?.value).toContain('"numResults": 3');
  expect(view.rows.find(row => row.label === "Quoted cost")?.value).toBe("USD 0.01");
});
