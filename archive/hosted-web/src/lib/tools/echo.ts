import { z } from "zod";
import type { Tool } from "./types";

export const echoArgsSchema = z.object({ text: z.string() });
export type EchoArgs = z.infer<typeof echoArgsSchema>;

export const echoTool: Tool<EchoArgs, { echoed: string }> = {
  name: "echo",
  description: "Echo back the provided text. A reference tool for the harness.",
  permissionClass: "read",
  argsSchema: echoArgsSchema,
  async execute(args) {
    return { echoed: args.text };
  },
};
