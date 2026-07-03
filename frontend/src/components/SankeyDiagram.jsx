const WIDTH = 720;
const HEIGHT = 320;
const NODE_WIDTH = 110;
const NODE_X = [24, (WIDTH - NODE_WIDTH) / 2, WIDTH - NODE_WIDTH - 24];
const MAX_NODE_HEIGHT = 200;
const MIN_NODE_HEIGHT = 14;
const TOP = 64;

function formatEUR(value) {
  return Math.round(value).toLocaleString("fr-BE", { maximumFractionDigits: 0 }) + " €";
}

function nodeHeight(value, scale) {
  const h = (Math.abs(value) / scale) * MAX_NODE_HEIGHT;
  return Math.max(h, MIN_NODE_HEIGHT);
}

// Ruban de flux entre deux noeuds : quadrilatere a bords courbes (bezier cubique).
// Aplat a ~30% d'opacite + un trait fin plus sature sur les bords pour qu'il reste
// lisible sur le fond clair (un simple wash a 10% se perdait dans la surface).
function FlowRibbon({ x1, y1, h1, x2, y2, h2, color, delta }) {
  const midX = (x1 + x2) / 2;
  const path = `
    M ${x1},${y1}
    C ${midX},${y1} ${midX},${y2} ${x2},${y2}
    L ${x2},${y2 + h2}
    C ${midX},${y2 + h2} ${midX},${y1 + h1} ${x1},${y1 + h1}
    Z
  `;
  const showDelta = Math.abs(delta) > 0.02 * Math.max(h1, h2, 1) && Math.abs(delta) > 1;
  return (
    <g>
      <path d={path} fill={color} opacity="0.28" />
      <path d={`M ${x1},${y1} C ${midX},${y1} ${midX},${y2} ${x2},${y2}`} stroke={color} strokeWidth="1.5" fill="none" opacity="0.55" />
      <path d={`M ${x1},${y1 + h1} C ${midX},${y1 + h1} ${midX},${y2 + h2} ${x2},${y2 + h2}`} stroke={color} strokeWidth="1.5" fill="none" opacity="0.55" />
      {showDelta && (
        <text x={midX} y={(y1 + h1 / 2 + y2 + h2 / 2) / 2 - 6} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
          {delta < 0 ? "− " : "+ "}{formatEUR(Math.abs(delta))}
        </text>
      )}
    </g>
  );
}

function Node({ x, y, height, color, title, value }) {
  return (
    <g>
      <rect x={x} y={y} width={NODE_WIDTH} height={height} rx={4} fill={color} />
      <text x={x + NODE_WIDTH / 2} y={y - 12} textAnchor="middle" fontSize="15" fontWeight="600" fill="var(--text-primary)">
        {formatEUR(value)}
      </text>
      <text x={x + NODE_WIDTH / 2} y={y + height + 20} textAnchor="middle" fontSize="13" fill="var(--text-muted)">
        {title}
      </text>
    </g>
  );
}

export default function SankeyDiagram({ ca, margeBrute, resultatNet }) {
  const scale = Math.max(Math.abs(ca), Math.abs(margeBrute), Math.abs(resultatNet), 1);

  const hCA = nodeHeight(ca, scale);
  const hMB = nodeHeight(margeBrute, scale);
  const hRN = nodeHeight(resultatNet, scale);

  // Les 3 noeuds sont alignes sur leur centre vertical pour que les rubans restent lisibles.
  const yCA = TOP + (MAX_NODE_HEIGHT - hCA) / 2;
  const yMB = TOP + (MAX_NODE_HEIGHT - hMB) / 2;
  const yRN = TOP + (MAX_NODE_HEIGHT - hRN) / 2;

  const resultColor = resultatNet >= 0 ? "var(--status-good)" : "var(--status-critical)";

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" role="img" aria-label="Sankey compte de resultat">
      <FlowRibbon
        x1={NODE_X[0] + NODE_WIDTH} y1={yCA} h1={hCA}
        x2={NODE_X[1]}             y2={yMB} h2={hMB}
        color="var(--series-1)"
        delta={margeBrute - ca}
      />
      <FlowRibbon
        x1={NODE_X[1] + NODE_WIDTH} y1={yMB} h1={hMB}
        x2={NODE_X[2]}              y2={yRN} h2={hRN}
        color="var(--series-2)"
        delta={resultatNet - margeBrute}
      />

      <Node x={NODE_X[0]} y={yCA} height={hCA} color="var(--series-1)" title="Chiffre d'affaires" value={ca} />
      <Node x={NODE_X[1]} y={yMB} height={hMB} color="var(--series-2)" title="Marge brute" value={margeBrute} />
      <Node x={NODE_X[2]} y={yRN} height={hRN} color={resultColor} title="Resultat net" value={resultatNet} />
    </svg>
  );
}
