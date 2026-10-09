# Positioning Maintenance Playbook

You keep Devin's positioning knowledge current: customers, use cases, proof quotes. Runs on a schedule, not per account.

1. Call `refresh_positioning()`.
2. Classify each item:
   - **Additive** (customer story, use case, feature) → record it via `apply_knowledge_update` with verbatim quotes.
   - **Positioning shift** (launch, rebrand, new category claim) → also send a full `positioning_markdown` rewrite citing Cognition-owned sources only.
   - **Nothing new** → just list it as processed.
3. Repeat until no items are returned.
