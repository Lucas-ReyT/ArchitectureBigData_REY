const WIDTH = 300;
const HEIGHT = 170;
const PAD_TOP = 22;
const PAD_BOTTOM = 22;
const LABEL_GAP = 14;

function formatEUR(value) {
  return value.toLocaleString("fr-BE", { maximumFractionDigits: 0 }) + " €";
}

/**
 * Petit graphique en barres a un seul axe (une seule metrique) — jamais deux
 * metriques d'echelles differentes sur le meme graphique (cf. regle "one axis").
 * La ligne zero est placee proportionnellement a l'amplitude positive/negative
 * des valeurs pour que les barres negatives disposent aussi de leur espace
 * (sinon elles sortent du cadre si zero est colle en bas).
 */
export default function TrendChart({ title, years, values, colorFor }) {
  const chartHeight = HEIGHT - PAD_TOP - PAD_BOTTOM - LABEL_GAP;
  const maxPos = Math.max(0, ...values, 0.0001);
  const maxNeg = Math.max(0, ...values.map((v) => -v), 0.0001);
  const span = maxPos + maxNeg;

  const zeroY = PAD_TOP + chartHeight * (maxPos / span);
  const step = (WIDTH - 16) / values.length;
  const barWidth = Math.min(36, step - 8);

  return (
    <div>
      <div className="field-label" style={{ marginBottom: 4 }}>{title}</div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" role="img" aria-label={title}>
        <line x1={8} y1={zeroY} x2={WIDTH - 8} y2={zeroY} stroke="var(--baseline)" strokeWidth="1" />
        {values.map((v, i) => {
          const h = (Math.abs(v) / span) * chartHeight;
          const x = 8 + i * step + (step - barWidth) / 2;
          const y = v >= 0 ? zeroY - h : zeroY;
          const isEdge = i === 0 || i === values.length - 1;
          return (
            <g key={i}>
              <rect x={x} y={y} width={barWidth} height={Math.max(h, 2)} rx={2} fill={colorFor(v)} />
              {isEdge && (
                <text
                  x={x + barWidth / 2}
                  y={v >= 0 ? y - 6 : y + h + 12}
                  textAnchor="middle"
                  fontSize="10"
                  fill="var(--text-secondary)"
                >
                  {formatEUR(v)}
                </text>
              )}
              <text
                x={x + barWidth / 2}
                y={HEIGHT - 6}
                textAnchor="middle"
                fontSize="11"
                fill="var(--text-muted)"
              >
                {years[i]}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
