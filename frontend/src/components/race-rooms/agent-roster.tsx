// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useState } from "react";

import type { AgentProfile } from "@/lib/types";

type AgentRosterProps = {
  agents: AgentProfile[];
  selectedAgent: string;
  onSelectAgent: (agentId: string) => void;
};

export function AgentRoster({ agents, selectedAgent, onSelectAgent }: AgentRosterProps) {
  const [expanded, setExpanded] = useState(false);
  return <section className="roster-card" data-testid="agent-roster" aria-labelledby="roster-title">
    <div className="panel-heading">
      <div><p className="section-kicker">Five specialist voices</p><h2 id="roster-title">In this room</h2></div>
      <button className="icon-button" type="button" aria-expanded={expanded} aria-controls="agent-roster" onClick={() => setExpanded(!expanded)}>
        <span aria-hidden>{expanded ? "−" : "+"}</span><span className="sr-only">{expanded ? "Collapse" : "Expand"} agent roster</span>
      </button>
    </div>
    {!expanded && <button className="roster-summary" type="button" aria-expanded="false" aria-controls="agent-roster" onClick={() => setExpanded(true)}><span className="roster-summary__avatars" aria-hidden>{agents.map((agent) => <span data-accent={agent.ui_accent_key} key={agent.id}>{agent.avatar_key}</span>)}</span><span><b>{agents.length} agents in this room</b><small>{agents.map((agent) => agent.display_name).join(" · ")}</small></span><span className="roster-summary__action">Meet the team <b aria-hidden>+</b></span></button>}
    {expanded && <><p className="roster-support">Each specialist has a different view of the session. Select one to filter the conversation.</p><div id="agent-roster" className="agent-roster">
      {agents.map((agent) => <button
        className={`agent-profile ${selectedAgent === agent.id ? "agent-profile--selected" : ""}`}
        data-accent={agent.ui_accent_key}
        data-agent-id={agent.id}
        key={agent.id}
        type="button"
        aria-pressed={selectedAgent === agent.id}
        onClick={() => onSelectAgent(selectedAgent === agent.id ? "all" : agent.id)}
      >
        <span className="agent-avatar" aria-hidden>{agent.avatar_key}</span>
        <span className="agent-profile__copy">
          <span className="agent-profile__name">{agent.display_name}</span>
          <span className="agent-profile__role">{agent.role} · {agent.active ? "Active" : "Inactive"}</span>
          <span className="agent-profile__description">{agent.short_description}</span>
          <span className="agent-profile__tags" aria-label="Specialties">{agent.specialties.map((specialty) => <span key={specialty}>{specialty}</span>)}</span>
        </span>
      </button>)}
    </div></>}
  </section>;
}
