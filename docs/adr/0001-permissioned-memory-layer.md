# Permissioned Memory Layer

SourcyAvo will not rely on an LLM prompt to protect sensitive club context. Source material should be separated before indexing, with restricted material isolated at the file/source level and again at the vector or embedding metadata level, so retrieval only exposes context the current user is allowed to see. This is a trust requirement for the sourcing memory system, not the product's top-level promise.

**Considered Options**

- Use one shared memory index and ask the model not to reveal restricted content.
- Use permission filtering only after retrieval.
- Use file/source-level separation plus vector metadata filtering before retrieval.

**Consequences**

This makes ingestion and retrieval more complex, but it lowers the risk of leaking officer-only, sourcing-only, or otherwise restricted club context.
