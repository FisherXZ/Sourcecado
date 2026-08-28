# Effective model tool catalog

Status: active-stack engineering reference.

The schemas sent to a model for a Sourcecado run are the sole capability surface for that run. The catalog is rebuilt immediately before scheduled and interactive turns from:

1. the registered Sourcecado and connected MCP schemas;
2. the runtime-owned sourcing or buddy narrowing policy;
3. any validated persona declaration, which may narrow but never broaden that policy;
4. current connector availability;
5. active Sourcecado workspace grants, including read/write and shell authority; and
6. the schema-level approval class derived from the runtime permission policy.

Persona prose and frontmatter do not grant tools. The built-in personas declare no tools. A custom declaration is treated only as an additional allowlist restriction, and an unknown or misspelled name fails composition clearly.

Filesystem tools require an active workspace grant. Write tools additionally require a read-write grant. Shell tools require a read-write grant with shell authority. `request_directory` remains available so the operator can deliberately create the contract; it grants no filesystem or shell authority by itself.

Each effective schema keeps its registered shape and receives a content-free approval annotation: `auto`, `approval_required`, or `conditional`. Provider calls outside the supplied catalog are persisted as failed tool results and never execute.

Prompt diagnostics expose only ordered effective tool names and approval classes. They never include descriptions, parameter schemas, arguments, connector output, credentials, or prompt prose.
