// One answer that uses every Markdown shape the thread must format: headings,
// nested lists, a quote, a wide table, inline code and fenced code. The tests
// stream it in prefixes, and the #250 screenshots render the same text.
export const FORMATTED_ANSWER = `# Series A fintech founders

I found 14 people and kept the 3 with a named warm path. The ranking uses **warm path first**, then \`fit_score\`.

## What I checked

- Apollo search for founders at seed to Series A fintechs
  - Dropped 6 people with no current role
  - Kept 8 people with a company site
    - 3 have a mutual contact on the Codeology alumni list
- Gmail threads from the last 90 days
- Drive notes tagged \`sourcing\`

1. Review the three kept people
2. Approve the draft for Maya Chen
   1. Check the subject line
   2. Check the sender

> Maya replied on 12 Oct: "Happy to talk after the demo day." Her email is confirmed.

### Shortlist

| Person | Role | Company | Stage | Email status | Last touch | Warm path | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maya Chen | Founder | Acme Pay | Seed | Found | 12 Oct | Alumni list | Replied once, wants a demo day intro |
| Luis Ortega | CEO | Ledgerly | Series A | Missing | None | None | Surname shown in the source, no email in Apollo |
| Priya Nair | Co-founder | Tillwise | Seed | Found | 3 Oct | Alumni list | Opened the first message twice |

### Ranking code

\`\`\`python
def rank(people, weights):
    """Warm path first, then fit score."""
    scored = []
    for person in people:
        warm = 1 if person["warm_path"] else 0
        score = warm * weights["warm"] + person["fit_score"]  # 0 to 100
        scored.append((score, person["name"]))
    return sorted(scored, reverse=True)
\`\`\`

\`\`\`json
{ "kept": 3, "dropped": 6, "email_found": true, "source": "apollo" }
\`\`\`

Next step: approve the draft for Maya Chen in the thread above.`;
