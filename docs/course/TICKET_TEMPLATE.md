# Guided Ticket: <Outcome-oriented title>

## User problem

Who encounters the problem, what are they trying to do, and what fails or is unnecessarily difficult today?

## Learning objective

What engineering concept should the student understand after completing this change?

## Current behavior

Describe the observable behavior in the frozen course baseline. Include a reproduction or concrete example when possible.

## Acceptance criteria

- [ ] Describe observable behavior, not implementation steps.
- [ ] Include the relevant normal path.
- [ ] Include important failure, permission, empty, or recovery behavior.
- [ ] Name any required compatibility or accessibility behavior.

## Boundaries and non-goals

- In scope:
- Out of scope:
- Product invariants that must remain true:

## Dependencies

- Related tickets or pull requests:
- Required connector or local state:
- External account or OAuth setup:

## Verification

Automated tests that should be added or updated:

Commands the student should run:

```text
make test
make build
```

Replace or narrow these commands when the ticket has a more focused verification path.

## Live behavior demonstration

Required when the ticket changes agent or integration behavior. State the scenario, starting state, operator action, expected result, and evidence to inspect.

## Pull-request checklist

- [ ] The branch contains only work needed for this ticket.
- [ ] Acceptance criteria are satisfied.
- [ ] Appropriate automated tests were added or updated.
- [ ] Relevant verification commands pass locally.
- [ ] The live behavior demonstration passes when required.
- [ ] The PR explains known limits and uncertainty.
- [ ] The AI Accountability Note is complete.
