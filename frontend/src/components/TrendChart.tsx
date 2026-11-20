import { useEffect, useRef, useState } from "react";
import { fmt } from "../api";

// Two fixed series: color follows the series, not its position.
const SERIES = [
  { key: "heldout", se: "heldout_se", name: "Held-out", color: "var(--series-1)" },
  { key: "dev", se: "dev_se", name: "Dev", color: "var(--series-2)" },
] as const;

const H = 240;
const M = { top: 16, right: 76, bottom: 44, left: 40 };

export default function TrendChart({ rows }: { rows: any[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, e.contentRect.width)));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const innerW = width - M.left - M.right;
  const innerH = H - M.top - M.bottom;
  const x = (i: number) => M.left + (rows.length === 1 ? innerW / 2 : (i * innerW) / (rows.length - 1));
  const y = (v: number) => M.top + (1 - v) * innerH;
  const band = rows.length === 1 ? innerW : innerW / (rows.length - 1);
  const every = Math.max(1, Math.ceil(rows.length / Math.max(1, Math.floor(innerW / 70))));

  // Direct labels at the end of each line, separated if they collide.
  const last = rows[rows.length - 1];
  const ends = SERIES.filter((s) => last[s.key] != null).map((s) => ({ s, py: y(last[s.key]) })).sort((a, b) => a.py - b.py);
  if (ends.length === 2 && ends[1].py - ends[0].py < 14) {
    const mid = (ends[0].py + ends[1].py) / 2;
    ends[0].py = mid - 7;
    ends[1].py = mid + 7;
  }

  const hovered = hover == null ? null : rows[hover];
  return (
    <figure className="chart">
      <figcaption>
        <b>Score per iteration</b>
        <span className="legend">
          {SERIES.map((s) => (
            <span key={s.key}><i style={{ background: s.color }} />{s.name}</span>
          ))}
        </span>
      </figcaption>
      <div ref={box} className="chart-box" onMouseLeave={() => setHover(null)}>
        <svg width={width} height={H} role="img"
          aria-label={`Dev and held-out score over ${rows.length} iterations. The same data is in the table below.`}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} className={t === 0 ? "axis" : "grid"} />
              <text x={M.left - 8} y={y(t)} className="tick" textAnchor="end" dominantBaseline="middle">{t.toFixed(2)}</text>
            </g>
          ))}
          {rows.map((r, i) => i % every === 0 && (
            <g key={r.iteration}>
              <text x={x(i)} y={H - M.bottom + 16} className="tick" textAnchor="middle">{r.version}</text>
              <text x={x(i)} y={H - M.bottom + 30} className="tick dim" textAnchor="middle">{r.status}</text>
            </g>
          ))}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} className="cross" />}
          {SERIES.map((s) => {
            const pts = rows.map((r, i) => (r[s.key] == null ? null : [x(i), y(r[s.key])])).filter(Boolean) as number[][];
            return (
              <g key={s.key}>
                {pts.length > 1 && (
                  <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={s.color} strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" />
                )}
                {pts.map(([px, py], i) => (
                  <circle key={i} cx={px} cy={py} r={4.5} fill={s.color} className="dot" />
                ))}
              </g>
            );
          })}
          {ends.map(({ s, py }) => (
            <text key={s.key} x={x(rows.length - 1) + 10} y={py} className="end-label" dominantBaseline="middle">
              {s.name} {fmt(last[s.key])}
            </text>
          ))}
          {rows.map((r, i) => (
            <rect key={r.iteration} x={x(i) - band / 2} y={M.top} width={band} height={innerH} fill="transparent"
              onMouseEnter={() => setHover(i)} />
          ))}
        </svg>
        {hovered && (
          <div className="tip" style={x(hover!) > width / 2 ? { right: width - x(hover!) + 12 } : { left: x(hover!) + 12 }}>
            <b>{hovered.version}</b> · {hovered.iteration} · {hovered.status}
            {SERIES.map((s) => (
              <div key={s.key} className="tip-row">
                <i style={{ background: s.color }} />
                <span>{s.name}</span>
                <span className="tip-val">
                  {fmt(hovered[s.key])}{hovered.seeds > 1 ? ` ± ${fmt(hovered[s.se])}` : ""}
                </span>
              </div>
            ))}
            {hovered.gain != null && <div className="muted">Gain in held-out: {hovered.gain >= 0 ? "+" : ""}{hovered.gain.toFixed(3)}</div>}
          </div>
        )}
      </div>
    </figure>
  );
}
