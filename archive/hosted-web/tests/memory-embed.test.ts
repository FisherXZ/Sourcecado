import {
  embedText,
  MEMORY_EMBEDDING_DIMENSIONS,
  toVectorLiteral,
  usesRealEmbeddings,
} from "@/lib/memory/embed";
import type { Sql } from "@/lib/tools/types";

// Placeholder db — not used in fallback path; real path uses callModel
const fakeDb = {} as Sql;

// ---------------------------------------------------------------------------
// MEMORY_EMBEDDING_DIMENSIONS
// ---------------------------------------------------------------------------

describe("MEMORY_EMBEDDING_DIMENSIONS", () => {
  it("is 1536", () => {
    expect(MEMORY_EMBEDDING_DIMENSIONS).toBe(1536);
  });
});

// ---------------------------------------------------------------------------
// toVectorLiteral
// ---------------------------------------------------------------------------

describe("toVectorLiteral()", () => {
  it("formats a two-element vector for pgvector", () => {
    expect(toVectorLiteral([0.5, -0.25])).toBe("[0.5,-0.25]");
  });

  it("handles an empty vector", () => {
    expect(toVectorLiteral([])).toBe("[]");
  });
});

// ---------------------------------------------------------------------------
// usesRealEmbeddings()
// ---------------------------------------------------------------------------

describe("usesRealEmbeddings()", () => {
  const saveRestore = () => {
    const saved = process.env.OPENAI_API_KEY;
    return () => {
      if (saved === undefined) {
        delete process.env.OPENAI_API_KEY;
      } else {
        process.env.OPENAI_API_KEY = saved;
      }
    };
  };

  it("returns true when OPENAI_API_KEY is a non-empty string", () => {
    const restore = saveRestore();
    process.env.OPENAI_API_KEY = "sk-test";
    try {
      expect(usesRealEmbeddings()).toBe(true);
    } finally {
      restore();
    }
  });

  it("returns false when OPENAI_API_KEY is unset", () => {
    const restore = saveRestore();
    delete process.env.OPENAI_API_KEY;
    try {
      expect(usesRealEmbeddings()).toBe(false);
    } finally {
      restore();
    }
  });

  it("returns false when OPENAI_API_KEY is an empty string", () => {
    const restore = saveRestore();
    process.env.OPENAI_API_KEY = "";
    try {
      expect(usesRealEmbeddings()).toBe(false);
    } finally {
      restore();
    }
  });

  it("returns false when OPENAI_API_KEY is whitespace only", () => {
    const restore = saveRestore();
    process.env.OPENAI_API_KEY = "   ";
    try {
      expect(usesRealEmbeddings()).toBe(false);
    } finally {
      restore();
    }
  });
});

// ---------------------------------------------------------------------------
// embedText() — fallback path (OPENAI_API_KEY unset)
// ---------------------------------------------------------------------------

describe("embedText() fallback path", () => {
  let savedKey: string | undefined;

  beforeEach(() => {
    savedKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
  });

  afterEach(() => {
    if (savedKey !== undefined) {
      process.env.OPENAI_API_KEY = savedKey;
    }
  });

  it("returns a vector of exactly 1536 dimensions", async () => {
    const vec = await embedText(fakeDb, "hello world");
    expect(vec).toHaveLength(1536);
  });

  it("is deterministic: same input → identical vector", async () => {
    const vec1 = await embedText(fakeDb, "deterministic test phrase");
    const vec2 = await embedText(fakeDb, "deterministic test phrase");
    expect(vec1).toEqual(vec2);
  });

  it("is L2-normalized: magnitude ≈ 1 for non-empty text", async () => {
    const vec = await embedText(fakeDb, "some meaningful text for normalization");
    const mag = Math.sqrt(vec.reduce((sum, v) => sum + v * v, 0));
    expect(mag).toBeCloseTo(1, 6);
  });

  it("returns a zero vector for empty string (no tokens)", async () => {
    const vec = await embedText(fakeDb, "");
    expect(vec).toHaveLength(1536);
    expect(vec.every((v) => v === 0)).toBe(true);
  });

  it("returns a zero vector for text with no alphanumeric tokens", async () => {
    const vec = await embedText(fakeDb, "!!! ??? --- ###");
    expect(vec).toHaveLength(1536);
    expect(vec.every((v) => v === 0)).toBe(true);
  });

  it("has no NaN values", async () => {
    const empty = await embedText(fakeDb, "");
    const nonEmpty = await embedText(fakeDb, "no nan values");
    expect(empty.every((v) => !Number.isNaN(v))).toBe(true);
    expect(nonEmpty.every((v) => !Number.isNaN(v))).toBe(true);
  });

  it("produces different vectors for different inputs", async () => {
    const vec1 = await embedText(fakeDb, "apple orange banana");
    const vec2 = await embedText(fakeDb, "quantum physics relativity");
    expect(vec1).not.toEqual(vec2);
  });
});

// ---------------------------------------------------------------------------
// embedText() — real path branch selection (no network call)
// ---------------------------------------------------------------------------

describe("embedText() real path branch selection", () => {
  it("usesRealEmbeddings() is true when OPENAI_API_KEY is set, confirming real path selection", () => {
    const saved = process.env.OPENAI_API_KEY;
    process.env.OPENAI_API_KEY = "sk-fake-key-for-branch-test";
    try {
      // The function reads OPENAI_API_KEY at call time — presence means real path
      expect(usesRealEmbeddings()).toBe(true);
    } finally {
      if (saved === undefined) {
        delete process.env.OPENAI_API_KEY;
      } else {
        process.env.OPENAI_API_KEY = saved;
      }
    }
  });
});
