import CopyCommand from "./components/CopyCommand";

const githubUrl = "https://github.com/7fromheaven/repocharter";

const providers = [
  {
    name: "Codex",
    monogram: "CX",
    context: "Native context",
    guardrails: "Verified per checkout",
    verified: true,
  },
  {
    name: "Claude Code",
    monogram: "CL",
    context: "Shared context",
    guardrails: "Advisory",
  },
  {
    name: "OpenCode",
    monogram: "OC",
    context: "Native context",
    guardrails: "Advisory",
  },
  {
    name: "Hermes Agent",
    monogram: "HA",
    context: "Native context",
    guardrails: "Advisory",
  },
  {
    name: "ZCode",
    monogram: "ZC",
    context: "Context adapter",
    guardrails: "Advisory",
  },
  {
    name: "Other harnesses",
    monogram: "+",
    context: "AGENTS.md fallback",
    guardrails: "Explicit status",
  },
];

const steps = [
  {
    number: "01",
    title: "Write the charter",
    body: "Keep the rules every agent needs in one human-authored AGENTS.md. RepoCharter never invents your project truth.",
    file: "AGENTS.md",
  },
  {
    number: "02",
    title: "Move detail on demand",
    body: "Specifications, decisions, current state, and repeatable procedures stay at named paths until the work actually needs them.",
    file: "docs/project/  ·  .agents/skills/",
  },
  {
    number: "03",
    title: "Wire each harness",
    body: "Small provider adapters connect the same charter and policy to the controls each coding-agent harness really exposes.",
    file: "CLAUDE.md  ·  .codex/hooks.json",
  },
  {
    number: "04",
    title: "Prove what runs",
    body: "Live deny and allow probes separate installed configuration from verified enforcement. No checkmark by assumption.",
    file: "self-test  ·  measure  ·  verify",
  },
];

export default function Home() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "RepoCharter",
    applicationCategory: "DeveloperApplication",
    operatingSystem: "Cross-platform",
    url: "https://repocharter.com",
    codeRepository: githubUrl,
    softwareVersion: "0.3.0",
    description:
      "Portable repository context and tested guardrails for coding-agent harnesses.",
  };

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="site-header">
        <div className="shell site-header__inner">
          <a className="brand" href="#top" aria-label="RepoCharter home">
            <LogoMark />
            <span className="brand__word">
              Repo<span>Charter</span>
            </span>
          </a>
          <nav className="site-nav" aria-label="Primary navigation">
            <a href="#why">Why RepoCharter</a>
            <a href="#how-it-works">How it works</a>
            <a href="#compatibility">Compatibility</a>
          </nav>
          <a className="header-github" href={githubUrl} target="_blank" rel="noreferrer" aria-label="RepoCharter on GitHub">
            <GitHubIcon />
            <span>GitHub</span>
            <ArrowUpRight />
          </a>
        </div>
      </header>

      <main id="main">
        <section className="hero" id="top">
          <div className="hero__glow hero__glow--blue" />
          <div className="hero__glow hero__glow--mint" />
          <div className="shell hero__grid">
            <div className="hero__copy">
              <div className="eyebrow reveal reveal--1">
                <span className="eyebrow__dot" />
                Local-first infrastructure for agentic development
              </div>
              <h1 className="reveal reveal--2">
                One repo charter.
                <span>Every coding agent.</span>
              </h1>
              <p className="hero__lede reveal reveal--3">
                Keep project instructions, on-demand context, and tested guardrails in one
                portable system—so every coding agent works from the same repository truth.
              </p>
              <div className="hero__actions reveal reveal--4">
                <a className="button button--primary" href={githubUrl} target="_blank" rel="noreferrer">
                  Explore on GitHub
                  <ArrowUpRight />
                </a>
                <a className="button button--ghost" href="#how-it-works">
                  See how it works
                  <ArrowDown />
                </a>
              </div>
              <div className="reveal reveal--5">
                <CopyCommand command="kit/agentkit census --repo ~/dev/your-repo" />
                <p className="command-note">Read-only. No dependencies. No files changed.</p>
              </div>
            </div>

            <div className="context-map reveal reveal--3" aria-label="One AGENTS.md feeding multiple coding agents">
              <div className="context-map__chrome">
                <div className="window-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
                <span>repo / context map</span>
                <span className="live-pill"><i /> live</span>
              </div>

              <div className="context-map__canvas">
                <div className="map-grid" />
                <svg className="map-connectors" viewBox="0 0 620 500" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <linearGradient id="line-gradient" x1="0" x2="1">
                      <stop offset="0" stopColor="#4878ff" />
                      <stop offset="1" stopColor="#4de0b7" />
                    </linearGradient>
                  </defs>
                  <g className="map-connectors__static">
                    <path d="M310 210V270" />
                    <path d="M108 344V308Q108 270 148 270H472Q512 270 512 308V344" />
                    <path d="M310 270V344" />
                  </g>
                  <g className="map-flows">
                    <path className="map-flow map-flow--source" pathLength="100" d="M310 210V270" />
                    <path className="map-flow map-flow--branch" pathLength="100" d="M310 270H148Q108 270 108 308V344" />
                    <path className="map-flow map-flow--branch" pathLength="100" d="M310 270V344" />
                    <path className="map-flow map-flow--branch" pathLength="100" d="M310 270H472Q512 270 512 308V344" />
                  </g>
                  <circle className="map-junction" cx="310" cy="270" r="5" />
                </svg>

                <div className="charter-card">
                  <div className="charter-card__top">
                    <FileIcon />
                    <span>AGENTS.md</span>
                    <b>CANONICAL</b>
                  </div>
                  <div className="code-lines" aria-hidden="true">
                    <span style={{ "--line": "86%" }} />
                    <span style={{ "--line": "67%" }} />
                    <span style={{ "--line": "76%" }} />
                    <span style={{ "--line": "48%" }} />
                  </div>
                  <div className="charter-card__footer">
                    <span><i className="dot dot--blue" /> context</span>
                    <span><i className="dot dot--mint" /> policy</span>
                  </div>
                </div>

                <div className="agent-node agent-node--claude">
                  <span className="agent-node__icon">CL</span>
                  <span><b>Claude</b><small>adapter</small></span>
                  <CheckBadge />
                </div>
                <div className="agent-node agent-node--codex">
                  <span className="agent-node__icon">CX</span>
                  <span><b>Codex</b><small>native</small></span>
                  <CheckBadge />
                </div>
                <div className="agent-node agent-node--open">
                  <span className="agent-node__icon">OC</span>
                  <span><b>OpenCode</b><small>native</small></span>
                  <CheckBadge />
                </div>

                <div className="map-proof">
                  <ShieldCheckIcon />
                  <span><b>Policy probe passed</b><small>deny + allow paths tested</small></span>
                  <em>verified</em>
                </div>
              </div>
            </div>
          </div>

          <div className="shell proof-strip reveal reveal--5" aria-label="RepoCharter facts">
            <div><strong>209</strong><span>negative-first tests</span></div>
            <div><strong>0</strong><span>CLI runtime dependencies</span></div>
            <div><strong>1</strong><span>canonical charter</span></div>
            <div><strong>5+</strong><span>agent harnesses reached</span></div>
          </div>
        </section>

        <section className="section section--problem" id="why">
          <div className="shell problem-grid">
            <div className="section-heading section-heading--sticky">
              <p className="kicker">One source of truth</p>
              <h2>Your repository should not change personality when the agent does.</h2>
              <p>
                Parallel instruction files drift. Huge startup prompts crowd out the task. A hook
                that merely exists gets mistaken for a guardrail that works. RepoCharter gives each
                concern one clear home—and makes the adapters prove their reach.
              </p>
            </div>

            <div className="truth-stack">
              <article className="truth-card truth-card--before">
                <div className="truth-card__label"><span /> Without a charter</div>
                <h3>Five agents. Five versions of the project.</h3>
                <div className="drift-files" aria-hidden="true">
                  <div><FileMini /><span>CLAUDE.md</span><em>142 lines</em></div>
                  <div><FileMini /><span>agent-rules.md</span><em>drifted</em></div>
                  <div><FileMini /><span>memory.md</span><em>machine-local</em></div>
                  <div><FileMini /><span>tool-policy.sh</span><em>untested</em></div>
                </div>
                <p>Duplicated guidance quietly diverges while every provider reports success.</p>
              </article>

              <article className="truth-card truth-card--after">
                <div className="truth-card__label"><span /> With RepoCharter</div>
                <h3>One authored truth. Small, explicit adapters.</h3>
                <div className="source-tree" aria-label="RepoCharter source tree">
                  <p><i>◆</i><b>AGENTS.md</b><em>always loaded</em></p>
                  <p><i>├</i><span>docs/project/</span><em>on demand</em></p>
                  <p><i>├</i><span>.agents/skills/</span><em>on demand</em></p>
                  <p><i>└</i><span>compatibility.json</span><em>mechanical policy</em></p>
                </div>
                <p>Provider files point inward. They do not become competing sources of truth.</p>
              </article>
            </div>
          </div>
        </section>

        <section className="section section--features">
          <div className="shell">
            <div className="section-heading section-heading--center">
              <p className="kicker">Built for real repositories</p>
              <h2>Portable context without hand-wavy safety.</h2>
              <p>RepoCharter separates what every agent can read from what each harness can actually enforce.</p>
            </div>

            <div className="feature-grid">
              <article className="feature-card feature-card--portable">
                <div className="feature-card__icon"><RouteIcon /></div>
                <p className="feature-card__eyebrow">Portable by construction</p>
                <h3>Switch agents. Keep the map.</h3>
                <p>
                  Claude Code, Codex, OpenCode, Hermes Agent, and ZCode all reach the same checked-in
                  instructions and procedures through explicit, reviewable adapters.
                </p>
                <div className="provider-orbit" aria-hidden="true">
                  <span>CL</span><span>CX</span><span>OC</span><span>HA</span><span>ZC</span>
                  <div><LogoMark /></div>
                </div>
              </article>

              <article className="feature-card feature-card--lean">
                <div className="feature-card__icon"><LayersIcon /></div>
                <p className="feature-card__eyebrow">Lean by default</p>
                <h3>Load the task, not the archive.</h3>
                <p>
                  Keep startup rules compact. Pull specifications, decisions, state, and skills only
                  when they matter to the work in front of the agent.
                </p>
                <div className="context-meter">
                  <div className="context-meter__head"><span>Startup context</span><b>compact</b></div>
                  <div className="context-meter__bar"><span /></div>
                  <div className="context-meter__labels"><span>charter</span><span>room for the work</span></div>
                </div>
              </article>

              <article className="feature-card feature-card--proof">
                <div className="feature-card__icon"><ShieldCheckIcon /></div>
                <p className="feature-card__eyebrow">Guardrails with receipts</p>
                <h3>Installed is not the same as enforced.</h3>
                <p>
                  RepoCharter fires real forbidden and benign calls, records provider-backed evidence,
                  and keeps unsupported surfaces explicitly advisory.
                </p>
                <div className="mini-terminal">
                  <p><span>✓</span> destructive command denied</p>
                  <p><span>✓</span> benign command allowed</p>
                  <p><span>✓</span> malformed input failed closed</p>
                  <div><i /> verified for this checkout</div>
                </div>
              </article>
            </div>
          </div>
        </section>

        <section className="section section--steps" id="how-it-works">
          <div className="shell">
            <div className="section-heading section-heading--split">
              <div>
                <p className="kicker">How it works</p>
                <h2>Human-authored truth. Mechanical reach.</h2>
              </div>
              <p>
                RepoCharter does not generate the meaning of your project. It gives that meaning a
                durable structure, connects it to each harness, and checks the connection.
              </p>
            </div>

            <div className="steps">
              {steps.map((step) => (
                <article className="step" key={step.number}>
                  <span className="step__number">{step.number}</span>
                  <div className="step__body">
                    <h3>{step.title}</h3>
                    <p>{step.body}</p>
                    <code>{step.file}</code>
                  </div>
                  <ArrowLong />
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="section section--compatibility" id="compatibility">
          <div className="shell">
            <div className="section-heading section-heading--split">
              <div>
                <p className="kicker">Compatibility, stated plainly</p>
                <h2>Shared context everywhere. Verified enforcement where proven.</h2>
              </div>
              <p>
                Providers expose different controls. RepoCharter reports context reach and safety
                enforcement separately so an adapter never gets credit for a test it did not pass.
              </p>
            </div>

            <div className="provider-grid">
              {providers.map((provider) => (
                <article className={`provider-card ${provider.verified ? "provider-card--verified" : ""}`} key={provider.name}>
                  <div className="provider-card__top">
                    <span className="provider-card__mark">{provider.monogram}</span>
                    <h3>{provider.name}</h3>
                    {provider.verified ? <span className="verified-chip"><i /> proven path</span> : null}
                  </div>
                  <dl>
                    <div><dt>Context</dt><dd><i className="status-dot status-dot--context" />{provider.context}</dd></div>
                    <div><dt>Guardrails</dt><dd><i className={`status-dot ${provider.verified ? "status-dot--verified" : "status-dot--advisory"}`} />{provider.guardrails}</dd></div>
                  </dl>
                </article>
              ))}
            </div>
            <p className="compatibility-note">
              <InfoIcon /> Verified enforcement is earned per provider, checkout, hook hash, and probe matrix.
              Advisory means “use the shared context and pre-commit gate,” not “mechanically blocked.”
            </p>
          </div>
        </section>

        <section className="section section--control">
          <div className="shell control-grid">
            <div className="control-copy">
              <p className="kicker">Your repository stays yours</p>
              <h2>No context service between you and your code.</h2>
              <p>
                The RepoCharter CLI is dependency-free Python 3. It runs offline, vendors into the
                repository, and leaves project truth under human review and version control.
              </p>
              <ul className="check-list">
                <li><CheckBadge /> No hosted memory or required account</li>
                <li><CheckBadge /> Existing hooks and settings are preserved</li>
                <li><CheckBadge /> Dirty worktrees are refused by default</li>
                <li><CheckBadge /> Every mechanical change is previewable</li>
              </ul>
            </div>

            <div className="control-terminal" aria-label="RepoCharter verification output">
              <div className="control-terminal__bar">
                <div className="window-dots"><span /><span /><span /></div>
                <span>verify — your-repo</span>
                <em>python3</em>
              </div>
              <div className="control-terminal__body">
                <p><i>$</i> kit/agentkit verify --repo .</p>
                <div className="terminal-gap" />
                <p><span className="term-muted">schema</span><b>valid</b></p>
                <p><span className="term-muted">context budget</span><b>within limit</b></p>
                <p><span className="term-muted">provider adapters</span><b>reachable</b></p>
                <p><span className="term-muted">declared checks</span><b>passed</b></p>
                <div className="terminal-rule" />
                <p className="term-result"><CheckCircle /> 0 errors · ready for review</p>
              </div>
            </div>
          </div>
        </section>

        <section className="final-cta" id="get-started">
          <div className="final-cta__glow" />
          <div className="shell final-cta__inner">
            <LogoMark />
            <p className="kicker">Start with a read-only baseline</p>
            <h2>Give your agents one map.</h2>
            <p>
              See what loads today before RepoCharter changes a file. Then preview the mechanical
              layer and adopt it on your terms.
            </p>
            <CopyCommand command="kit/agentkit census --repo ~/dev/your-repo" compact />
            <div className="final-cta__actions">
              <a className="button button--primary" href={githubUrl} target="_blank" rel="noreferrer">
                Get RepoCharter on GitHub <ArrowUpRight />
              </a>
              <a className="text-link" href={githubUrl + "#quick-start"} target="_blank" rel="noreferrer">
                Read the quick start <ArrowRight />
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <div className="shell site-footer__inner">
          <div>
            <a className="brand brand--footer" href="#top">
              <LogoMark />
              <span className="brand__word">Repo<span>Charter</span></span>
            </a>
            <p>One repo charter. Every coding agent.</p>
          </div>
          <div className="footer-links">
            <a href="#why">Why</a>
            <a href="#how-it-works">How it works</a>
            <a href="#compatibility">Compatibility</a>
            <a href={githubUrl} target="_blank" rel="noreferrer">GitHub <ArrowUpRight /></a>
          </div>
          <p className="footer-meta">RepoCharter 0.3.0 · CLI compatibility name: agentkit</p>
        </div>
      </footer>

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
    </>
  );
}

function LogoMark() {
  return (
    <svg className="logo-mark" viewBox="0 0 38 38" aria-hidden="true">
      <path d="M10 4.5h12.5L29 11v16.5A5.5 5.5 0 0 1 23.5 33h-13A5.5 5.5 0 0 1 5 27.5V10a5.5 5.5 0 0 1 5-5.5Z" />
      <path d="M22.5 4.5V11H29" />
      <path className="logo-mark__line" d="M11 17h12M11 22h9" />
      <path className="logo-mark__mint" d="M11 27h5" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2.6a9.7 9.7 0 0 0-3.1 18.9c.5.1.7-.2.7-.5v-1.9c-2.8.6-3.4-1.2-3.4-1.2-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 0 1.6 1 1.6 1 .9 1.6 2.4 1.1 2.9.9.1-.7.4-1.1.7-1.3-2.3-.3-4.7-1.1-4.7-5.1 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.8 1a9.5 9.5 0 0 1 5 0c1.9-1.3 2.8-1 2.8-1 .5 1.3.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.9-2.4 4.8-4.7 5.1.4.3.7 1 .7 1.9V21c0 .4.2.6.7.5A9.7 9.7 0 0 0 12 2.6Z" />
    </svg>
  );
}

function ArrowUpRight() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M5 13 13 5M6 5h7v7" /></svg>;
}

function ArrowDown() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M9 3v11M4.5 9.5 9 14l4.5-4.5" /></svg>;
}

function ArrowRight() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M3 9h11M9.5 4.5 14 9l-4.5 4.5" /></svg>;
}

function ArrowLong() {
  return <svg className="arrow-long" viewBox="0 0 30 30" aria-hidden="true"><path d="M5 15h19M17 8l7 7-7 7" /></svg>;
}

function FileIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2.5h8l4 4V21H6z" /><path d="M14 2.5v4h4M9 11h6M9 15h5" /></svg>;
}

function FileMini() {
  return <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M4 1.5h6.5l3.5 3.6v11.4H4z" /><path d="M10.5 1.5v3.6H14" /></svg>;
}

function CheckBadge() {
  return <svg className="check-badge" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" /><path d="m6.7 10 2.1 2.2 4.5-4.6" /></svg>;
}

function CheckCircle() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" /><path d="m6.6 10 2.2 2.2 4.6-4.7" /></svg>;
}

function ShieldCheckIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5 20 6v5.7c0 4.8-3.3 8.2-8 9.8-4.7-1.6-8-5-8-9.8V6z" /><path d="m8.3 12 2.3 2.3 5-5" /></svg>;
}

function RouteIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="5" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="19" r="2.5" /><path d="M7.5 5h4a3 3 0 0 1 3 3v2a3 3 0 0 0 3 3H19M7 6.5l3.5 9.7" /></svg>;
}

function LayersIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5z" /><path d="m4.5 12 7.5 4.2 7.5-4.2M4.5 16l7.5 4.2 7.5-4.2" /></svg>;
}

function InfoIcon() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" /><path d="M10 8.7v5M10 5.8h.01" /></svg>;
}
