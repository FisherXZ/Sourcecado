import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { buildSourcingMemoryAnswer } from "../src/answer.js";
import { openMemoryDatabase, type MemoryDatabase } from "../src/db.js";
import { ingestFolder } from "../src/ingest.js";
import type { SourceScope } from "../src/read-service.js";
import { refreshMemory } from "../src/refresh.js";

const tempDirs: string[] = [];

function tempDbPath(): string {
  const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-test-"));
  tempDirs.push(dir);
  return join(dir, ".sourcyavo", "memory.db");
}

// Full-scope helper: grant every indexed source so these legacy tests stay
// scope-agnostic and keep exercising the ranking/answer logic.
function fullScope(db: MemoryDatabase): SourceScope {
  const rows = db
    .prepare("select source_id from source_records where source_id is not null")
    .all() as Array<{ source_id: string }>;
  return { allowedSourceIds: rows.map((row) => row.source_id) };
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("no-memory answer path", () => {
  it("returns all required sections with clear no-source language", () => {
    const db = openMemoryDatabase(tempDbPath());
    const output = buildSourcingMemoryAnswer(db, "Who needs follow-up?", fullScope(db));

    expect(output).toContain("Answer:");
    expect(output).toContain("Evidence:");
    expect(output).toContain("Gaps:");
    expect(output).toContain("Next Action:");
    expect(output).toContain("I do not have any indexed sourcing memory yet");
    expect(output).toContain("No sources found.");
    expect(output).toContain("Run `sourcyavo ingest seed-data/`");

    db.close();
  });
});

describe("sourcing memory answers", () => {
  it("answers with accepted facts and citations for relevant memory", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-fixture-"));
    tempDirs.push(dir);
    writeFileSync(
      join(dir, "tracker.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Miguel Alvarez,Civic Data Lab,AI safety,contacted,interested,Asked for intro.,yes,Need intro to student organizer",
        "Priya Shah,Robotics Guild,robotics,responded,declined,Too busy this semester.,no,"
      ].join("\n")
    );
    const db = openMemoryDatabase(join(dir, ".sourcyavo", "memory.db"));
    ingestFolder(db, dir);
    await refreshMemory(db);

    const output = buildSourcingMemoryAnswer(db, "Who needs follow-up for AI safety?", fullScope(db));

    expect(output).toContain("Answer:");
    expect(output).toContain("Miguel Alvarez");
    expect(output).toContain("needs follow-up");
    expect(output).toContain("Evidence:");
    expect(output).toContain("tracker.csv#row-1");
    expect(output).toContain("Gaps:");
    expect(output).toContain("Next Action:");
    db.close();
  });

  it("does not include follow-up contacts from unrelated domains", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-domain-filter-"));
    tempDirs.push(dir);
    writeFileSync(
      join(dir, "mixed.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Miguel Alvarez,Civic Data Lab,AI safety,contacted,interested,Asked for intro.,yes,Need intro to student organizer",
        "Alex Rivera,Open Robotics Collective,robotics,contacted,interested,Met at demo night,yes,send lab tour invite"
      ].join("\n")
    );
    const db = openMemoryDatabase(join(dir, ".sourcyavo", "memory.db"));
    ingestFolder(db, dir);
    await refreshMemory(db);

    const output = buildSourcingMemoryAnswer(db, "Who needs follow-up for AI safety?", fullScope(db));

    expect(output).toContain("Miguel Alvarez needs follow-up");
    expect(output).not.toContain("Alex Rivera needs follow-up");
    expect(output).toContain("Follow up with Miguel Alvarez");
    expect(output).not.toContain("Follow up with Alex Rivera");
    db.close();
  });

  it("answers responded questions from response status facts", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-responded-"));
    tempDirs.push(dir);
    writeFileSync(
      join(dir, "tracker.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Priya Shah,Robotics Guild,robotics,Responded,interested,Asked for the project brief,no,",
        "Alex Rivera,Open Robotics Collective,robotics,Contacted,pending,No response yet,no,"
      ].join("\n")
    );
    const db = openMemoryDatabase(join(dir, ".sourcyavo", "memory.db"));
    ingestFolder(db, dir);
    await refreshMemory(db);

    const output = buildSourcingMemoryAnswer(db, "Who responded?", fullScope(db));

    expect(output).toContain("Priya Shah status is Responded");
    expect(output).toContain("tracker.csv#row-1");
    expect(output).not.toContain("Alex Rivera");
    db.close();
  });

  it("answers no-response questions from ghosted or rejected status facts", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-no-response-"));
    tempDirs.push(dir);
    writeFileSync(
      join(dir, "tracker.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Annette Lapham,Design Partner,design,Rejected/ghosted,not a fit,No reply after follow-up,no,",
        "Priya Shah,Robotics Guild,robotics,Responded,interested,Asked for the project brief,no,"
      ].join("\n")
    );
    const db = openMemoryDatabase(join(dir, ".sourcyavo", "memory.db"));
    ingestFolder(db, dir);
    await refreshMemory(db);

    const output = buildSourcingMemoryAnswer(db, "Who did not respond?", fullScope(db));

    expect(output).toContain("Annette Lapham status is Rejected/ghosted");
    expect(output).toContain("tracker.csv#row-1");
    expect(output).not.toContain("Priya Shah");
    db.close();
  });

  it("does not treat owner or interest metadata as prior Codeology collaboration", () => {
    const db = openMemoryDatabase(tempDbPath());
    db.prepare(
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?)"
    ).run("apollo.csv", "apollo", "Apollo", "csv", "hash", "Alex Duffy codeology owner is Rohan Gulati.");
    db.prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, citation) values (?, ?, ?, ?, ?)"
    ).run(1, 0, "Alex Duffy codeology owner is Rohan Gulati.", "chunk", "apollo.csv#row-1");
    db.prepare(
      "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
    ).run("Alex Duffy", "codeology_owner", "Rohan Gulati", 1, 1, 0.86, "accepted");
    db.prepare(
      "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
    ).run("Alex Duffy", "interest", "Client project", 1, 1, 0.86, "accepted");

    const output = buildSourcingMemoryAnswer(db, "Who worked with Codeology before?", fullScope(db));

    expect(output).toContain("I do not have accepted sourcing facts");
    expect(output).not.toContain("codeology owner");
    expect(output).not.toContain("Client project");
    expect(output).not.toContain("Rohan Gulati");
    db.close();
  });

  it("surfaces candidate and conflicted facts under gaps", () => {
    const db = openMemoryDatabase(tempDbPath());
    db.prepare(
      "insert into source_records (path, source_id, title, source_type, content_hash, raw_text) values (?, ?, ?, ?, ?, ?)"
    ).run("memory.csv", "memory", "Memory", "csv", "hash", "raw");
    db.prepare(
      "insert into memory_chunks (source_record_id, chunk_index, text, chunk_hash, citation) values (?, ?, ?, ?, ?)"
    ).run(1, 0, "Noor may be interested. Maya has conflicting status.", "chunk", "memory.csv#chunk-1");
    db.prepare(
      "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
    ).run("Noor Patel", "status", "maybe interested", 1, 1, 0.62, "candidate");
    db.prepare(
      "insert into semantic_facts (subject, predicate, object, source_record_id, source_chunk_id, confidence, status) values (?, ?, ?, ?, ?, ?, ?)"
    ).run("Maya Chen", "status", "contacted", 1, 1, 0.9, "conflicted");

    const output = buildSourcingMemoryAnswer(db, "What is uncertain?", fullScope(db));

    expect(output).toContain("Gaps:");
    expect(output).toContain("Noor Patel");
    expect(output).toContain("candidate");
    expect(output).toContain("Maya Chen");
    expect(output).toContain("conflicted");
    db.close();
  });

  it("returns no relevant memory instead of unrelated accepted facts", async () => {
    const dir = mkdtempSync(join(tmpdir(), "sourcyavo-answer-unrelated-"));
    tempDirs.push(dir);
    writeFileSync(
      join(dir, "tracker.csv"),
      [
        "contact,organization,domain,status,outcome,notes,needs_follow_up,reason",
        "Miguel Alvarez,Civic Data Lab,AI safety,contacted,interested,Asked for intro.,yes,Need intro"
      ].join("\n")
    );
    const db = openMemoryDatabase(join(dir, ".sourcyavo", "memory.db"));
    ingestFolder(db, dir);
    await refreshMemory(db);

    const output = buildSourcingMemoryAnswer(db, "What happened with quantum hardware?", fullScope(db));

    expect(output).toContain("I do not have accepted sourcing facts");
    expect(output).not.toContain("Miguel Alvarez outcome is interested");
    db.close();
  });
});
