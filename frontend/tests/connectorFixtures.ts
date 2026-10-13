import type { Connector } from "../src/api";

export function connector(overrides: Partial<Connector> & Pick<Connector, "id" | "title">): Connector {
  return {
    id: overrides.id,
    title: overrides.title,
    description: `${overrides.title} source connection.`,
    status: "available",
    catalogGroup: "available",
    email: null,
    requiredScopes: [],
    missingScopes: [],
    health: {
      category: "setup_required",
      label: "Not connected",
      message: "This connection has not been set up yet.",
    },
    recovery: {
      category: "connect",
      actionLabel: "Connect",
      message: "Start authorization, then return to this detail page.",
    },
    verified: false,
    supportedActions: [],
    availableActions: ["connect"],
    repairRoute: `#/connections/${overrides.id}`,
    authorizationGroup: overrides.id === "granola" ? "granola" : null,
    ...overrides,
  };
}

// The three states #181 requires both screens to describe identically.

export const unconfiguredGmail = connector({
  id: "gmail",
  title: "Gmail",
  requiredScopes: ["Read Gmail messages", "Create Gmail drafts"],
  missingScopes: ["Read Gmail messages", "Create Gmail drafts"],
  authorizationGroup: "google",
});

export const configuredUnverifiedApollo = connector({
  id: "apollo",
  title: "Apollo",
  status: "connected",
  catalogGroup: "connected",
  health: {
    category: "healthy",
    label: "Configured",
    message: "This connection is ready to use.",
  },
  recovery: null,
  verified: false,
  availableActions: ["view_guidance"],
});

export const reportedErrorGranola = connector({
  id: "granola",
  title: "Granola",
  status: "failed",
  catalogGroup: "connected",
  health: {
    category: "error",
    label: "Connection failed",
    message: "Granola did not respond.",
  },
  recovery: {
    category: "retry",
    actionLabel: "Reconnect",
    message: "Reconnect Granola to restore access.",
  },
  availableActions: ["reconnect", "disconnect"],
});

export const verifiedDrive = connector({
  id: "drive",
  title: "Google Drive",
  status: "connected",
  catalogGroup: "connected",
  email: "operator@example.com",
  health: { category: "healthy", label: "Ready", message: "This connection is ready to use." },
  recovery: null,
  verified: true,
  availableActions: ["disconnect"],
  authorizationGroup: "google",
});
