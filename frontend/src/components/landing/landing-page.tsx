// SPDX-License-Identifier: AGPL-3.0-only
import Link from "next/link";

import { AppNavigation } from "@/components/navigation/app-navigation";

const agents = [
  { initials: "MV", name: "Mira Vale", role: "Strategy", accent: "copper" },
  { initials: "TV", name: "Theo Voss", role: "Telemetry", accent: "cyan" },
  { initials: "LC", name: "Lena Cross", role: "Racecraft", accent: "rose" },
  { initials: "AR", name: "Arjun Reyes", role: "History", accent: "violet" },
  { initials: "N", name: "Nova", role: "Host", accent: "gold" },
] as const;

const features = [
  {
    number: "01",
    eyebrow: "Unified race signal",
    title: "Noise becomes context.",
    copy: "Timing, telemetry and race-control events are normalized into one ordered race story before the room reacts.",
    color: "cyan",
  },
  {
    number: "02",
    eyebrow: "Specialist conversation",
    title: "Five minds challenge the obvious.",
    copy: "Strategy, pace, racecraft and history specialists reply, disagree and revise conclusions as the evidence changes.",
    color: "violet",
  },
  {
    number: "03",
    eyebrow: "Inspectable intelligence",
    title: "Every claim leaves a trail.",
    copy: "Open the evidence behind a message to see its trigger, source metrics, confidence and data-quality notes.",
    color: "coral",
  },
] as const;

export function LandingPage() {
  return <main className="landing-shell track-grid">
    <AppNavigation />

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-hero__copy">
        <p className="landing-kicker"><span /> Formula racing, interpreted live</p>
        <h1 id="landing-title">Every race has a story. <em>Five minds find it.</em></h1>
        <p className="landing-lede">Apex Arena turns live and historical race data into an evidence-linked conversation between specialist AI agents—so every strategy call, pace shift and on-track battle has context.</p>
        <div className="landing-actions">
          <Link className="landing-button landing-button--primary" href="/race-rooms">Enter Race Rooms <span aria-hidden>↗</span></Link>
          <a className="landing-button landing-button--secondary" href="#experience">See how it works <span aria-hidden>↓</span></a>
        </div>
        <div className="landing-proof" aria-label="Product highlights">
          <span><i className="proof-dot proof-dot--live" /> Live + replay</span>
          <span><b>5</b> specialist agents</span>
          <span><b>100%</b> evidence linked</span>
        </div>
      </div>

      <div className="strategy-visual" aria-label="A stylized preview of an Apex Arena strategy conversation">
        <div className="strategy-visual__glow" />
        <div className="strategy-visual__topline"><span><i /> Room 01 · Race analysis</span><strong>LIVE SIGNAL</strong></div>
        <div className="track-orbit" aria-hidden><span className="track-orbit__car" /><b>12</b><small>LAP / 57</small></div>
        <article className="signal-card signal-card--pace">
          <header><span className="agent-avatar" data-accent="cyan">TV</span><div><b>Theo Voss</b><small>Telemetry engineer</small></div><time>Lap 12</time></header>
          <p>The clean-air pace is improving through sector two. The last three representative laps support the trend.</p>
          <footer><span>PACE</span><span>HIGH CONFIDENCE</span><span>7 SOURCES</span></footer>
        </article>
        <article className="signal-card signal-card--strategy">
          <header><span className="agent-avatar" data-accent="copper">MV</span><div><b>Mira Vale</b><small>Race strategist</small></div><time>Reply</time></header>
          <p>That strengthens the overcut case, but traffic after the stop remains the deciding variable.</p>
          <footer><span>STRATEGY</span><span>↳ REPLYING TO THEO</span></footer>
        </article>
        <div className="visual-telemetry" aria-hidden><span style={{ height: "45%" }} /><span style={{ height: "72%" }} /><span style={{ height: "54%" }} /><span style={{ height: "88%" }} /><span style={{ height: "66%" }} /><span style={{ height: "94%" }} /><span style={{ height: "58%" }} /></div>
      </div>
    </section>

    <section id="experience" className="landing-section landing-section--experience" aria-labelledby="experience-title">
      <div className="landing-section__heading"><div><p className="landing-kicker"><span /> From signal to insight</p><h2 id="experience-title">Not another timing screen.</h2></div><p>The technical depth of a strategy room, presented as a conversation worth following.</p></div>
      <div className="feature-grid">{features.map((feature) => <article className="feature-panel" data-color={feature.color} key={feature.number}><span className="feature-panel__number">{feature.number}</span><p>{feature.eyebrow}</p><h3>{feature.title}</h3><div className="feature-panel__line" /><p>{feature.copy}</p></article>)}</div>
    </section>

    <section id="agents" className="landing-section landing-agents" aria-labelledby="agents-title">
      <div className="landing-agents__copy"><p className="landing-kicker"><span /> Inside every Race Room</p><h2 id="agents-title">One race.<br /><em>Five perspectives.</em></h2><p>Each agent has a defined analytical lens, speaking style and evidence standard. They do not simply comment—they respond to one another.</p><Link className="text-link" href="/race-rooms">Meet them in a Race Room <span aria-hidden>→</span></Link></div>
      <div className="agent-spectrum">{agents.map((agent, index) => <article data-accent={agent.accent} key={agent.name}><span className="agent-spectrum__index">0{index + 1}</span><span className="agent-avatar" aria-hidden>{agent.initials}</span><div><h3>{agent.name}</h3><p>{agent.role}</p></div><span className="agent-spectrum__arrow" aria-hidden>↗</span></article>)}</div>
    </section>

    <section className="landing-cta" aria-labelledby="cta-title">
      <div><p className="landing-kicker"><span /> The room is ready</p><h2 id="cta-title">Watch the race think out loud.</h2></div><Link className="landing-button landing-button--light" href="/race-rooms">Explore the 2026 rooms <span aria-hidden>→</span></Link>
    </section>

    <footer className="landing-footer"><Link className="landing-brand" href="/"><i className="brand-mark" /><span>APEX ARENA</span></Link><p>Independent Formula racing intelligence. Built around evidence, not noise.</p><span>Unofficial fan project · 2026</span></footer>
  </main>;
}
