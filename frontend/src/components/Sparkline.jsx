import React from "react";

// Tiny inline sparkline of pass-rate trend (§6.13 card).
export default function Sparkline({ values, width = 120, height = 30 }) {
  const pts = values.filter((v) => v !== null && v !== undefined);
  if (pts.length === 0) return <span className="muted">no runs</span>;
  if (pts.length === 1) {
    return (
      <svg width={width} height={height}>
        <circle cx={width / 2} cy={height / 2} r={3} fill="#5b9dff" />
      </svg>
    );
  }
  const min = Math.min(...pts, 0);
  const max = Math.max(...pts, 1);
  const range = max - min || 1;
  const step = width / (pts.length - 1);
  const coords = pts.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 6) - 3;
    return [x, y];
  });
  const d = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const trendColor = last >= pts[0] ? "#35c46a" : "#ef5f6b";
  return (
    <svg width={width} height={height}>
      <path d={d} fill="none" stroke={trendColor} strokeWidth="2" />
      <circle cx={coords[coords.length - 1][0]} cy={coords[coords.length - 1][1]} r={3} fill={trendColor} />
    </svg>
  );
}
