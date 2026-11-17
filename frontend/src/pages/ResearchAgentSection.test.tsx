// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RESEARCH_AGENT, type AgentExport, type AgentProposal } from "../data/researchAgent";
import { ResearchAgentSection } from "./ResearchAgentSection";

afterEach(cleanup);

const show = (data?: AgentExport) => render(<ResearchAgentSection data={data} />);
const proposal = (p: Partial<AgentProposal>): AgentExport => ({
  ...RESEARCH_AGENT,
  proposals: [{ ...RESEARCH_AGENT.proposals[0], ...p }],
});

describe("research agent section", () => {
  it("shows the four questions in order with their answers", () => {
    show();
    const items = within(screen.getAllByRole("list")[0]).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector("p")!.textContent)).toEqual(RESEARCH_AGENT.questions.map((q) => `“${q.question}”`));
    expect(items[0].textContent).toContain("had an alert on for 13 of 52 episodes");
    expect(items[0].textContent).toContain("Chance control: the same time windows on patients with no episode held an alert in 329 of 3115 windows");
    expect(items[2].textContent).toContain("2.54 alerts per nurse per 12-hour shift");
    expect(items[3].textContent).toContain("Refused, nothing was run");
    expect(items[0].textContent).toContain("Answered");
  });

  it("shows the ledger's ask times, says when none is recorded, and shows no question id", () => {
    show();
    expect(screen.getByText("Asked 2026-11-13T05:05:46Z")).toBeTruthy();
    expect(screen.getAllByText(/^Asked /)).toHaveLength(4);
    expect(screen.queryByText(/S-00/)).toBeNull();
    cleanup();
    show({ ...RESEARCH_AGENT, questions: [{ ...RESEARCH_AGENT.questions[0], askedAt: null }] });
    expect(screen.getByText("Time not recorded")).toBeTruthy();
  });

  it("shows the only sign-off verbatim, with the proposal id and the source", () => {
    show();
    expect(screen.getByText("Proposal P-0001")).toBeTruthy();
    expect(screen.getByText("Signed off by Unattended demo run - not a person, then applied to the models folder that run was given.")).toBeTruthy();
    expect(screen.getByText(/This page cannot ask anything/)).toBeTruthy();
    expect(screen.getByText(/typed by the unattended run, not by a doctor/)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Proposals" })).toBeTruthy();
    expect(screen.getByText(/^Source: The run's ledger, committed as research\/agent\/records\/pr19-run\/ledger\.jsonl \(sha256 340e0bcc/).textContent).toMatch(/ 21 ledger records, copied from the ledger's export \(frontend\/src\/data\/evaluation\/research-agent\.json\), 2026-11-13\.$/);
    expect(screen.getByText(/^Synthetic, not validated\./)).toBeTruthy();
  });

  it("shows both empty states", () => {
    show({ ...RESEARCH_AGENT, questions: [], proposals: [] });
    expect(screen.getByText("No questions are recorded in this export.")).toBeTruthy();
    expect(screen.getByText("No proposals are recorded in this export.")).toBeTruthy();
  });

  it("shows the proposed and approved states and a set ask time", () => {
    show(proposal({ state: "proposed", approvedBy: null }));
    expect(screen.getByText("Waiting for a person to sign off. Nothing applied.")).toBeTruthy();
    cleanup();
    show(proposal({ state: "approved", approvedAt: "2026-11-13T05:00:00Z" }));
    expect(screen.getByText("Signed off by Unattended demo run - not a person on 2026-11-13T05:00:00Z. Not applied yet.")).toBeTruthy();
    cleanup();
    show({ ...RESEARCH_AGENT, questions: [{ ...RESEARCH_AGENT.questions[0], askedAt: "2026-11-13T04:00:00Z" }] });
    expect(screen.getByText("Asked 2026-11-13T04:00:00Z")).toBeTruthy();
  });
});
