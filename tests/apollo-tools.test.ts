import { getDb } from "@/lib/db";
import {
  apolloEnrichContactArgsSchema,
  apolloEnrichContactTool,
  apolloSearchPeopleArgsSchema,
  apolloSearchPeopleTool,
} from "@/lib/tools/apollo";

const ORIGINAL_APOLLO_KEY = process.env.APOLLO_API_KEY;

describe("apollo tools", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    if (ORIGINAL_APOLLO_KEY === undefined) {
      delete process.env.APOLLO_API_KEY;
    } else {
      process.env.APOLLO_API_KEY = ORIGINAL_APOLLO_KEY;
    }
  });

  describe("apolloSearchPeopleTool", () => {
    it("is an enrich-class tool named apollo_search_people", () => {
      expect(apolloSearchPeopleTool.name).toBe("apollo_search_people");
      expect(apolloSearchPeopleTool.permissionClass).toBe("enrich");
    });

    it("rejects args with neither organizationName nor personTitles", () => {
      expect(apolloSearchPeopleArgsSchema.safeParse({}).success).toBe(false);
    });

    it("throws a clean error when APOLLO_API_KEY is not configured", async () => {
      delete process.env.APOLLO_API_KEY;
      await expect(
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/APOLLO_API_KEY/);
    });

    it("returns mapped people on a successful Apollo response", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          people: [
            {
              name: "Jane Doe",
              title: "VP Eng",
              organization: { name: "Acme" },
              linkedin_url: "https://linkedin.com/in/janedoe",
              email: "jane@acme.com",
            },
          ],
        }),
      });
      vi.stubGlobal("fetch", fetchMock);

      const result = await apolloSearchPeopleTool.execute(
        { organizationName: "Acme" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );

      expect(result.people).toEqual([
        {
          name: "Jane Doe",
          title: "VP Eng",
          organizationName: "Acme",
          linkedinUrl: "https://linkedin.com/in/janedoe",
          email: "jane@acme.com",
        },
      ]);
      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.apollo.io/api/v1/mixed_people/api_search",
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("throws a clean error when fetch itself rejects (network failure)", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND api.apollo.io")));
      await expect(
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/ENOTFOUND/);
    });

    it("throws a clean error when the response body is not valid JSON", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => {
            throw new SyntaxError("Unexpected token in JSON");
          },
        }),
      );
      await expect(
        apolloSearchPeopleTool.execute(
          { organizationName: "Acme" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/Unexpected token/);
    });
  });

  describe("apolloEnrichContactTool", () => {
    it("is an enrich-class tool named apollo_enrich_contact", () => {
      expect(apolloEnrichContactTool.name).toBe("apollo_enrich_contact");
      expect(apolloEnrichContactTool.permissionClass).toBe("enrich");
    });

    it("rejects args with neither email nor firstName+lastName", () => {
      expect(apolloEnrichContactArgsSchema.safeParse({ firstName: "Jane" }).success).toBe(false);
    });

    it("throws a clean error when APOLLO_API_KEY is not configured", async () => {
      delete process.env.APOLLO_API_KEY;
      await expect(
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/APOLLO_API_KEY/);
    });

    it("returns a mapped contact on a successful Apollo response", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({
            person: {
              name: "Jane Doe",
              title: "VP Eng",
              organization: { name: "Acme" },
              linkedin_url: "https://linkedin.com/in/janedoe",
              email: "jane@acme.com",
              phone_numbers: [{ raw_number: "+1-555-0100" }],
            },
          }),
        }),
      );

      const result = await apolloEnrichContactTool.execute(
        { email: "jane@acme.com" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );

      expect(result).toEqual({
        name: "Jane Doe",
        title: "VP Eng",
        organizationName: "Acme",
        linkedinUrl: "https://linkedin.com/in/janedoe",
        email: "jane@acme.com",
        phone: "+1-555-0100",
      });
    });

    it("throws a clean error when fetch itself rejects (network failure)", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ENOTFOUND api.apollo.io")));
      await expect(
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/ENOTFOUND/);
    });

    it("throws a clean error when the response body is not valid JSON", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => {
            throw new SyntaxError("Unexpected token in JSON");
          },
        }),
      );
      await expect(
        apolloEnrichContactTool.execute(
          { email: "jane@acme.com" },
          { db: getDb(), runId: 0, parentStepId: 0 },
        ),
      ).rejects.toThrow(/Unexpected token/);
    });
  });
});
