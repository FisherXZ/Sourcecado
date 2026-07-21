import { z } from "zod";
import type { Tool } from "./types";

const TAVILY_SEARCH_URL = "https://api.tavily.com/search";

export const webSearchArgsSchema = z.object({
  query: z.string().min(1),
  maxResults: z.number().int().positive().max(10).optional(),
});
export type WebSearchArgs = z.infer<typeof webSearchArgsSchema>;

export interface WebSearchResultItem {
  title: string;
  url: string;
  content: string;
  score: number | null;
}

export interface WebSearchResult {
  results: WebSearchResultItem[];
}

interface TavilySearchResponse {
  results?: Array<{ title?: string; url?: string; content?: string; score?: number }>;
}

export const webSearchTool: Tool<WebSearchArgs, WebSearchResult> = {
  name: "web_search",
  description:
    "Search the web via Tavily. Returns ranked results with title/url/content snippet. Use when memory doesn't cover something and current external information is needed.",
  permissionClass: "enrich",
  argsSchema: webSearchArgsSchema,
  async execute(args) {
    const apiKey = process.env.TAVILY_API_KEY;
    if (!apiKey) {
      throw new Error("TAVILY_API_KEY is not configured.");
    }

    const res = await fetch(TAVILY_SEARCH_URL, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        query: args.query,
        max_results: args.maxResults ?? 5,
      }),
      signal: AbortSignal.timeout(15_000),
    });

    if (!res.ok) {
      throw new Error(`Tavily search failed: ${res.status} ${res.statusText}`);
    }

    const data = (await res.json()) as TavilySearchResponse;
    const results: WebSearchResultItem[] = (data.results ?? []).map((r) => ({
      title: r.title ?? "",
      url: r.url ?? "",
      content: r.content ?? "",
      score: typeof r.score === "number" ? r.score : null,
    }));
    return { results };
  },
};
