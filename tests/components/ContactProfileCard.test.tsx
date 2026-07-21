// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { ContactProfileCard } from "@/app/chat/ContactProfileCard";
import type { ContactCardView } from "@/app/chat/stream";

const baseView: ContactCardView = {
  contact: { id: 1, canonicalName: "Jane Smith", role: "PM", organizationName: "Acme Corp" },
  history: [],
  acceptedFacts: [],
  gapFacts: [],
};

describe("ContactProfileCard", () => {
  it("renders identity", () => {
    render(<ContactProfileCard {...baseView} />);
    expect(screen.getByText("Jane Smith")).toBeInTheDocument();
    expect(screen.getByText(/PM/)).toBeInTheDocument();
    expect(screen.getByText(/Acme Corp/)).toBeInTheDocument();
  });

  it("shows a gap indicator instead of hiding missing role/org", () => {
    render(
      <ContactProfileCard
        contact={{ id: 1, canonicalName: "Thin Record", role: null, organizationName: null }}
        history={[]}
        acceptedFacts={[]}
        gapFacts={[]}
      />,
    );
    expect(screen.getByText(/Role not yet known/)).toBeInTheDocument();
    expect(screen.getByText(/Organization not yet known/)).toBeInTheDocument();
  });

  it("shows a no-history message when there is no recorded interaction", () => {
    render(<ContactProfileCard {...baseView} />);
    expect(screen.getByText("No recorded interactions yet.")).toBeInTheDocument();
  });

  it("renders the relationship timeline when history is present", () => {
    render(
      <ContactProfileCard
        {...baseView}
        history={[
          { occurredAt: "2026-01-01T00:00:00.000Z", channel: "email", summary: "First outreach", citation: null },
        ]}
      />,
    );
    expect(screen.getByText("First outreach")).toBeInTheDocument();
    expect(screen.getByText("email")).toBeInTheDocument();
  });

  it("renders key facts and knowledge gaps in separate sections", () => {
    render(
      <ContactProfileCard
        {...baseView}
        acceptedFacts={[{ subject: "Jane", predicate: "role", object: "PM", citation: "c1", status: "accepted" }]}
        gapFacts={[{ subject: "Jane", predicate: "last_contacted", object: "unknown", citation: null, status: "candidate" }]}
      />,
    );
    expect(screen.getByText("Key facts")).toBeInTheDocument();
    expect(screen.getByText("Knowledge gaps")).toBeInTheDocument();
    expect(screen.getByText(/Jane role PM/)).toBeInTheDocument();
    expect(screen.getByText(/Jane last_contacted unknown/)).toBeInTheDocument();
  });

  it("omits the key facts / knowledge gaps sections when there are none", () => {
    render(<ContactProfileCard {...baseView} />);
    expect(screen.queryByText("Key facts")).not.toBeInTheDocument();
    expect(screen.queryByText("Knowledge gaps")).not.toBeInTheDocument();
  });
});
