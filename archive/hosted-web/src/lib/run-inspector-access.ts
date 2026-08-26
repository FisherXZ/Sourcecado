export function isRunInspectorEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.NODE_ENV !== "production" && env.SOURCECADO_ENABLE_RUN_INSPECTOR === "true";
}
