/** Local Sourcecado API only. Provider credentials never enter chat or storage here. */
declare const __CLUB_DEV_TOKEN__: string;
export async function monidRequest(path: string, method = "GET", body?: unknown): Promise<any> {
  const base = window.__CLUB_HTTP__ || (import.meta as ImportMeta & { env?: { VITE_CLUB_HTTP?: string } }).env?.VITE_CLUB_HTTP || "";
  const token = window.__CLUB_API_TOKEN__ || (typeof __CLUB_DEV_TOKEN__ === "string" ? __CLUB_DEV_TOKEN__ : "");
  const response = await fetch(`${base}/v1/${path}`, {
    method, headers: { "X-Club-Token": token, "Content-Type": "application/json" },
    ...(body !== undefined ? {body:JSON.stringify(body)} : {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The local Monid request could not be completed.");
  return result;
}
