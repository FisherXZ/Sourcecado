import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createDatabase, type MemoryDatabase } from "../src/db.js";
import { resolveAccessContext, type ActorType } from "../src/read-service.js";

const tempDirs: string[] = [];

function tempDb(): MemoryDatabase {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-permissions-test-"));
  tempDirs.push(dir);
  return createDatabase(join(dir, ".sourcyavo", "memory.db"));
}

// Exercises the production allowlist resolver through its public AccessContext
// surface, so the permission predicate (including access = 'read') lives in
// exactly one place.
function resolveAllowedSourceIds(
  db: MemoryDatabase,
  actorType: ActorType,
  actorId: string
): string[] {
  return resolveAccessContext(db, { actorType, actorId }).allowedSourceIds;
}

function grant(
  db: MemoryDatabase,
  principalType: string,
  principalId: string,
  sourceId: string
): void {
  db.prepare(
    "insert into source_permissions (principal_type, principal_id, source_id) values (?, ?, ?)"
  ).run(principalType, principalId, sourceId);
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("resolveAllowedSourceIds", () => {
  it("returns an empty scope when the principal has no permissions", () => {
    const db = tempDb();
    expect(resolveAllowedSourceIds(db, "user", "alice")).toEqual([]);
    db.close();
  });

  it("returns a single granted source id", () => {
    const db = tempDb();
    grant(db, "user", "alice", "spring-2026/apollo");
    expect(resolveAllowedSourceIds(db, "user", "alice")).toEqual(["spring-2026/apollo"]);
    db.close();
  });

  it("returns multiple granted source ids", () => {
    const db = tempDb();
    grant(db, "user", "alice", "spring-2026/apollo");
    grant(db, "user", "alice", "spring-2026/cold-emailing");
    expect(resolveAllowedSourceIds(db, "user", "alice")).toEqual([
      "spring-2026/apollo",
      "spring-2026/cold-emailing"
    ]);
    db.close();
  });

  it("returns an empty scope for an unknown principal", () => {
    const db = tempDb();
    grant(db, "user", "alice", "spring-2026/apollo");
    expect(resolveAllowedSourceIds(db, "user", "mallory")).toEqual([]);
    db.close();
  });

  it("keeps two principals' scopes disjoint", () => {
    const db = tempDb();
    grant(db, "user", "alice", "spring-2026/apollo");
    grant(db, "oauth_client", "bob-app", "fall-2026/referrals");

    expect(resolveAllowedSourceIds(db, "user", "alice")).toEqual(["spring-2026/apollo"]);
    expect(resolveAllowedSourceIds(db, "oauth_client", "bob-app")).toEqual([
      "fall-2026/referrals"
    ]);

    db.close();
  });

  it("excludes grants that are not read access", () => {
    const db = tempDb();
    grant(db, "user", "alice", "spring-2026/apollo");
    db.prepare(
      "insert into source_permissions (principal_type, principal_id, source_id, access) values (?, ?, ?, ?)"
    ).run("user", "alice", "spring-2026/restricted", "write");

    expect(resolveAllowedSourceIds(db, "user", "alice")).toEqual([
      "spring-2026/apollo"
    ]);

    db.close();
  });
});
