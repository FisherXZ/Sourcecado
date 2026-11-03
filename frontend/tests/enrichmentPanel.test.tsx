import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EnrichmentPanel } from "../src/EnrichmentPanel";
const api=vi.hoisted(()=>({getPersonEnrichment:vi.fn(),preparePersonEnrichment:vi.fn(),decideApproval:vi.fn(),newOperationId:vi.fn(()=>"lookup-operation-1")}));
vi.mock("../src/api",()=>api);
const base={person:{person_id:"maya",name:"Maya Patel",title:"Lead",company:"Example Labs",version:1,identity_revision:0},known:{email:null,phone:null,linkedin_url:null},match:{apollo_id:"apollo_maya"},credits_estimated:null,session_id:"sid",status:"not_enriched",latest:null,pending:null,can_request:true,problem:null,attempts:[]};
const pending={id:"lookup-1",status:"awaiting_approval",provider:"apollo",person:"Maya Patel",title:"Lead",company:"Example Labs",match:{apollo_id:"apollo_maya"},matched_on:"apollo_id",credits_estimated:null,credits_actual:null,fetched_at:null,created_at:"2026-11-02",fields_found:[],fields_applied:[],conflicts:[],stale_identity:false};
const review={...base,status:"awaiting_approval",latest:pending,pending,can_request:false};
function terminal(status="found") {return {...base,status,latest:{...pending,status,credits_actual:null,fetched_at:"2026-11-02T12:00:00Z",fields_found:["email"],fields_applied:["email"]},known:{...base.known,email:"maya@example.test"}};}
beforeEach(()=>{vi.clearAllMocks();localStorage.clear();api.getPersonEnrichment.mockResolvedValue(base);api.preparePersonEnrichment.mockResolvedValue(review);api.decideApproval.mockResolvedValue({ok:true,result:{status:"found"}});});
describe("deliberate person enrichment",()=>{
 it("opens a one-line status without preparing or spending",async()=>{
  render(<EnrichmentPanel personId="maya"/>);await screen.findByText("Not looked up");
  expect(screen.getByRole("button",{name:"Find contact details"})).toBeEnabled();
  expect(api.preparePersonEnrichment).not.toHaveBeenCalled();expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it("reviews exactly one selected person and its unknown estimated cost before explicit approval",async()=>{
  render(<EnrichmentPanel personId="maya"/>);fireEvent.click(await screen.findByRole("button",{name:"Find contact details"}));
  await screen.findByRole("button",{name:"Approve lookup once"});expect(screen.getByText("Maya Patel",{exact:true})).toBeInTheDocument();
  expect(screen.getByText("Unknown",{exact:true})).toBeInTheDocument();
  expect(api.decideApproval).not.toHaveBeenCalled();
  api.getPersonEnrichment.mockResolvedValue(terminal());fireEvent.click(screen.getByRole("button",{name:"Approve lookup once"}));
  await screen.findByText(/^Found via Apollo/);expect(api.decideApproval).toHaveBeenCalledExactlyOnceWith("lookup-1","allow");
  expect(screen.getByRole("status")).toHaveTextContent("credits not reported");
 });
 it("cancel denies the waiting lookup and never pays",async()=>{
  api.getPersonEnrichment.mockResolvedValue(review);render(<EnrichmentPanel personId="maya"/>);
  fireEvent.click(await screen.findByRole("button",{name:"Cancel lookup"}));
  await waitFor(()=>expect(api.decideApproval).toHaveBeenCalledExactlyOnceWith("lookup-1","deny"));
 });
 it("restores a pending approval after remount without creating another",async()=>{
  api.getPersonEnrichment.mockResolvedValue(review);const first=render(<EnrichmentPanel personId="maya"/>);
  await screen.findByRole("button",{name:"Approve lookup once"});first.unmount();
  render(<EnrichmentPanel personId="maya"/>);await screen.findByRole("button",{name:"Approve lookup once"});
  expect(api.preparePersonEnrichment).not.toHaveBeenCalled();expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it("a lost decision response checks status without another paid submission",async()=>{
  api.getPersonEnrichment.mockResolvedValue(review);api.decideApproval.mockRejectedValue(new Error("offline"));
  render(<EnrichmentPanel personId="maya"/>);fireEvent.click(await screen.findByRole("button",{name:"Approve lookup once"}));
  await screen.findByRole("alert");expect(api.decideApproval).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button",{name:"Check lookup status"})).toBeInTheDocument();
 });
 it("an unknown outcome stays stopped until explicit review of a new attempt",async()=>{
  api.getPersonEnrichment.mockResolvedValue({...terminal("outcome_unknown"),can_request:false});render(<EnrichmentPanel personId="maya"/>);
  await screen.findByText("Outcome unknown");expect(api.preparePersonEnrichment).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"Check lookup status"}));await waitFor(()=>expect(api.getPersonEnrichment).toHaveBeenCalledTimes(2));
  expect(api.decideApproval).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("button",{name:"Review a new lookup"}));
  expect(api.preparePersonEnrichment).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("checkbox",{name:"I understand this is a new paid attempt"}));
  fireEvent.click(screen.getByRole("button",{name:"Review new attempt"}));
  await waitFor(()=>expect(api.preparePersonEnrichment).toHaveBeenCalledWith("maya",expect.anything(),expect.any(String),"lookup-1"));
  expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it.each([["partial","Partly found via Apollo · credits not reported"],["no_match","No match found"],["failed","Lookup failed"],["insufficient_credit","Not enough credits"]])("shows %s honestly without automatic retry",async(status,label)=>{
  api.getPersonEnrichment.mockResolvedValue(terminal(status));render(<EnrichmentPanel personId="maya"/>);await screen.findByText(new RegExp(`^${label}`));
  expect(api.preparePersonEnrichment).not.toHaveBeenCalled();expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it("blocks stale identity approval and offers correction navigation",async()=>{
  api.getPersonEnrichment.mockResolvedValue({...review,pending:{...pending,stale_identity:true}});render(<EnrichmentPanel personId="maya"/>);
  expect(await screen.findByRole("button",{name:"Approve lookup once"})).toBeDisabled();
  expect(screen.getByRole("link",{name:"Review person details"})).toHaveAttribute("href","#/people/maya");
 });
 it("requires fixing a missing connection before approving a restored review",async()=>{
  api.getPersonEnrichment.mockResolvedValue({...review,problem:"apollo_not_configured"});render(<EnrichmentPanel personId="maya"/>);
  expect(await screen.findByRole("button",{name:"Approve lookup once"})).toBeDisabled();
  expect(screen.getByRole("link",{name:"Connect Apollo"})).toHaveAttribute("href","#/connections/apollo");
  expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it("keeps an older unknown stopped while displaying the newer known result",async()=>{
  api.getPersonEnrichment.mockResolvedValue({...terminal(),can_request:false,unresolved_unknown_id:"older-unknown"});render(<EnrichmentPanel personId="maya"/>);
  await screen.findByText(/^Found via Apollo/);fireEvent.click(screen.getByRole("button",{name:"Review a new lookup"}));
  fireEvent.click(screen.getByRole("checkbox",{name:"I understand this is a new paid attempt"}));fireEvent.click(screen.getByRole("button",{name:"Review new attempt"}));
  await waitFor(()=>expect(api.preparePersonEnrichment).toHaveBeenCalledWith("maya",expect.anything(),expect.any(String),"older-unknown"));
  expect(api.decideApproval).not.toHaveBeenCalled();
 });
 it("shows a found lookup as one line: provider, actual credits, and date",async()=>{
  const found=terminal();api.getPersonEnrichment.mockResolvedValue({...found,latest:{...found.latest,credits_actual:1}});render(<EnrichmentPanel personId="maya"/>);
  const chip=await screen.findByText(/^Found via Apollo · 1 credit · /);
  expect(chip).toHaveAttribute("title","Updated: Email");
  expect(screen.queryByText("Missing email")).toBeNull();
 });
 it("does not ask for a person review after an ordinary successful lookup",async()=>{
  const found=terminal();api.getPersonEnrichment.mockResolvedValue({...found,latest:{...found.latest,stale_identity:true}});render(<EnrichmentPanel personId="maya"/>);
  await screen.findByText(/^Found via Apollo/);
  expect(screen.queryByRole("link",{name:"Review person details"})).toBeNull();
 });
 it("explains an unconfirmed provider identity and links to the person's details",async()=>{
  const missed=terminal("no_match");api.getPersonEnrichment.mockResolvedValue({...missed,latest:{...missed.latest,fields_applied:[],review_required:true,code:"provider_identity_mismatch"}});render(<EnrichmentPanel personId="maya"/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Apollo couldn’t confirm this is the same person. No details were saved.");
  expect(screen.getByRole("link",{name:"Review person details"})).toHaveAttribute("href","#/people/maya");
 });
 it("reuses the preparation operation after a lost preparation response",async()=>{
  api.preparePersonEnrichment.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(review);
  render(<EnrichmentPanel personId="maya"/>);fireEvent.click(await screen.findByRole("button",{name:"Find contact details"}));
  await screen.findByRole("alert");fireEvent.click(screen.getByRole("button",{name:"Find contact details"}));
  await screen.findByRole("button",{name:"Approve lookup once"});
  expect(api.preparePersonEnrichment.mock.calls[0][2]).toBe(api.preparePersonEnrichment.mock.calls[1][2]);
  expect(api.decideApproval).not.toHaveBeenCalled();
 });
});
