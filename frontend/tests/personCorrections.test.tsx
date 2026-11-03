import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { PersonPreview } from "../src/crm/PersonPreview";
import { defaultPersonDescriptions, type PersonFile } from "../src/api";
import { livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  getPerson: vi.fn(),
  reviewPersonCorrection: vi.fn(),
  savePersonCorrection: vi.fn(),
  listTasks: vi.fn(),
  crmRequest: vi.fn(),
  undoPersonCorrection: vi.fn(),
}));
vi.mock("../src/api", async (original) => ({
  ...(await original<typeof import("../src/api")>()),
  ...(await import("./taskApi")).taskApiMock(),
  ...api,
}));
const file = (): PersonFile => ({
  person: {
    person_id: "per_maya",
    first_name: "Maya",
    last_name: "Patel",
    company: "Lumen",
    title: "Lead",
    email: "maya@lumen.test",
    sequence_state: "open",
    version: 3,
  },
  brief: livingBrief(),
  timeline: [],
  sourcing_chat: null,
  descriptions: defaultPersonDescriptions(),
  crm_changes: [],
  crm_proposals: [],
});
const review = {
  person_id: "per_maya",
  expected_version: 3,
  changes: [
    {
      field: "company",
      label: "Company",
      before: "Lumen",
      after: "Lumen Labs",
    },
  ],
  duplicates: [],
  collision_token: "current-review",
  identity_changed: true,
};
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  api.getPerson.mockResolvedValue(file());
  api.listTasks.mockResolvedValue({ tasks: [], truncated: false });
  api.reviewPersonCorrection.mockResolvedValue(review);
  api.savePersonCorrection.mockResolvedValue({
    person: { ...file().person, company: "Lumen Labs", version: 4 },
    change: { change_id: "chg_details" },
  });
});

it("reviews exact changes before saving the same person from the preview", async () => {
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit company: Lumen" }),
  );
  const company = screen.getByLabelText("Company");
  await waitFor(() => expect(company).toHaveFocus());
  fireEvent.change(company, { target: { value: "Lumen Labs" } });
  fireEvent.keyDown(company, { key: "Enter" });
  const card = await screen.findByRole("group", {
    name: "Review person correction",
  });
  expect(api.savePersonCorrection).not.toHaveBeenCalled();
  const comparison = within(card).getByRole("list", {
    name: "Person detail changes",
  });
  expect(within(comparison).getByText("Lumen")).toBeInTheDocument();
  expect(within(comparison).getByText("Lumen Labs")).toBeInTheDocument();
  expect(card).toHaveTextContent(
    "Earlier send and lookup approvals will need a new review.",
  );
  // The reason is optional; the server records a default when it is blank.
  const reason = within(card).getByLabelText("Reason");
  expect(reason).toHaveAttribute("placeholder", "Why (optional)");
  await waitFor(() => expect(reason).toHaveFocus());
  fireEvent.change(reason, {
    target: { value: "Maya confirmed the new company" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(api.savePersonCorrection).toHaveBeenCalled());
  expect(api.savePersonCorrection.mock.calls[0][0]).toBe("per_maya");
  expect(api.savePersonCorrection.mock.calls[0][1]).toMatchObject({
    changes: { company: "Lumen Labs" },
    expected_version: 3,
    reason: "Maya confirmed the new company",
  });
  expect(await screen.findByText("Company saved")).toBeInTheDocument();
});

it("keeps unsaved values and retry identity after a failed save", async () => {
  api.savePersonCorrection.mockRejectedValueOnce(new Error("Offline"));
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Company"), {
    target: { value: "Lumen Labs" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByRole("button", { name: "Save correction" });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Company")).toHaveValue("Lumen Labs");
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() =>
    expect(api.savePersonCorrection).toHaveBeenCalledTimes(2),
  );
  expect(api.savePersonCorrection.mock.calls[0][1].operation_id).toBe(
    api.savePersonCorrection.mock.calls[1][1].operation_id,
  );
});

it("requires duplicate confirmation and links the existing file without merging", async () => {
  api.reviewPersonCorrection.mockResolvedValue({
    ...review,
    duplicates: [
      {
        person_id: "per_other",
        name: "Other Maya",
        company: "Orbit",
        matched_fields: ["email"],
        href: "#/people/per_other",
      },
    ],
  });
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit email: maya@lumen.test" }),
  );
  const email = screen.getByLabelText("Email");
  fireEvent.change(email, { target: { value: "shared@lumen.test" } });
  fireEvent.keyDown(email, { key: "Enter" });
  expect(
    await screen.findByRole("link", { name: "Other Maya · Orbit" }),
  ).toHaveAttribute("href", "#/people/per_other");
  expect(
    screen.getByRole("button", { name: "Save correction" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: /I checked the existing file/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(api.savePersonCorrection).toHaveBeenCalled());
  expect(api.savePersonCorrection.mock.calls[0][1]).toMatchObject({
    collision_token: "current-review",
    confirmed_duplicate_ids: ["per_other"],
  });
});

it("retains the draft when the selected person changes during saving", async () => {
  const actual = await import("../src/api");
  api.savePersonCorrection.mockRejectedValueOnce(
    new actual.PersonCorrectionError(
      "The saved person changed.",
      409,
      "person_conflict",
    ),
  );
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Company"), {
    target: { value: "Lumen Labs" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Save correction" }),
  );
  await screen.findByRole("alert");
  api.getPerson.mockResolvedValue({
    ...file(),
    person: { ...file().person, version: 5, company: "New saved company" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Review latest details" }),
  );
  expect(await screen.findByLabelText("Company")).toHaveValue("Lumen Labs");
  fireEvent.click(
    await screen.findByRole("button", { name: "Review changes" }),
  );
  await waitFor(() =>
    expect(api.reviewPersonCorrection.mock.calls.at(-1)?.[1]).toMatchObject({
      expected_version: 5,
      changes: { company: "Lumen Labs" },
    }),
  );
});

it("edits one value in place: Escape cancels, Enter saves a role without review", async () => {
  api.savePersonCorrection.mockResolvedValue({
    person: { ...file().person, title: "Principal", version: 4 },
    change: { change_id: "chg_role" },
  });
  api.undoPersonCorrection.mockResolvedValue({
    person: { ...file().person, version: 5 },
    replayed: false,
  });
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  const details = await screen.findByRole("region", { name: "Person details" });
  fireEvent.click(
    within(details).getByRole("button", { name: "Edit role: Lead" }),
  );
  const role = within(details).getByLabelText("Role");
  await waitFor(() => expect(role).toHaveFocus());
  fireEvent.change(role, { target: { value: "Principal" } });
  fireEvent.keyDown(role, { key: "Escape" });
  expect(within(details).queryByLabelText("Role")).toBeNull();
  await waitFor(() =>
    expect(
      within(details).getByRole("button", { name: "Edit role: Lead" }),
    ).toHaveFocus(),
  );
  expect(api.reviewPersonCorrection).not.toHaveBeenCalled();
  expect(api.savePersonCorrection).not.toHaveBeenCalled();
  // The preview stays open: Escape belonged to the field.
  expect(
    screen.getByRole("region", { name: "Person preview" }),
  ).toBeInTheDocument();

  fireEvent.click(
    within(details).getByRole("button", { name: "Edit role: Lead" }),
  );
  const again = within(details).getByLabelText("Role");
  fireEvent.change(again, { target: { value: "Principal" } });
  fireEvent.keyDown(again, { key: "Enter" });
  await waitFor(() => expect(api.savePersonCorrection).toHaveBeenCalled());
  expect(api.reviewPersonCorrection).not.toHaveBeenCalled();
  expect(api.savePersonCorrection.mock.calls[0][1]).toMatchObject({
    changes: { title: "Principal" },
    expected_version: 3,
    reason: "",
  });
  const result = await within(details).findByRole("status");
  expect(result).toHaveTextContent("Role saved");
  fireEvent.click(
    within(result).getByRole("button", { name: "Undo correction" }),
  );
  await waitFor(() =>
    expect(api.undoPersonCorrection.mock.calls[0].slice(0, 2)).toEqual([
      "per_maya",
      "chg_role",
    ]),
  );
  expect(await within(details).findByRole("status")).toHaveTextContent(
    "Restored",
  );
});

it("keeps identity fields behind the correction review, even on blur", async () => {
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit email: maya@lumen.test" }),
  );
  const email = screen.getByLabelText("Email");
  fireEvent.change(email, { target: { value: "maya@lumenlabs.test" } });
  fireEvent.blur(email);
  await screen.findByRole("group", { name: "Review person correction" });
  fireEvent.blur(email);
  expect(api.reviewPersonCorrection).toHaveBeenCalledTimes(1);
  expect(api.reviewPersonCorrection.mock.calls[0][1]).toMatchObject({
    changes: { email: "maya@lumenlabs.test" },
    expected_version: 3,
  });
  expect(api.savePersonCorrection).not.toHaveBeenCalled();
  // Escape steps back to the edited value, keeping it.
  fireEvent.keyDown(screen.getByLabelText("Reason"), { key: "Escape" });
  expect(
    screen.queryByRole("group", { name: "Review person correction" }),
  ).toBeNull();
  expect(screen.getByLabelText("Email")).toHaveValue("maya@lumenlabs.test");
});

it("turns a direct save into a review when the server finds a duplicate", async () => {
  const actual = await import("../src/api");
  api.savePersonCorrection.mockRejectedValueOnce(
    new actual.PersonCorrectionError(
      "Review the possible duplicate before saving this correction.",
      409,
      "duplicate_review_required",
      {
        ...review,
        changes: [{ field: "phone", label: "Phone", before: null, after: "+1 555 0100" }],
        identity_changed: false,
        duplicates: [
          {
            person_id: "per_other",
            name: "Other Maya",
            company: null,
            matched_fields: ["phone"],
            href: "#/people/per_other",
          },
        ],
      },
    ),
  );
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add phone" }));
  const phone = screen.getByLabelText("Phone");
  fireEvent.change(phone, { target: { value: "+1 555 0100" } });
  fireEvent.keyDown(phone, { key: "Enter" });
  expect(
    await screen.findByRole("link", { name: "Other Maya" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Save correction" }),
  ).toBeDisabled();
  expect(api.savePersonCorrection).toHaveBeenCalledTimes(1);
});

it("offers the same correction editor in the full person file", async () => {
  const { PersonFileView } = await import("../src/PersonFile");
  render(<PersonFileView personId="per_maya" />);
  expect(
    await screen.findByRole("button", { name: "Edit details" }),
  ).toBeInTheDocument();
});

it("offers field-scoped correction Undo after reloading a person", async () => {
  api.getPerson.mockResolvedValue({
    ...file(),
    crm_changes: [
      {
        change_id: "chg_details",
        entity_type: "person_details",
        action: "update",
        reason: "Verified company",
        actor: "director",
        created_at: "2026-11-02",
        source_refs: "[]",
        can_undo: true,
      },
    ],
  });
  api.undoPersonCorrection.mockResolvedValue({
    person: { ...file().person, version: 4 },
    replayed: false,
  });
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("Correction history"));
  fireEvent.click(
    screen.getByRole("button", { name: "Undo correction: Verified company" }),
  );
  await waitFor(() => expect(api.undoPersonCorrection).toHaveBeenCalled());
  expect(api.undoPersonCorrection.mock.calls[0].slice(0, 2)).toEqual([
    "per_maya",
    "chg_details",
  ]);
});

it("returns focus to Edit details after Cancel", async () => {
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Company"), {
    target: { value: "Unsaved company" },
  });
  fireEvent.click(
    within(screen.getByRole("region", { name: "Person details" })).getByRole(
      "button",
      { name: "Cancel" },
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Edit details" })).toHaveFocus(),
  );
});

it('never places a completed correction on a different person after navigation',async()=>{
  const {PersonFileView}=await import('../src/PersonFile');
  let finish!:(value:unknown)=>void;
  api.savePersonCorrection.mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  const other={...file(),person:{...file().person,person_id:'per_noor',first_name:'Noor',last_name:'Chen',email:'noor@orbit.test'},brief:{...livingBrief(),who:'Noor Chen'}};
  let reads=0;
  api.getPerson.mockImplementation((id:string)=>id==='per_noor'?(reads++===0?Promise.resolve(other):new Promise(()=>{})):Promise.resolve(file()));
  const view=render(<PersonFileView personId="per_maya"/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit details'}));
  fireEvent.change(screen.getByLabelText('Company'),{target:{value:'Lumen Labs'}});
  fireEvent.click(screen.getByRole('button',{name:'Review changes'}));
  fireEvent.click(await screen.findByRole('button',{name:'Save correction'}));
  await waitFor(()=>expect(api.savePersonCorrection).toHaveBeenCalled());
  view.rerender(<PersonFileView personId="per_noor"/>);
  await screen.findByRole('heading',{level:1,name:'Noor Chen'});
  finish({person:{...file().person,company:'Lumen Labs',version:4},change:{change_id:'chg_saved'}});
  await waitFor(()=>expect(api.getPerson.mock.calls.filter(call=>call[0]==='per_noor').length).toBeGreaterThan(1));
  expect(within(screen.getByRole('region',{name:'Person details'})).getByText('noor@orbit.test')).toBeInTheDocument();
  expect(within(screen.getByRole('region',{name:'Person details'})).queryByText('maya@lumen.test')).not.toBeInTheDocument();
});
