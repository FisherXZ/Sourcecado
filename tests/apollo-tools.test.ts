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

    // Fixture shape captured live from POST /api/v1/mixed_people/api_search on
    // 2026-08-04 (50 records). Every key, type, and value below is one Apollo
    // actually sends — including has_direct_phone being a string ("Yes" or
    // "Maybe: ...") rather than a boolean, and last_name_obfuscated being
    // nullable. Only the person identities are swapped out. The endpoint sends
    // no `name`, `email`, or `linkedin_url` on this plan.
    it("returns mapped people on a successful Apollo response", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          total_entries: 3402,
          people: [
            {
              id: "55d725d1f3e5bb49f90024ff",
              first_name: "Jane",
              last_name_obfuscated: "Do***e",
              title: "VP Eng",
              last_refreshed_at: "2026-07-14T15:24:06.101+00:00",
              has_email: true,
              has_city: true,
              has_state: true,
              has_country: true,
              has_direct_phone: "Yes",
              organization: {
                name: "Acme",
                has_industry: true,
                has_phone: false,
                has_city: true,
              },
            },
            {
              id: "54a5d4697468693442c7e1ab",
              first_name: "Robert",
              last_name_obfuscated: null,
              title: "Strategic Partnerships",
              last_refreshed_at: "2026-06-02T10:11:12.000+00:00",
              has_email: false,
              has_city: true,
              has_state: true,
              has_country: true,
              has_direct_phone: "Maybe: please request direct dial via people/bulk_match",
              organization: { name: "Acme" },
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
          apolloId: "55d725d1f3e5bb49f90024ff",
          firstName: "Jane",
          lastNameObfuscated: "Do***e",
          title: "VP Eng",
          organizationName: "Acme",
          hasEmail: true,
          directPhoneStatus: "Yes",
        },
        {
          apolloId: "54a5d4697468693442c7e1ab",
          firstName: "Robert",
          lastNameObfuscated: null,
          title: "Strategic Partnerships",
          organizationName: "Acme",
          hasEmail: false,
          directPhoneStatus: "Maybe: please request direct dial via people/bulk_match",
        },
      ]);
      expect(fetchMock).toHaveBeenCalledWith(
        "https://api.apollo.io/api/v1/mixed_people/api_search",
        expect.objectContaining({ method: "POST" }),
      );
    });

    // Both assertions here are exact-match: a re-added `email` or `linkedinUrl`
    // member — the bug this ticket fixes — fails them.
    it("degrades to nulls when Apollo omits fields on a person", async () => {
      process.env.APOLLO_API_KEY = "test-key";
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => ({ total_entries: 1, people: [{}] }),
        }),
      );

      const result = await apolloSearchPeopleTool.execute(
        { organizationName: "Acme" },
        { db: getDb(), runId: 0, parentStepId: 0 },
      );

      expect(result.people).toEqual([
        {
          apolloId: null,
          firstName: null,
          lastNameObfuscated: null,
          title: null,
          organizationName: null,
          hasEmail: false,
          directPhoneStatus: null,
        },
      ]);
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
