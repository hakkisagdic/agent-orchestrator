# IDE-native agents

Some agents have no useful CLI: their real surface is an editor extension — VS Code Copilot
agent mode, Cursor's in-editor agent, Qoder, Windsurf, Zed's assistant. Driving those needs
a different bridge than a `--print` flag, and it is worth being honest about what is
achievable.

## Three ways to reach an IDE agent, best first

**1. It also has a CLI. Use that.**
The fastest-moving editors ship a headless CLI alongside the extension, and the CLI is
always the better integration target — no editor process, no GUI automation. Cursor is the
clearest case: the `agent` command runs the same agent headlessly with `-p` and
`--output-format json`. When a CLI exists, ignore the extension and adapt the CLI. See the
`cursor-agent` adapter.

**2. It speaks MCP. Meet it there.**
Since mid-2026, VS Code Copilot agent mode, Cursor and others load MCP servers. That does
not let you *drive* the agent, but it lets the agent drive **agent-orchestrator**: register
`ao mcp serve` in the editor and the in-editor agent can read the mailbox, report status and
request commit authority through the same tools any other agent uses. The human dispatches
in the editor; coordination flows through MCP. This is the realistic bridge for
Copilot-in-VS-Code today.

**3. It only has an extension. Bridge through the workspace and git.**
No CLI, no MCP. The agent still edits files and runs in a real workspace, so treat it like a
constrained cloud lane:

- **Dispatch** is a human action in the editor — there is no headless entry point, and GUI
  automation of an editor is brittle enough that we do not ship it.
- **Observation** is the working tree and git: what changed, what the diff says, whether the
  gates pass. A file-watch on the workspace produces `turn_end`-like events.
- **Coordination** is `AGENTS.md` / editor rules plus the mailbox: most of these agents read
  an always-included instructions file, so the standing directive that makes a bare
  "continue" deliver mail works the same way.
- **Verification** is unchanged and local.

You lose unattended dispatch. You keep observation, coordination and the gate — which is
most of the value.

## What we deliberately do not do

**GUI automation of editors.** Screenshotting and clicking an editor to inject a prompt is
possible and a maintenance sinkhole: it breaks on every UI change, cannot run unattended
safely, and violates the two-writer rule the moment a human touches the same window. If an
agent has neither a CLI nor MCP, the honest answer is "dispatch it yourself; we observe and
verify," not a screen-scraper.

## Per-tool status

| Tool | Best bridge | Notes |
|---|---|---|
| Cursor | **CLI** (`agent`) | Full headless: `-p`, json, `agent ls`, resume. Adapter shipped. |
| VS Code Copilot agent mode | **MCP** | `chat.agent.enabled: true`; loads MCP servers. Dispatch stays in-editor; register `ao mcp serve`. Pre-auth with `gh` on headless/SSH. |
| Qoder | CLI if present, else workspace bridge | IDE-first; companion CLI adapter written but untested. |
| Windsurf | workspace bridge | Cascade agent is extension-native; observe via git. |
| Zed | workspace bridge / MCP | Assistant reads MCP; no headless dispatch surface confirmed. |

## The rule underneath all three

The bridge changes; the invariants do not. However a turn is dispatched — CLI, MCP, or a
human in an editor — the delivered code is untrusted, the gates run locally, and separation
of duties holds. An IDE agent is just a lane with a fancier dispatch button.
