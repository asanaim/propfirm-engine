/** The landing page's picture of the product: an equity line that climbs
from the starting balance to the profit target without touching the
maximum-loss floor, and reaches the summit (funded). The rule lines carry
the real figures of the first challenge when they are known. Drawn once on
load; still for people who prefer reduced motion. */

const W = 1200;
const H = 360;
const START = 236; // y of the starting balance
const TARGET = 96; // y of the profit target
const FLOOR = 318; // y of the maximum loss

// x, y of the equity curve: dips (never to the floor), then the climb
const POINTS: [number, number][] = [
  [0, START], [60, 228], [110, 244], [170, 222], [230, 252], [280, 270], [330, 248],
  [390, 262], [450, 226], [500, 214], [560, 236], [610, 198], [670, 206], [720, 176],
  [780, 190], [830, 152], [890, 162], [940, 128], [1000, 138], [1050, 104], [1110, 74],
];

function path(points: [number, number][]): string {
  const [first, ...rest] = points;
  if (first === undefined) return "";
  let d = `M${first[0]},${first[1]}`;
  let prev = first;
  for (const p of rest) {
    const mx = (prev[0] + p[0]) / 2;
    d += ` C${mx},${prev[1]} ${mx},${p[1]} ${p[0]},${p[1]}`;
    prev = p;
  }
  return d;
}

export function Ridge({ target, maxLoss }: { target?: string | undefined; maxLoss?: string | undefined }) {
  const line = path(POINTS);
  const last = POINTS[POINTS.length - 1] ?? [W, TARGET];
  return (
    <figure className="ridge" aria-label="An evaluation account climbing from its starting balance to the profit target, staying above the maximum loss">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="presentation" focusable="false">
        <defs>
          <linearGradient id="ridge-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="ridge-stroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--accent)" />
            <stop offset="100%" stopColor="var(--summit)" />
          </linearGradient>
        </defs>
        <line className="ridge-rule" x1="0" x2={W} y1={TARGET} y2={TARGET} />
        <line className="ridge-rule ridge-floor" x1="0" x2={W} y1={FLOOR} y2={FLOOR} />
        <path className="ridge-area" d={`${line} L${last[0]},${H} L0,${H} Z`} fill="url(#ridge-fill)" />
        <path className="ridge-line" d={line} pathLength={1} stroke="url(#ridge-stroke)" />
      </svg>
      <span className="ridge-label ridge-label-target" style={{ top: `${(TARGET / H) * 100}%` }}>
        Profit target{target === undefined ? "" : ` +${target}`}
      </span>
      <span className="ridge-label ridge-label-floor" style={{ top: `${(FLOOR / H) * 100}%` }}>
        Maximum loss{maxLoss === undefined ? "" : ` −${maxLoss}`}
      </span>
      <span className="ridge-summit" style={{ left: `${(last[0] / W) * 100}%`, top: `${(last[1] / H) * 100}%` }}>
        <span className="ridge-dot" aria-hidden="true" />
        Funded
      </span>
    </figure>
  );
}
