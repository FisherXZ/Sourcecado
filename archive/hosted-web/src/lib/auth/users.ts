import type { Sql } from "../tools/types";

export interface AppUser {
  id: number;
  googleSub: string;
  email: string;
  name: string | null;
  imageUrl: string | null;
}

export interface UpsertUserInput {
  googleSub: string;
  email: string;
  name?: string | null;
  imageUrl?: string | null;
}

// Called on every sign-in. Keyed on google_sub (Google's stable subject claim)
// rather than email, so a director whose Workspace address changes keeps the
// same users.id — and therefore keeps every run, chat session, and memory
// grant already attributed to them.
export async function upsertUser(db: Sql, input: UpsertUserInput): Promise<AppUser> {
  const [row] = await db<
    { id: string; google_sub: string; email: string; name: string | null; image_url: string | null }[]
  >`
    INSERT INTO users (google_sub, email, name, image_url)
    VALUES (${input.googleSub}, ${input.email}, ${input.name ?? null}, ${input.imageUrl ?? null})
    ON CONFLICT (google_sub) DO UPDATE
      SET email      = EXCLUDED.email,
          name       = EXCLUDED.name,
          image_url  = EXCLUDED.image_url,
          updated_at = now()
    RETURNING id, google_sub, email, name, image_url
  `;
  return {
    id: Number(row.id),
    googleSub: row.google_sub,
    email: row.email,
    name: row.name,
    imageUrl: row.image_url,
  };
}
