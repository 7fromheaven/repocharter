// Managed by RepoCharter. Installed in the user's OpenCode config only by
// `repocharter self-test --promote-opencode`; never copy this into a repository.
import { createHash } from "node:crypto"
import { spawnSync } from "node:child_process"
import { existsSync, readFileSync, realpathSync } from "node:fs"
import { resolve } from "node:path"
import { fileURLToPath } from "node:url"

const governed = new Set(["bash", "write", "edit", "apply_patch", "patch"])
const adapterFiles = [
  "kit/hooks/opencode/bridge.py",
  ".claude/hooks/agentkit/_policy.py",
  ".claude/hooks/agentkit/pretooluse-bash.sh",
  ".claude/hooks/agentkit/pretooluse-write.py",
  ".claude/hooks/agentkit/posttooluse-write.py",
  ".claude/hooks/agentkit/pretooluse-mcp.py",
]
const pluginPath = fileURLToPath(import.meta.url)
const sanitize = (value) => value.replace(/[^a-zA-Z0-9_-]/g, "_")
const canonicalServer = (value) => value.replace(/[^A-Za-z0-9_.-]/g, "_")

const deepFreeze = (value, seen = new Set()) => {
  if (!value || typeof value !== "object" || seen.has(value)) return value
  seen.add(value)
  for (const item of Object.values(value)) deepFreeze(item, seen)
  return Object.freeze(value)
}

const canonicalJSON = (value) => {
  if (Array.isArray(value)) return `[${value.map(canonicalJSON).join(",")}]`
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJSON(value[key])}`).join(",")}}`
  }
  return JSON.stringify(value)
}

const loadCompatibility = (path) => {
  if (!existsSync(path)) return { value: null, error: `manifest is missing: ${path}` }
  try {
    const value = JSON.parse(readFileSync(path, "utf8"))
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { value: null, error: `manifest is not an object: ${path}` }
    }
    return { value, error: null }
  } catch (error) {
    return { value: null, error: `manifest is invalid: ${error}` }
  }
}

const adapterDigest = (root) => {
  try {
    const digest = createHash("sha256")
    digest.update("user-plugin\0")
    digest.update(readFileSync(pluginPath))
    digest.update("\0")
    for (const relative of adapterFiles) {
      digest.update(relative)
      digest.update("\0")
      digest.update(readFileSync(`${root}/${relative}`))
      digest.update("\0")
    }
    return digest.digest("hex")
  } catch {
    return null
  }
}

const localAttestation = (root) => {
  const git = spawnSync(
    "git", ["-C", root, "rev-parse", "--absolute-git-dir"],
    { encoding: "utf8", timeout: 10_000 },
  )
  if (git.error || git.status !== 0 || !git.stdout.trim()) return null
  const path = resolve(root, git.stdout.trim(), "agentkit/enforcement/opencode.json")
  try {
    const value = JSON.parse(readFileSync(path, "utf8"))
    return value && typeof value === "object" && !Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

const locallyBound = (root, local, digest) => Boolean(
  digest &&
  local?.formatVersion === 1 &&
  local?.checkout === root &&
  local?.provider === "opencode" &&
  local?.evidence?.adapterSha256 === digest
)

const promotionAuthorized = (root, digest) => Boolean(
  digest &&
  process.env.REPO_CHARTER_OPENCODE_PROMOTION_ROOT === root &&
  process.env.REPO_CHARTER_OPENCODE_PROMOTION_SHA256 === digest
)

const attested = (root, compatibility, local, digest) => Boolean(
  locallyBound(root, local, digest) &&
  compatibility?.enforcement?.opencode === "blocking" &&
  compatibility?.agentkitVersion === local?.agentkitVersion &&
  compatibility?.enforcementEvidence?.opencode &&
  canonicalJSON(compatibility.enforcementEvidence.opencode) === canonicalJSON(local.evidence)
)

export const RepoCharter = async ({ worktree, directory }) => {
  const root = realpathSync(worktree || directory)
  const compatibilityPath = `${root}/.agents/compatibility.json`
  const initialCompatibility = loadCompatibility(compatibilityPath)
  const initialDigest = adapterDigest(root)
  const initialLocal = localAttestation(root)
  const initiallyActive = promotionAuthorized(root, initialDigest) ||
    initialCompatibility.value?.enforcement?.opencode === "blocking" ||
    locallyBound(root, initialLocal, initialDigest)
  // Merely cloning or opening a repository must never authorize its Python. Advisory
  // repositories remain inert until explicit promotion; a stale blocking checkout instead
  // gets hooks that refuse calls without executing untrusted repository code.
  if (!initiallyActive) return {}

  const bridge = `${root}/kit/hooks/opencode/bridge.py`
  let servers = []
  const pendingContext = new Map()

  const authorize = () => {
    const digest = adapterDigest(root)
    const loaded = loadCompatibility(compatibilityPath)
    if (promotionAuthorized(root, digest)) {
      if (loaded.error) throw new Error(`[RepoCharter] compatibility ${loaded.error}`)
      return
    }
    const local = localAttestation(root)
    if (loaded.error) throw new Error(`[RepoCharter] compatibility ${loaded.error}`)
    if (!attested(root, loaded.value, local, digest)) {
      throw new Error(
        "[RepoCharter] this OpenCode checkout has no current private attestation; " +
        "run repocharter self-test --promote-opencode",
      )
    }
  }

  const invoke = (phase, tool, args, canonicalTool = null) => {
    authorize()
    if (!existsSync(bridge)) {
      throw new Error(`[RepoCharter] declared OpenCode bridge is missing: ${bridge}`)
    }
    const result = spawnSync("python3", [bridge], {
      cwd: root,
      encoding: "utf8",
      timeout: 20_000,
      maxBuffer: 1024 * 1024,
      input: JSON.stringify({ phase, tool, args, canonicalTool, projectDir: root }),
    })
    if (result.error || result.status !== 0) {
      const detail = result.error?.message || result.stderr?.trim() || `exit ${result.status}`
      throw new Error(`[RepoCharter] adapter bridge failed closed: ${detail}`)
    }
    let verdict
    try {
      verdict = JSON.parse(result.stdout)
    } catch (error) {
      throw new Error(`[RepoCharter] adapter bridge returned malformed JSON: ${error}`)
    }
    if (!verdict || !["allow", "deny"].includes(verdict.decision)) {
      throw new Error("[RepoCharter] adapter bridge returned no valid decision")
    }
    if (verdict.decision === "deny") {
      throw new Error(`[RepoCharter] ${verdict.reason || "call denied"}`)
    }
    return verdict.additional_context || ""
  }

  const mcpIdentity = (tool) => {
    const matches = servers.filter((server) => tool.startsWith(`${sanitize(server)}_`))
    if (!matches.length) return null
    if (matches.length !== 1) {
      throw new Error(`[RepoCharter] OpenCode MCP identity ${tool} matches multiple configured servers`)
    }
    const server = matches[0]
    const collisions = servers.filter((candidate) => sanitize(candidate) === sanitize(server))
    if (collisions.length !== 1) {
      throw new Error(`[RepoCharter] OpenCode MCP server names collide after sanitization: ${collisions.join(", ")}`)
    }
    const exposedTool = tool.slice(sanitize(server).length + 1)
    if (!exposedTool) {
      throw new Error(`[RepoCharter] OpenCode MCP identity ${tool} has no tool suffix`)
    }
    return `mcp__${canonicalServer(server)}__${exposedTool}`
  }

  const appendContext = (output, context) => {
    if (!context) return
    if (typeof output?.output === "string") {
      output.output = `${output.output}\n\n${context}`
      return
    }
    if (output && typeof output === "object") {
      output.repocharter_context = context
    }
  }

  return {
    config: async (...values) => {
      const config = values.at(-1)
      servers = config?.mcp && typeof config.mcp === "object" ? Object.keys(config.mcp) : []
    },
    "tool.execute.before": async (input, output) => {
      const canonical = governed.has(input.tool) ? null : mcpIdentity(input.tool)
      if (!governed.has(input.tool) && canonical === null) return
      const context = invoke("before", input.tool, output.args, canonical)
      if (context) pendingContext.set(`${input.sessionID}:${input.callID}`, context)
      // A later plugin must not replace a benign checked call with a different one.
      deepFreeze(output.args)
      Object.freeze(output)
      if (
        process.env.REPO_CHARTER_OPENCODE_FREEZE_PROBE === "1" &&
        input.tool === "bash" &&
        output.args.command === "touch opencode-mutation-safe.txt"
      ) {
        try { output.args.command = "touch opencode-mutation-ran.txt" } catch {}
        try { output.args = { command: "touch opencode-mutation-ran.txt" } } catch {}
        if (output.args.command !== "touch opencode-mutation-safe.txt") {
          throw new Error("[RepoCharter] later-plugin mutation probe bypassed the frozen call")
        }
        throw new Error("[RepoCharter] later-plugin mutation probe held before execution")
      }
    },
    "tool.execute.after": async (input, output) => {
      if (!governed.has(input.tool)) return
      const key = `${input.sessionID}:${input.callID}`
      const before = pendingContext.get(key) || ""
      pendingContext.delete(key)
      const after = invoke("after", input.tool, input.args || {}, null)
      appendContext(output, [before, after].filter(Boolean).join("\n"))
      deepFreeze(output)
    },
  }
}
