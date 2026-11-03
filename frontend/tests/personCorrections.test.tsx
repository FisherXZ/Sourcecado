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
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Company"), {
    target: { value: "Lumen Labs" },
  });
  fireEvent.change(screen.getByLabelText("Reason or source"), {
    target: { value: "Maya confirmed the new company" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByRole("heading", { name: "Review person correction" });
  expect(api.savePersonCorrection).not.toHaveBeenCalled();
  const comparison = screen.getByRole("table", {
    name: "Person detail changes",
  });
  expect(within(comparison).getByText("Lumen")).toBeInTheDocument();
  expect(within(comparison).getByText("Lumen Labs")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(api.savePersonCorrection).toHaveBeenCalled());
  expect(api.savePersonCorrection.mock.calls[0][0]).toBe("per_maya");
  expect(api.savePersonCorrection.mock.calls[0][1]).toMatchObject({
    changes: { company: "Lumen Labs" },
    expected_version: 3,
    reason: "Maya confirmed the new company",
  });
});

it("keeps unsaved values and retry identity after a failed save", async () => {
  api.savePersonCorrection.mockRejectedValueOnce(new Error("Offline"));
  render(<PersonPreview personId="per_maya" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Company"), {
    target: { value: "Lumen Labs" },
  });
  fireEvent.change(screen.getByLabelText("Reason or source"), {
    target: { value: "Verified offline" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByRole("button", { name: "Save correction" });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await screen.findByRole("alert");
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
  fireEvent.click(await screen.findByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "shared@lumen.test" },
  });
  fireEvent.change(screen.getByLabelText("Reason or source"), {
    target: { value: "Confirmed new address" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(
    await screen.findByRole("link", { name: "Other Maya · Orbit" }),
  ).toHaveAttribute("href", "#/people/per_other");
  expect(
    screen.getByRole("button", { name: "Save correction" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: /I reviewed the existing files/ }),
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
  fireEvent.change(screen.getByLabelText("Reason or source"), {
    target: { value: "Verified company" },
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
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  await waitFor(() =>
    expect(api.reviewPersonCorrection.mock.calls.at(-1)?.[1]).toMatchObject({
      expected_version: 5,
      changes: { company: "Lumen Labs" },
    }),
  );
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
  fireEvent.change(screen.getByLabelText('Reason or source'),{target:{value:'Confirmed'}});
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
