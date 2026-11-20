const LABELS: Record<string, string> = {
  start: "Start",
  routing: "Routing",
  agent_start: "Agent starts",
  model: "Model",
  tool: "Tool",
  tool_filtered: "Tool filtered by policy",
  unknown_tool: "Unknown tool",
  redaction: "Redaction",
  violation: "Policy violation",
  handoff: "Handoff",
  agent_end: "Agent ends",
  error: "Error",
  end: "End",
};

function describe(e: any): string {
  switch (e.type) {
    case "start": return e.input;
    case "routing": return `→ ${e.target || "nobody"} (${e.reason})`;
    case "agent_start": return `${e.agent} · ${e.model} · tools: ${e.tools.join(", ") || "none"} · ${e.docs} documents · policies: ${e.policy.sources.join(", ") || "none"}`;
    case "model": return `${e.agent}: ${e.tool_calls.length ? `asks for ${e.tool_calls.join(", ")}` : e.text} (${e.tokens_in}+${e.tokens_out} tokens)`;
    case "tool": return `${e.tool}(${JSON.stringify(e.input)}) → ${e.result}`;
    case "tool_filtered": return `${e.tool}: ${e.reason}`;
    case "unknown_tool": return String(e.tool);
    case "redaction": return `${e.count} matches redacted in the answer of ${e.agent}`;
    case "violation": return `${e.kind}: ${e.detail}`;
    case "handoff": return `→ ${e.to}`;
    case "agent_end": return e.output;
    case "error": return e.detail;
    case "fin": return e.output;
    default: return JSON.stringify(e);
  }
}

export default function TraceView({ trace, compact = false }: { trace: any[]; compact?: boolean }) {
  return (
    <ol className={compact ? "trace compact" : "trace"}>
      {trace.map((e, i) => (
        <li key={i} className={`ev ev-${e.type}`}>
          <span className="ms">{e.ms} ms</span>
          <span className="ev-type">{LABELS[e.type] ?? e.type}</span>
          <span className="ev-text">{describe(e)}</span>
        </li>
      ))}
    </ol>
  );
}
