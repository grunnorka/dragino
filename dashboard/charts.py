"""Server-rendered inline SVG line chart (no JS, themed via CSS classes in base.html)."""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from statistics import median
from typing import Any

from dashboard.queries import to_utc

WIDTH, HEIGHT = 640, 280
PAD_L, PAD_R, PAD_T, PAD_B = 52, 14, 16, 30
GOOD_QUALITY = (None, "ok", "saturated")
QUALITY_CLASS = {"saturated": "q-sat", "fault": "q-fault", "no_signal": "q-none"}


def fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    a = abs(value)
    if a >= 1000:
        return f"{value:.0f}"
    if a >= 100:
        return f"{value:.1f}"
    if a >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def _x_label(t: datetime, span: timedelta) -> str:
    return t.strftime("%H:%M") if span <= timedelta(days=2) else t.strftime("%m-%d")


def build_chart(
    points: list[dict[str, Any]],
    t0: datetime,
    t1: datetime,
    unit: str,
    *,
    bucket_seconds: int = 0,
) -> dict[str, Any] | None:
    """points: ascending dicts {t, v, q}. Returns {svg, min, max, latest, ...} or None.

    Lines are drawn through good samples only and broken at data gaps and at
    non-good samples; those are drawn as quality-coloured points instead.
    """
    pts = [
        {"t": to_utc(p["t"]), "v": float(p["v"]), "q": p.get("q")}
        for p in points
        if p.get("v") is not None
    ]
    if not pts:
        return None
    t0, t1 = to_utc(t0), to_utc(t1)
    span = t1 - t0
    span_s = max(span.total_seconds(), 1.0)

    good = [p for p in pts if p["q"] in GOOD_QUALITY] or pts
    vmin = min(p["v"] for p in good)
    vmax = max(p["v"] for p in good)
    pad = (vmax - vmin) * 0.1 or max(abs(vmax) * 0.05, 1.0)
    ylo, yhi = vmin - pad, vmax + pad

    plot_w = WIDTH - PAD_L - PAD_R
    plot_h = HEIGHT - PAD_T - PAD_B

    def sx(t: datetime) -> float:
        frac = (t - t0).total_seconds() / span_s
        return PAD_L + min(max(frac, 0.0), 1.0) * plot_w

    def sy(v: float) -> tuple[float, bool]:
        clamped = v < ylo or v > yhi
        v = min(max(v, ylo), yhi)
        return PAD_T + (yhi - v) / (yhi - ylo) * plot_h, clamped

    # Gap threshold: a few typical intervals, never below two buckets.
    deltas = [(b["t"] - a["t"]).total_seconds() for a, b in zip(pts, pts[1:])]
    typical = median(deltas) if deltas else 0.0
    gap_s = max(3 * typical, 2 * bucket_seconds, 120.0)

    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for p in pts:
        if p["q"] not in GOOD_QUALITY:
            if current:
                segments.append(current)
                current = []
            prev = None
            continue
        if prev is not None and (p["t"] - prev["t"]).total_seconds() > gap_s:
            segments.append(current)
            current = []
        current.append(p)
        prev = p
    if current:
        segments.append(current)

    out: list[str] = []
    out.append(
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="Line chart of {escape(unit)} over time" '
        'xmlns="http://www.w3.org/2000/svg">'
    )
    # grid + y labels
    for i in range(5):
        v = ylo + (yhi - ylo) * i / 4
        y, _ = sy(v)
        out.append(f'<line class="gl" x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(
            f'<text class="ax" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{fmt_num(v)}</text>'
        )
    # x labels
    for i in range(5):
        t = t0 + span * (i / 4)
        x = sx(t)
        anchor = "start" if i == 0 else "end" if i == 4 else "middle"
        out.append(
            f'<text class="ax" x="{x:.1f}" y="{HEIGHT - 8}" text-anchor="{anchor}">'
            f"{_x_label(t, span)}</text>"
        )
    out.append(
        f'<text class="ax" x="6" y="{PAD_T - 4}" text-anchor="start">{escape(unit)}</text>'
    )
    # lines
    for seg in segments:
        if len(seg) == 1:
            y, _ = sy(seg[0]["v"])
            out.append(f'<circle class="pt" cx="{sx(seg[0]["t"]):.1f}" cy="{y:.1f}" r="2.5"/>')
            continue
        d = " ".join(
            f'{"M" if i == 0 else "L"}{sx(p["t"]):.1f} {sy(p["v"])[0]:.1f}'
            for i, p in enumerate(seg)
        )
        out.append(f'<path class="ln" d="{d}"/>')
    # quality-coloured points for non-ok samples
    bad = 0
    for p in pts:
        cls = QUALITY_CLASS.get(p["q"] or "")
        if cls is None:
            continue
        bad += 1
        y, clamped = sy(p["v"])
        ring = " clamp" if clamped else ""
        out.append(
            f'<circle class="{cls}{ring}" cx="{sx(p["t"]):.1f}" cy="{y:.1f}" r="3.5">'
            f'<title>{p["t"].strftime("%Y-%m-%d %H:%M")}Z {fmt_num(p["v"])} {escape(unit)} '
            f'({escape(p["q"] or "")})</title></circle>'
        )
    # min / max / latest markers
    good_ids = {id(p) for p in good}
    imin = min((i for i, p in enumerate(pts) if id(p) in good_ids), key=lambda i: pts[i]["v"])
    imax = max((i for i, p in enumerate(pts) if id(p) in good_ids), key=lambda i: pts[i]["v"])
    ilast = len(pts) - 1
    names: dict[int, list[str]] = {}
    names.setdefault(imax, []).append("max")
    if imin != imax:
        names.setdefault(imin, []).append("min")
    names.setdefault(ilast, []).append("latest")
    for idx, parts in names.items():
        p = pts[idx]
        x = sx(p["t"])
        y, _ = sy(p["v"])
        cls = "mk-last" if "latest" in parts else "mk"
        out.append(f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="4.5"/>')
        label = f'{" / ".join(parts)} {fmt_num(p["v"])}'
        below = y < PAD_T + 18 or parts == ["min"]
        ty = y + 17 if below else y - 9
        anchor = "end" if x > PAD_L + plot_w * 0.55 else "start"
        tx = x - 6 if anchor == "end" else x + 6
        out.append(
            f'<text class="lb" x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{escape(label)}</text>'
        )
    out.append("</svg>")

    return {
        "svg": "".join(out),
        "unit": unit,
        "min": vmin,
        "max": vmax,
        "latest": pts[-1]["v"],
        "latest_t": pts[-1]["t"],
        "latest_q": pts[-1]["q"],
        "n": len(pts),
        "bad": bad,
        "gaps": max(len(segments) - 1, 0),
    }
