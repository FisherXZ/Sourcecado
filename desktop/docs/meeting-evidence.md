# Person meeting evidence

Status: active-stack engineering reference for issue #68.

Calendar events and Granola meetings are stored locally by stable `(provider, provider_id)` identity. The normalized record keeps timestamps, safe participant identity, a Source Reference, note availability, and the current association state.

Only one unconflicted exact participant-email match attaches automatically. Name-only, conflicting, duplicate-email, or multi-person matches create review proposals. The director can attach or reject a proposal from the Person View; connector content never makes that decision.

Refresh is idempotent and source-independent. Calendar and Granola are read through list operations only, and a failure from one source does not delete or block evidence from the other. Existing attached records update in place and use a stable external event key so the Person File timeline is not duplicated.

Meeting records and note excerpts are untrusted evidence. They do not grant tools, approvals, connector writes, or any other authority. Attached records feed the Living Brief; an attached meeting without notes creates an explicit `meeting notes` Knowledge Gap.
