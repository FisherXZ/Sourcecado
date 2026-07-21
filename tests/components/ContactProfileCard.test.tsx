// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { ContactProfileCard } from "@/app/chat/ContactProfileCard";
import type { ContactCardView } from "@/app/chat/stream";

const baseView: ContactCardView = {
  contact: {
    id: 1,
    canonicalName: "Jane Smith",
    role: "PM",
    organizationName: "Acme Corp",
    phone: null,
    email: null,
    linkedinUrl: null,
    photoUrl: null,
  },
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
        contact={{
          id: 1,
          canonicalName: "Thin Record",
          role: null,
          organizationName: null,
          phone: null,
          email: null,
          linkedinUrl: null,
          photoUrl: null,
        }}
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

  it("drops a fact's subject when it's just restating the contact's own name", () => {
    render(
      <ContactProfileCard
        {...baseView}
        acceptedFacts={[
          { subject: "Jane Smith", predicate: "role", object: "Engineering Manager at Acme Corp", citation: "c1", status: "accepted" },
        ]}
      />,
    );
    expect(screen.getByText(/Engineering Manager at Acme Corp/)).toBeInTheDocument();
    expect(screen.queryByText(/Jane Smith role/)).not.toBeInTheDocument();
  });

  it("keeps a fact's subject when it's about someone/something else", () => {
    render(
      <ContactProfileCard
        {...baseView}
        acceptedFacts={[{ subject: "Acme Corp", predicate: "founded", object: "2015", citation: "c1", status: "accepted" }]}
      />,
    );
    expect(screen.getByText(/Acme Corp founded 2015/)).toBeInTheDocument();
  });

  it("shows contact details when present, and gap placeholders when missing", () => {
    render(<ContactProfileCard {...baseView} />);
    expect(screen.getByText(/Phone not yet known/)).toBeInTheDocument();
    expect(screen.getByText(/Email not yet known/)).toBeInTheDocument();
    expect(screen.getByText(/LinkedIn not yet known/)).toBeInTheDocument();
  });

  it("renders phone, email, and a working LinkedIn link when known", () => {
    render(
      <ContactProfileCard
        {...baseView}
        contact={{ ...baseView.contact, phone: "555-0100", email: "jane@acme.com", linkedinUrl: "https://linkedin.com/in/janesmith" }}
      />,
    );
    expect(screen.getByText("555-0100")).toBeInTheDocument();
    expect(screen.getByText("jane@acme.com")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /linkedin/i });
    expect(link).toHaveAttribute("href", "https://linkedin.com/in/janesmith");
  });

  it("renders a photo when photoUrl is set, and initials otherwise", () => {
    const { rerender } = render(<ContactProfileCard {...baseView} />);
    expect(screen.getByText("JS")).toBeInTheDocument();

    rerender(
      <ContactProfileCard
        {...baseView}
        contact={{ ...baseView.contact, photoUrl: "https://example.com/jane.jpg" }}
      />,
    );
    expect(screen.getByRole("img", { name: "Jane Smith" })).toHaveAttribute("src", "https://example.com/jane.jpg");
  });
});
