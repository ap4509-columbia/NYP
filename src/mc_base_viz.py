# =============================================================================
# mc_base_viz.py
# Monte Carlo versions of the baseline visualizations.
#
# Reads the long-format CSV produced by mc_baseline.run_mc_baseline (expanded
# to include every per-year year_checkpoint scalar as "cp.<field>" and every
# end-of-sim aggregate as "final.<field>"). Each render function computes the
# MEAN across seeds per year (or across seeds for single-number metrics) and
# plots it with an optional ±1 SD band / error bars.
#
# Nothing about N (number of seeds) is hardcoded — every label, footer, and
# summary reads the seed count from the CSV at render time via
# df["seed"].nunique().
#
# Public API: render_all_base_mc(csv, output_dir) → list of saved PNG paths
# =============================================================================

import textwrap
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd


# =============================================================================
# Shared helpers
# =============================================================================

SPAGHETTI_COLOR = "#2C7BB6"
MEAN_COLOR      = "#1F4E79"
ENTRY_COLOR     = "#27AE60"
EXIT_COLOR      = "#C0392B"

# Optional scenario identifier rendered in every footer when set (via
# render_all_base_mc). Baseline pipeline leaves this None → byte-identical
# footer to today.
_scenario_tag: Optional[str] = None


def _adaptive_alpha(n: int, ink: float = 4.0) -> float:
    return float(max(0.04, min(0.5, ink / max(1, n))))


def _pivot_annual_cumulative(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Pull metric, pivot to (year × seed), sorted by year."""
    sub = df[df["metric"] == metric].copy()
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["year", "value"])
    return sub.pivot_table(index="year", columns="seed", values="value",
                           aggfunc="first").sort_index()


def _pivot_annual_delta(df: pd.DataFrame, cumulative_metric: str) -> pd.DataFrame:
    """Year-over-year delta pivot (seed × year of deltas)."""
    pivot = _pivot_annual_cumulative(df, cumulative_metric)
    return pivot.diff().dropna(how="all")


def _final_values(df: pd.DataFrame, metric: str) -> pd.Series:
    """All seeds' final (year=NaN) value for a single metric."""
    sub = df[(df["metric"] == metric) & df["year"].isna()].copy()
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    return sub.set_index("seed")["value"].dropna()


def _n_post_warmup_years(df: pd.DataFrame) -> int:
    """
    Number of post-warmup simulation years in the CSV. Derived dynamically
    from the data (NOT hardcoded), so changing WARMUP_YEARS / SIM_YEARS in
    parameters.py automatically flows through.

    We count distinct years where any cp.* metric is present. That's the
    number of year-checkpoints; since these are cumulative snapshots, the
    effective "coverage span" for averaging final-sim totals is the same
    number of years (end-of-sim total / span ≈ per-year mean).
    """
    years = df.loc[df["year"].notna(), "year"]
    years = pd.to_numeric(years, errors="coerce").dropna()
    n = int(years.nunique())
    return max(1, n)


def _to_annual(series: pd.Series, n_years: int) -> pd.Series:
    """Divide a per-seed end-of-sim cumulative total by years post-warmup to
    get each seed's MEAN ANNUAL value."""
    if series.empty or n_years <= 0:
        return series
    return series.astype(float) / float(n_years)


def _mean_and_sd(pivot: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """Mean + SD per row across columns (seeds)."""
    n_seeds = pivot.shape[1]
    mean = pivot.mean(axis=1)
    sd = pivot.std(axis=1, ddof=1) if n_seeds > 1 else pd.Series(
        np.zeros(len(mean)), index=mean.index
    )
    return mean, sd


def _format_thousands(ax, axis="y"):
    f = mticker.FuncFormatter(
        lambda v, _: f"{v/1e6:.1f}M" if abs(v) >= 1e6
        else f"{v/1e3:.0f}K" if abs(v) >= 1000
        else f"{v:.0f}"
    )
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def _format_usd(ax, axis="y"):
    f = mticker.FuncFormatter(
        lambda v, _: f"${v/1e9:.2f}B" if abs(v) >= 1e9
        else f"${v/1e6:.1f}M" if abs(v) >= 1e6
        else f"${v/1e3:.0f}K" if abs(v) >= 1000
        else f"${v:.0f}"
    )
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def _seed_summary(seeds) -> Tuple[int, str]:
    """Return (n_seeds, 'seeds X–Y') for use in titles and footers. Nothing hardcoded."""
    try:
        seeds_sorted = sorted({int(s) for s in seeds if s is not None})
    except (TypeError, ValueError):
        seeds_sorted = []
    n = len(seeds_sorted)
    rng = f"seeds {seeds_sorted[0]}–{seeds_sorted[-1]}" if seeds_sorted else ""
    return n, rng


def _finalize_figure(
    fig,
    seeds,
    *,
    title: str,
    subtitle: str = "",
    description: str = "",
    wrap_width: int = 160,
    top: float = 0.91,
    bottom: float = 0.18,
    left: float = 0.06,
    right: float = 0.98,
    subtitle_y: float = 0.945,
    title_y: float = 0.985,
) -> None:
    """
    Attach a professional title, subtitle, description, and footer to a figure
    and reserve space for them so nothing overlaps the plot content.

    The description sits in a reserved band at the bottom of the figure,
    BELOW the x-axis label — never overlapping plot content or tick labels.
    """
    n, rng = _seed_summary(seeds)
    fig_h = fig.get_size_inches()[1]

    # Fixed pixel anchors for title/subtitle so they never overlap regardless
    # of figure height. Title sits ~0.30" from top; subtitle ~0.60" from top.
    title_y_eff = 1.0 - max(0.015, 0.30 / fig_h)
    subtitle_y_eff = 1.0 - max(0.055, 0.62 / fig_h)
    fig.suptitle(title, fontsize=14, fontweight="bold", y=title_y_eff)
    if subtitle:
        fig.text(0.5, subtitle_y_eff, subtitle, ha="center",
                 fontsize=11, color="#444", style="italic")

    # Fixed pixel anchors so text never overlaps the plot regardless of fig height.
    foot_y = max(0.010, 0.15 / fig_h)
    desc_y = max(0.035, 0.50 / fig_h)

    wrapped = ""
    n_lines = 0
    if description:
        wrapped = "\n".join(
            textwrap.fill(p.strip(), width=wrap_width)
            for p in description.strip().split("\n") if p.strip()
        )
        n_lines = wrapped.count("\n") + 1
        fig.text(0.5, desc_y, wrapped, ha="center", va="bottom",
                 fontsize=9.5, color="#333", parse_math=False)

    needed_bottom_inches = 1.15 + 0.18 * n_lines
    needed_bottom_frac = needed_bottom_inches / fig_h
    bottom_eff = max(bottom, needed_bottom_frac)

    # Ensure enough top space for title (~0.3") + subtitle (~0.3") + gap.
    needed_top_inches = 1.00 if subtitle else 0.65
    top_eff = min(top, 1.0 - needed_top_inches / fig_h)

    footer_text = f"Monte Carlo across {n} baseline seeds ({rng})."
    if _scenario_tag:
        footer_text = f"{_scenario_tag}  •  {footer_text}"
    fig.text(
        0.5, foot_y,
        footer_text,
        ha="center", fontsize=8, color="#888", style="italic",
    )

    plt.subplots_adjust(top=top_eff, bottom=bottom_eff, left=left, right=right)


def _footer(*args, **kwargs) -> None:
    """Deprecated no-op kept for backward compatibility during refactor;
    renderers should call _finalize_figure(...) instead."""
    return None


def _external_legend(ax, loc: str = "upper right", **kwargs) -> None:
    """
    Place a legend OUTSIDE the axes so it never overlaps the plot content.
    Default: just to the right of the plot, vertically centered near top.
    """
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    defaults = dict(
        frameon=True, framealpha=0.95, edgecolor="#CCC",
        fontsize=9, borderpad=0.6,
    )
    defaults.update(kwargs)
    if loc == "upper right":
        anchor, loc_key = (1.005, 1.0), "upper left"
    elif loc == "lower right":
        anchor, loc_key = (1.005, 0.0), "lower left"
    elif loc == "right":
        anchor, loc_key = (1.005, 0.5), "center left"
    elif loc == "below":
        anchor, loc_key = (0.5, -0.18), "upper center"
    else:
        anchor, loc_key = (1.005, 1.0), "upper left"
    ax.legend(handles, labels, loc=loc_key, bbox_to_anchor=anchor, **defaults)


def _save(fig, viz_dir: str, name: str) -> str:
    Path(viz_dir).mkdir(parents=True, exist_ok=True)
    path = Path(viz_dir) / f"{name}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _grouped_ridge_bars(
    panels: List[Tuple[str, str, pd.Series]],
    seeds,
    viz_dir: str,
    filename: str,
    x_label: str,
    title: str,
    subtitle: str = "",
    description: str = "",
    xscale: str = "linear",
    sort_within_group: bool = True,
    gap_between_groups: int = 6,
    bar_thickness: float = 0.82,
) -> str:
    """
    Shared-X-axis grouped ridge-bar plot. One horizontal bar per simulation
    run, stacked within each category's row-group; groups share the X-axis
    so category sizes can be compared directly.
    """
    panels = [(lbl, col, s) for lbl, col, s in panels
              if pd.to_numeric(s, errors="coerce").dropna().size > 0]
    if not panels:
        return ""

    n_seeds_max = max(pd.to_numeric(s, errors="coerce").dropna().size
                      for _, _, s in panels)
    n_groups = len(panels)

    total_rows = n_groups * (n_seeds_max + gap_between_groups)
    fig_h = float(np.clip(0.02 * total_rows + 3.2, 7.5, 18.0))
    fig, ax = plt.subplots(figsize=(22, fig_h))
    fig.patch.set_facecolor("white")

    group_centers: List[float] = []
    group_labels: List[str] = []
    group_means: List[Tuple[float, float, str]] = []

    y = 0
    for label, color, series in panels:
        vals = pd.to_numeric(series, errors="coerce").dropna().values
        if sort_within_group:
            vals = np.sort(vals)
        y_positions = np.arange(y, y + vals.size)
        ax.barh(y_positions, vals, height=bar_thickness,
                 color=color, alpha=0.82, edgecolor="#333", linewidth=0.25)

        center = float(np.mean(y_positions))
        group_centers.append(center)
        group_labels.append(label)
        group_means.append((center, float(np.mean(vals)), color))
        y = int(y_positions[-1]) + 1 + gap_between_groups

    for y_c, mean, _ in group_means:
        ax.scatter([mean], [y_c], marker="D", s=36,
                    color="white", edgecolor="#1F4E79", linewidth=1.5,
                    zorder=5)

    prev_end = 0
    for i, ((label, color, series), center) in enumerate(zip(panels, group_centers)):
        n = pd.to_numeric(series, errors="coerce").dropna().size
        if i > 0:
            div_y = prev_end + gap_between_groups / 2.0
            ax.axhline(div_y, color="#CCC", ls="--", lw=0.6, alpha=0.7)
        prev_end = center + (n - 1) / 2.0 + 0.5

    ax.set_yticks(group_centers)
    ax.set_yticklabels(group_labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel(x_label, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", alpha=0.2)

    if xscale == "log":
        ax.set_xscale("log")

    xmin, xmax = ax.get_xlim()
    x_text = xmax * (1.02 if xscale == "linear" else 1.08)
    for y_c, mean, _ in group_means:
        ax.text(x_text, y_c, f"mean {mean:,.1f}",
                 va="center", ha="left", fontsize=8.5, fontweight="bold",
                 color="#1F4E79")
    if xscale == "linear":
        ax.set_xlim(xmin, xmax * 1.18)
    else:
        ax.set_xlim(xmin, xmax * 1.35)

    # Height-dependent margins so description never overlaps bars.
    bottom_pad = min(0.20, 1.6 / fig_h)
    top_pad = 1.0 - min(0.12, 1.0 / fig_h)
    _finalize_figure(
        fig, seeds,
        title=title, subtitle=subtitle, description=description,
        top=top_pad, bottom=bottom_pad, left=0.22, right=0.90,
    )
    return _save(fig, viz_dir, filename)


def _multi_histogram_grid(
    panels: List[Tuple[str, str, pd.Series]],
    seeds,
    suptitle: str,
    viz_dir: str,
    filename: str,
    x_label: str,
    ncols: int = 2,
    x_fmt: str = "count",
    footer_extra: str = "",
    share_x: bool = False,
) -> str:
    """
    Small-multiple histograms — one bar chart per category.

    Each panel is an MC distribution histogram built the same way as 17/20/
    25/28/31/32:
      • bins = max(50, n_seeds) → each bar ≈ 1 seed wide
      • Y-axis = Probability (fraction of seeds in that bin)
      • Mean (solid) + ±1 SD (dashed) vertical lines annotated
      • Title of each panel = category name

    This replaces box-plot-with-dots for categorical distributions when the
    user wants to SEE EVERY SEED in bar-chart form.
    """
    # Filter empty panels
    panels = [(lbl, col, s) for lbl, col, s in panels
              if pd.to_numeric(s, errors="coerce").dropna().size > 0]
    if not panels:
        return ""

    n = len(panels)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(5.8 * ncols, 3.6 * nrows),
        squeeze=False,
    )
    fig.patch.set_facecolor("white")

    # Optionally share x across panels (useful for wait-time panels where
    # values are in the same unit/range)
    if share_x:
        all_vals = np.concatenate([
            pd.to_numeric(s, errors="coerce").dropna().values
            for _, _, s in panels
        ])
        if all_vals.size > 0:
            pad = (all_vals.max() - all_vals.min()) * 0.05 or 0.01
            xmin = all_vals.min() - pad
            xmax = all_vals.max() + pad
        else:
            xmin, xmax = 0, 1

    for i, (label, color, series) in enumerate(panels):
        r, c = i // ncols, i % ncols
        ax = axes[r][c]
        _histogram_panel(ax, series, color, f"{label}", x_label, x_fmt=x_fmt)
        if share_x:
            ax.set_xlim(xmin, xmax)

    # Hide empty subplots if any
    for i in range(n, nrows * ncols):
        r, c = i // ncols, i % ncols
        axes[r][c].axis("off")

    fig.suptitle(suptitle, fontsize=13, fontweight="bold", y=0.998)
    _footer(fig, seeds, footer_extra)
    plt.tight_layout(rect=(0, 0.02, 1, 0.975))
    return _save(fig, viz_dir, filename)


def _box_with_strip(
    ax,
    per_seed_list: List[pd.Series],
    colors: List[str],
    orientation: str = "horizontal",
    whis=(5, 95),
    box_width: float = 0.55,
    strip_jitter: float = 0.22,
    strip_size: float = 10.0,
    strip_alpha: float = 0.45,
) -> List[float]:
    """
    Hybrid "box plot + translucent strip" for categorical distributions.

    Each category gets:
      • A box plot (box = IQR, whiskers = 5-95%) — summarizes distribution.
      • A hollow-circle strip on top — every seed shown as ONE dot so the user
        can see each replicate, not just the summary.
      • A white diamond at the mean (distinct from the box's median line).

    Returns the list of category means in order (so callers can annotate).
    Works horizontally (categories on y-axis) or vertically (on x-axis).
    """
    positions = list(range(1, len(per_seed_list) + 1))
    vert = (orientation == "vertical")

    bp = ax.boxplot(
        [pd.to_numeric(s, errors="coerce").dropna().values for s in per_seed_list],
        positions=positions, vert=vert, widths=box_width,
        patch_artist=True, whis=whis, showfliers=False,
        medianprops=dict(color="white", linewidth=1.6, zorder=4),
        whiskerprops=dict(color="#555", linewidth=1.0),
        capprops=dict(color="#555", linewidth=1.0),
        boxprops=dict(linewidth=0.8, edgecolor="#222"),
    )
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.70)

    # Per-seed strip (hollow circles, wider jitter). This is the "show every
    # seed" layer — sits ON TOP of the box so outliers are identifiable.
    means: List[float] = []
    for i, series in enumerate(per_seed_list):
        pos = positions[i]
        col = colors[i]
        v = pd.to_numeric(series, errors="coerce").dropna().values
        if v.size == 0:
            means.append(0.0)
            continue
        means.append(float(np.mean(v)))
        rng = np.random.default_rng(1000 + i)
        jit = rng.uniform(-strip_jitter, strip_jitter, v.size)
        if vert:
            ax.scatter(
                np.full(v.size, pos) + jit, v,
                s=strip_size, facecolors="none", edgecolors=col,
                linewidths=0.9, alpha=strip_alpha, zorder=5,
            )
        else:
            ax.scatter(
                v, np.full(v.size, pos) + jit,
                s=strip_size, facecolors="none", edgecolors=col,
                linewidths=0.9, alpha=strip_alpha, zorder=5,
            )

    # Mean as a white-filled diamond (distinct from the median line).
    for i, m in enumerate(means):
        pos = positions[i]
        if vert:
            ax.scatter([pos], [m], marker="D", s=42, color="white",
                        edgecolor="#1F4E79", linewidth=1.5, zorder=6)
        else:
            ax.scatter([m], [pos], marker="D", s=42, color="white",
                        edgecolor="#1F4E79", linewidth=1.5, zorder=6)

    return means


def _spaghetti_with_mean(ax, pivot: pd.DataFrame, color: str = SPAGHETTI_COLOR,
                        mean_color: str = MEAN_COLOR, mean_label: str = "mean") -> int:
    """Plot one thin line per seed, a ±1 SD band, and a bold mean line."""
    n = pivot.shape[1]
    alpha = _adaptive_alpha(n)
    for seed in pivot.columns:
        ax.plot(pivot.index, pivot[seed], color=color,
                alpha=alpha, linewidth=0.9, zorder=2)
    mean, sd = _mean_and_sd(pivot)
    if n > 1:
        ax.fill_between(mean.index, mean - sd, mean + sd,
                        color=color, alpha=0.18, zorder=1,
                        label="±1 SD across seeds")
    ax.plot(mean.index, mean, color=mean_color, linewidth=2.6,
            zorder=3, label=f"{mean_label} (n={n})")
    return n


# =============================================================================
# Time-series vizzes
# =============================================================================

def render_pool_size(df: pd.DataFrame, viz_dir: str) -> str:
    """
    01_pool_size — pool size + annual entries/exits, all as MC spaghetti.

    AGGREGATION — TOP panel (pool size):
      • Each thin line = ONE seed's pool-size snapshot per year.
      • Bold line = MEAN across seeds at each simulation year
        (i.e. mean of per-year snapshots, NOT a mean of annual means).
    AGGREGATION — BOTTOM panel (annual entries / exits):
      • Entries(year)_seed = Δ cum_arrivals for that (seed, year).
      • Exits(year)_seed   = Entries − Δ pool_size for that (seed, year).
      • Bold lines = MEAN across seeds per year.
    """
    import parameters as cfg
    pool = _pivot_annual_cumulative(df, "cp.pool_size")
    n_pts = _pivot_annual_cumulative(df, "cp.cum_n_patients")
    if pool.empty or n_pts.empty:
        return ""

    n_seeds = pool.shape[1]
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(22, 10), sharex=True,
        gridspec_kw={"height_ratios": [2, 1]}
    )
    fig.patch.set_facecolor("white")

    _spaghetti_with_mean(ax_top, pool, color="#1565C0", mean_color="#0B3A74",
                         mean_label="Average pool size")
    ax_top.axhline(cfg.INITIAL_POOL_SIZE, color="gray", lw=1, ls=":", alpha=0.5,
                   label=f"Initial pool size ({cfg.INITIAL_POOL_SIZE:,})")
    ax_top.set_ylabel("Active patients in pool", fontsize=10)
    _format_thousands(ax_top)
    pool_lo = float(np.nanmin(pool.values))
    pool_hi = float(np.nanmax(pool.values))
    pad = (pool_hi - pool_lo) * 0.08 or 1.0
    ax_top.set_ylim(max(0, pool_lo - pad), pool_hi + pad)
    ax_top.grid(axis="y", alpha=0.2)
    ax_top.spines[["top", "right"]].set_visible(False)
    _external_legend(ax_top, loc="upper right")

    entries = n_pts.diff().dropna(how="all")
    pool_delta = pool.diff().dropna(how="all")
    exits = entries - pool_delta

    _spaghetti_with_mean(ax_bot, entries, color=ENTRY_COLOR, mean_color="#0B5E28",
                          mean_label="Average annual entries")
    _spaghetti_with_mean(ax_bot, -exits, color=EXIT_COLOR, mean_color="#7B1F13",
                          mean_label="Average annual exits")
    ax_bot.axhline(0, color="#333", lw=0.6)
    ax_bot.set_xlabel("Simulation year", fontsize=10)
    ax_bot.set_ylabel("Annual entries (+) / exits (−)", fontsize=10)
    _format_thousands(ax_bot)
    e_vals = entries.values.flatten()
    x_vals = (-exits).values.flatten()
    combined = np.concatenate([e_vals[~np.isnan(e_vals)],
                               x_vals[~np.isnan(x_vals)]])
    if combined.size:
        amp = float(np.nanmax(np.abs(combined))) * 1.08
        ax_bot.set_ylim(-amp, amp)
    ax_bot.grid(axis="y", alpha=0.2)
    ax_bot.spines[["top", "right"]].set_visible(False)
    _external_legend(ax_bot, loc="upper right")

    _finalize_figure(
        fig, df["seed"].unique(),
        title="Population Pool Size Over Time",
        subtitle="Active screening population, annual entries, and annual exits",
        description=(
            f"The pool is the set of patients currently eligible for cancer screening at NYP. "
            f"It grows each year as new patients enter eligibility (aging in, new insurance, moving into the catchment area) "
            f"and shrinks as patients exit (aging out, mortality, relocation, loss to follow-up, or attrition). "
            f"Each of the {n_seeds} thin lines is one full 80-year simulation run — it shows that run's year-by-year "
            f"snapshot. Bold lines show the average value at each year across all {n_seeds} runs."
        ),
        top=0.92, bottom=0.14, left=0.06, right=0.88,
    )
    return _save(fig, viz_dir, "01_pool_size")


def render_exit_breakdown(df: pd.DataFrame, viz_dir: str) -> str:
    """
    02_exit_breakdown — small-multiple HISTOGRAMS, one per exit reason.
    Per-seed value = MEAN of annual Δ in that reason's cumulative count over
    post-warmup years (warmup years are excluded because cp.cum_* snapshots
    at year=WARMUP_YEARS set the baseline that each year's Δ starts from).
    """
    # Use cp.cum_exits_by_reason.<reason> per-year metrics; mean of annual Δ
    reason_prefix = "cp.cum_exits_by_reason."
    metrics = sorted(m for m in df["metric"].unique()
                     if isinstance(m, str) and m.startswith(reason_prefix))
    if not metrics:
        return ""

    reasons: Dict[str, pd.Series] = {}
    for m in metrics:
        reason = m[len(reason_prefix):]
        s = _per_seed_annual_mean(df, m)
        if not s.empty:
            reasons[reason] = s

    if not reasons:
        return ""

    ordered = sorted(reasons.items(), key=lambda kv: -kv[1].mean())
    palette = plt.cm.Set2(np.linspace(0, 1, len(ordered)))
    panels = [(f"{lbl}", tuple(palette[i]), v) for i, (lbl, v) in enumerate(ordered)]
    n_seeds = df["seed"].nunique()

    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="02_exit_breakdown",
        xscale="log",
        x_label="Average annual exits per simulation run (log scale)",
        title="Annual Patient Exits by Reason",
        subtitle="Average number of patients leaving the pool each year, per reason",
        description=(
            f"Patients leave the NYP screening pool for different reasons each year. Reasons include aging out "
            f"of the screening age range, death, relocating out of the catchment area, losing insurance coverage, "
            f"general attrition (administrative churn), and loss to follow-up from the screening queues. "
            f"Each horizontal bar is one of {n_seeds} simulation runs; its length is that run's average annual exit "
            f"count for that reason (computed as the yearly change in cumulative exits, averaged over the 70 years "
            f"after the 10-year warmup). The shared log-scaled X-axis lets you compare the relative size of each "
            f"reason at a glance — larger bars = more frequent exit reasons."
        ),
    )


def render_retention(df: pd.DataFrame, viz_dir: str) -> str:
    """
    03_retention — retention curve per seed, drawn as a translucent area.

    RETENTION DEFINITION (per seed, per year):
        retention_pct(year) = 100 × (1 − cum_exited / cum_arrivals)
      i.e. the share of all patients who entered through year X that are
      still in the system at year X.

    AGGREGATION:
      • One TRANSLUCENT FILLED AREA per seed (α ≈ 0.035 each).
      • Overlap → darker region. Where many seeds agree, the area appears
        opaque; where seeds diverge, it's pale.
      • Bold line = MEAN across seeds at each year (mean of per-year
        retention %, not a mean of annual means).
    """
    exited = _pivot_annual_cumulative(df, "cp.cum_exited")
    arrivals = _pivot_annual_cumulative(df, "cp.cum_arrivals")
    if exited.empty or arrivals.empty:
        return ""

    # Align and compute per-seed retention per year
    common_years = exited.index.intersection(arrivals.index)
    exited = exited.loc[common_years]
    arrivals = arrivals.loc[common_years]
    with np.errstate(divide="ignore", invalid="ignore"):
        retention = 100.0 * (1.0 - exited / arrivals)
    retention = retention.replace([np.inf, -np.inf], np.nan).dropna(how="all")
    if retention.empty:
        return ""

    n_seeds = retention.shape[1]

    fig, ax = plt.subplots(figsize=(22, 7))
    fig.patch.set_facecolor("white")

    _spaghetti_with_mean(
        ax, retention,
        color="#2C7BB6", mean_color="#0B3A74",
        mean_label="Average retention",
    )

    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Retention (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    y_min_obs = float(np.nanmin(retention.values))
    y_lo = max(0.0, np.floor((y_min_obs - 5) / 5) * 5)
    ax.set_ylim(y_lo, 100)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")

    _finalize_figure(
        fig, df["seed"].unique(),
        title="Patient Retention Over Time",
        subtitle="Share of patients who entered the pool and are still eligible at each year",
        description=(
            f"Retention at year X = share of all patients who have ever entered the NYP screening pool and are "
            f"still active at year X (not yet lost to death, aging out, relocation, insurance loss, or attrition). "
            f"A retention of 70% at year 50 means 70% of everyone who ever entered is still being tracked by year 50. "
            f"Each of the {n_seeds} thin lines is one simulation run's year-by-year retention curve. The bold line "
            f"is the average retention value at each year across all runs."
        ),
        top=0.90, bottom=0.18, left=0.06, right=0.88,
    )
    return _save(fig, viz_dir, "03_retention")


def render_mortality(df: pd.DataFrame, viz_dir: str) -> str:
    """04_mortality — annual death count over time."""
    delta = _pivot_annual_delta(df, "cp.cum_mortality")
    if delta.empty:
        return ""

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("white")
    n_seeds = _spaghetti_with_mean(ax, delta, mean_label="Average annual deaths")
    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Deaths per year", fontsize=10)
    _format_thousands(ax)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")

    _finalize_figure(
        fig, df["seed"].unique(),
        title="Annual Mortality Count",
        subtitle="Deaths per year within the NYP screening population",
        description=(
            f"Total deaths each year among patients in the screening pool — includes cancer mortality "
            f"(from cervical, lung, or other cancers detected by the program) and all-cause mortality "
            f"(from any other cause while the patient is still in the pool). "
            f"Each of the {n_seeds} thin lines is one simulation run's annual death count at each year; "
            f"the bold line is the average annual deaths across all runs."
        ),
        top=0.90, bottom=0.20, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "04_mortality")


def render_entries_exits(df: pd.DataFrame, viz_dir: str) -> str:
    """08_entries_exits — cumulative arrivals and exits, two spaghettis."""
    arrivals = _pivot_annual_cumulative(df, "cp.cum_arrivals")
    exited = _pivot_annual_cumulative(df, "cp.cum_exited")
    if arrivals.empty and exited.empty:
        return ""

    fig, ax = plt.subplots(figsize=(12, 5.5))
    fig.patch.set_facecolor("white")

    n_seeds = 0
    if not arrivals.empty:
        _spaghetti_with_mean(ax, arrivals,
                              color=ENTRY_COLOR, mean_color="#0B5E28",
                              mean_label="Arrivals mean")
        n_seeds = arrivals.shape[1]
    if not exited.empty:
        _spaghetti_with_mean(ax, exited,
                              color=EXIT_COLOR, mean_color="#7B1F13",
                              mean_label="Exits mean")
        n_seeds = max(n_seeds, exited.shape[1])

    ax.set_xlabel("Simulation Year", fontsize=10)
    ax.set_ylabel("Cumulative patients (count)", fontsize=10)
    ax.set_title(
        "Cumulative Entries & Exits — one line per seed; "
        "mean = average of per-year cumulative totals across seeds",
        fontsize=12, fontweight="bold",
    )
    _format_thousands(ax)
    ax.legend(fontsize=9, loc="best", framealpha=0.9)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _footer(fig, df["seed"].unique(),
            "AGGREGATION: per seed → per-year CUMULATIVE arrivals / exits. "
            "Bold line = MEAN across seeds at each year (not a mean of "
            "annual means).")
    plt.tight_layout()
    return _save(fig, viz_dir, "08_entries_exits")


def _per_seed_annual_mean(df: pd.DataFrame, cum_metric: str) -> pd.Series:
    """Per-seed mean annual value (Δ / year) for a cumulative metric, post-warmup."""
    delta = _pivot_annual_delta(df, cum_metric)
    if delta.empty:
        return pd.Series(dtype=float)
    return delta.mean(axis=0)  # one number per seed (column)


def _fmt_value(v: float, fmt: str) -> str:
    if fmt == "usd":
        if abs(v) >= 1e9:
            return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.2f}M"
        if abs(v) >= 1e3:
            return f"${v/1e3:.0f}K"
        return f"${v:.0f}"
    if fmt == "days":
        return f"{v:.3f} d"
    if abs(v) >= 1e6:
        return f"{v/1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:.1f}"


def _histogram_panel(ax, values: pd.Series, color: str, x_label: str,
                     x_fmt: str = "count") -> Tuple[float, float, int]:
    """
    Histogram of one number per simulation run. Returns (mean, sd, n) for the
    caller to fold into its description.

    Bars use fine bins (each bin ≈ 1 run wide). Mean and ±1 SD are drawn as
    vertical reference lines on the plot; the caller is responsible for
    labeling them via _external_legend or by annotating the description.
    """
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center",
                transform=ax.transAxes)
        return 0.0, 0.0, 0
    mean = float(v.mean())
    sd = float(v.std(ddof=1)) if v.size > 1 else 0.0
    n = int(v.size)

    bins = int(max(50, v.size))
    weights = np.ones_like(v.values, dtype=float) / float(v.size)
    ax.hist(v.values, bins=bins, weights=weights, color=color, alpha=0.85,
            edgecolor=color, linewidth=0.4,
            label=f"Per-simulation values (n={n})")

    ax.axvline(mean, color="#1F4E79", linewidth=2.4, zorder=3,
               label=f"Mean = {_fmt_value(mean, x_fmt)}")
    if sd > 0:
        ax.axvline(mean - sd, color="#888", linewidth=1.0,
                   linestyle="--", zorder=2,
                   label=f"±1 standard deviation = {_fmt_value(sd, x_fmt)}")
        ax.axvline(mean + sd, color="#888", linewidth=1.0,
                   linestyle="--", zorder=2)

    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel("Probability (share of runs)", fontsize=10)
    if x_fmt == "usd":
        _format_usd(ax, axis="x")
    elif x_fmt == "count":
        _format_thousands(ax, axis="x")
    elif x_fmt == "days":
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _: f"{v:.3f}"))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}"))
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    return mean, sd, n


def render_annual_screening_volume(df: pd.DataFrame, viz_dir: str) -> str:
    """
    08_annual_screening_volume — cervical + LDCT on ONE chart with shared
    X-axis (ridge-bar layout) so the two modalities can be compared directly.
    """
    cerv = _per_seed_annual_mean(df, "cp.cum_cervical")
    lung = _per_seed_annual_mean(df, "cp.cum_lung")

    panels: List[Tuple[str, str, pd.Series]] = []
    if not cerv.empty:
        panels.append(("Cervical", "#8E44AD", cerv))
    if not lung.empty:
        panels.append(("Lung (LDCT)", "#2980B9", lung))
    if not panels:
        return ""

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="08_annual_screening_volume",
        xscale="log",
        x_label="Average annual screenings per simulation run (log scale)",
        title="Annual Screening Volume by Pathway",
        subtitle="Cervical vs lung primary-screening counts per year",
        description=(
            f"Total primary screenings performed each year, split by cancer pathway. "
            f"Cervical = cytology + HPV-alone + co-test combined. Lung = LDCT (low-dose CT). "
            f"Cervical volume is roughly 6× lung volume, so a log scale is used so both groups stay readable. "
            f"Each bar is one of {n_seeds} simulation runs; its length is that run's average annual screenings "
            f"for the pathway, computed from the yearly change in the cumulative count averaged over the 70 "
            f"post-warmup years."
        ),
    )


def _uptake_over_time(df: pd.DataFrame, cum_screen_metric: str) -> pd.DataFrame:
    """Per-year uptake rate = (annual screenings / pool size) × 100, per seed."""
    cum = _pivot_annual_cumulative(df, cum_screen_metric)
    pool = _pivot_annual_cumulative(df, "cp.pool_size")
    if cum.empty or pool.empty:
        return pd.DataFrame()
    delta = cum.diff()
    # Align pool index to delta (drop the first year which has NaN for diff)
    pool_aligned = pool.reindex(delta.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        uptake = 100.0 * delta / pool_aligned
    return uptake.dropna(how="all")


def render_cervical_uptake(df: pd.DataFrame, viz_dir: str) -> str:
    """09_cervical_uptake — annual cervical uptake rate (%)."""
    uptake = _uptake_over_time(df, "cp.cum_cervical")
    if uptake.empty:
        return ""
    n_seeds = uptake.shape[1]
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("white")
    _spaghetti_with_mean(ax, uptake, color="#8E44AD", mean_color="#5B2A6E",
                        mean_label="Average annual uptake")
    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Cervical uptake rate (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Cervical Screening Uptake Rate",
        subtitle="Share of the eligible pool screened for cervical cancer each year",
        description=(
            f"Uptake rate = cervical screenings completed in a given year ÷ eligible pool size at that year. "
            f"Eligibility is based on age (screening-age women) and active pool membership. A higher uptake "
            f"rate means more of the eligible population is actually being reached. "
            f"Each of the {n_seeds} thin lines is one simulation run's annual uptake rate; the bold line is "
            f"the average uptake rate at each year across all runs."
        ),
        top=0.90, bottom=0.20, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "09_cervical_uptake")


def render_lung_uptake(df: pd.DataFrame, viz_dir: str) -> str:
    """10_lung_uptake — annual lung uptake rate (%)."""
    uptake = _uptake_over_time(df, "cp.cum_lung")
    if uptake.empty:
        return ""
    n_seeds = uptake.shape[1]
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("white")
    _spaghetti_with_mean(ax, uptake, color="#2980B9", mean_color="#154A73",
                        mean_label="Average annual uptake")
    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Lung uptake rate (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Lung Screening Uptake Rate",
        subtitle="Share of the eligible pool receiving LDCT each year",
        description=(
            f"Uptake rate = LDCT (low-dose CT) screenings completed in a given year ÷ eligible pool size at that year. "
            f"Lung-screening eligibility follows USPSTF guidelines: patients aged 50–80 with a 20+ pack-year "
            f"smoking history who either currently smoke or quit within the past 15 years. "
            f"Each of the {n_seeds} thin lines is one simulation run; the bold line is the average uptake rate "
            f"at each year across all runs."
        ),
        top=0.90, bottom=0.20, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "10_lung_uptake")


def render_first_stage_screenings(df: pd.DataFrame, viz_dir: str) -> str:
    """
    11_first_stage_screenings — ridge-bar plot across first-stage modalities.
    One horizontal bar per seed, grouped by modality, shared X-axis so
    volumes are directly comparable across modalities.
    """
    modalities = [
        ("Cytology",    "cp.cum_cytology",   "#8E44AD"),
        ("HPV-alone",   "cp.cum_hpv_alone",  "#16A085"),
        ("LDCT (lung)", "cp.cum_ldct",       "#2980B9"),
    ]
    panels: List[Tuple[str, str, pd.Series]] = []
    for name, metric, color in modalities:
        s = _per_seed_annual_mean(df, metric)
        if s.empty:
            continue
        panels.append((name, color, s))

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="11_first_stage_screenings",
        x_label="Average annual tests per simulation run",
        title="First-Stage Screenings by Modality",
        subtitle="Annual volume for each primary-screening test type",
        description=(
            f"First-stage (primary) screenings are the entry point to each cancer pathway at NYP. "
            f"Cytology (Pap test) and HPV-alone are two parallel cervical primary screens; patients are assigned "
            f"to one based on age and screening protocol. LDCT (low-dose CT) is the lung primary screen for "
            f"USPSTF-eligible patients. "
            f"Each bar is one of {n_seeds} simulation runs; its length is that run's average annual volume for "
            f"the modality, computed from the yearly change in the cumulative count averaged over the 70 "
            f"post-warmup years."
        ),
    )


def _per_seed_mean_of_annual_metric(df: pd.DataFrame, metric: str) -> pd.Series:
    """Per-seed mean value (mean across post-warmup years) for an annual metric."""
    sub = df[df["metric"] == metric].copy()
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["year", "value"])
    return sub.groupby("seed")["value"].mean()


def render_annual_revenue(df: pd.DataFrame, viz_dir: str) -> str:
    """
    16_annual_revenue — distribution of per-seed mean annual realized revenue
    (one dot per seed, histogram on x = $/year).
    """
    per_seed = _per_seed_mean_of_annual_metric(df, "annual_realized_revenue_usd")
    if per_seed.empty:
        return ""
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("white")
    mean, sd, n = _histogram_panel(
        ax, per_seed, "#2C7BB6",
        x_label="Average annual realized revenue per simulation run (USD)",
        x_fmt="usd",
    )
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Annual Realized Revenue",
        subtitle="Distribution of average annual billed revenue across simulation runs",
        description=(
            f"Realized revenue is revenue NYP actually collects: sum of (procedures completed × procedure price), "
            f"scaled up from the simulation pool to the full NYP addressable population. "
            f"For each of the {n} simulation runs, we compute the average annual realized revenue across the "
            f"70 post-warmup years — that gives one dollar value per run. The histogram shows how those "
            f"{n} per-run averages are distributed. "
            f"The dark vertical line is the mean across runs ({_fmt_value(mean, 'usd')}); the dashed lines are ±1 "
            f"standard deviation ({_fmt_value(sd, 'usd')})."
        ),
        top=0.90, bottom=0.22, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "16_annual_revenue")


def render_revenue_capture(df: pd.DataFrame, viz_dir: str) -> str:
    """
    17_revenue_capture — one stacked horizontal bar per seed, with three
    segments:
      • GREEN  = Realized revenue  (actual procedures × procedure prices)
      • ORANGE = Foregone — LTFU   (patients lost from primary / secondary
                                     / unscreened queues × price at their
                                     last served step)
      • RED    = Foregone — unserved (intake arrivals that never received
                                      any service × cervical-screen price)

    All values are POST-WARMUP TOTALS (last-year cumulative − first-year
    cumulative) × PROCEDURE_REVENUE × POPULATION_SCALE_FACTOR. Bar totals
    differ slightly seed-to-seed, so a shared X-axis makes both the
    TOTAL ADDRESSABLE revenue and the SPLIT between categories directly
    comparable across seeds.
    """
    import parameters as cfg
    scale = cfg.POPULATION_SCALE_FACTOR
    rev = cfg.PROCEDURE_REVENUE
    avg_cerv_screen = (rev["cytology"] + rev["hpv_alone"]) / 2.0

    def _post_warmup_total(metric: str) -> pd.Series:
        """Per-seed (last-year cum − first-year cum) for a cp.cum_* metric."""
        p = _pivot_annual_cumulative(df, metric)
        if p.empty:
            return pd.Series(dtype=float)
        return (p.iloc[-1] - p.iloc[0]).dropna()

    # ── REALIZED ────────────────────────────────────────────────────────────
    realized = (
        _post_warmup_total("cp.cum_cervical")      * avg_cerv_screen +
        _post_warmup_total("cp.cum_colposcopy")    * rev["colposcopy"] +
        _post_warmup_total("cp.cum_leep")          * rev["leep"] +
        _post_warmup_total("cp.cum_ldct")          * rev["ldct"] +
        _post_warmup_total("cp.cum_lung_biopsy")   * rev["lung_biopsy"] +
        _post_warmup_total("cp.cum_lung_treatment")* rev["lung_treatment"]
    ) * scale

    # ── FOREGONE — LTFU ────────────────────────────────────────────────────
    ltfu_foregone = (
        _post_warmup_total("cp.cum_ltfu_unscreened")    * avg_cerv_screen +
        _post_warmup_total("cp.cum_ltfu_queue_primary") * avg_cerv_screen +
        _post_warmup_total("cp.cum_ltfu_queue_secondary") * rev["colposcopy"]
    ) * scale

    # ── FOREGONE — UNSERVED INTAKE ─────────────────────────────────────────
    intake_total  = _post_warmup_total("cp.cum_intake_total")
    intake_served = _post_warmup_total("cp.cum_intake_served")
    # Align then compute
    seeds_all = sorted(set(intake_total.index) & set(intake_served.index))
    unserved_count = (intake_total.loc[seeds_all]
                      - intake_served.loc[seeds_all]).clip(lower=0)
    unserved_foregone = unserved_count * avg_cerv_screen * scale

    # Align all three on the same seed index
    seeds = sorted(set(realized.index)
                   & set(ltfu_foregone.index)
                   & set(unserved_foregone.index))
    if not seeds:
        return ""
    realized = realized.loc[seeds]
    ltfu_foregone = ltfu_foregone.loc[seeds]
    unserved_foregone = unserved_foregone.loc[seeds]

    # Sort seeds by REALIZED-share ascending — creates a visual gradient
    total = realized + ltfu_foregone + unserved_foregone
    realized_share = realized / total.replace(0, np.nan)
    order = realized_share.sort_values(na_position="last").index

    realized_o    = realized.reindex(order).values
    ltfu_o        = ltfu_foregone.reindex(order).values
    unserved_o    = unserved_foregone.reindex(order).values
    n = len(order)

    fig_h = float(np.clip(0.035 * n + 4.5, 6.5, 11.0))
    fig, ax = plt.subplots(figsize=(15, fig_h))
    fig.patch.set_facecolor("white")

    GREEN  = "#6AA84F"
    ORANGE = "#E8A853"
    RED    = "#C0392B"
    y = np.arange(n)
    ax.barh(y, realized_o, color=GREEN,
             label="Realized revenue",
             edgecolor="none", linewidth=0, height=1.0)
    ax.barh(y, ltfu_o, left=realized_o, color=ORANGE,
             label="Foregone — LTFU",
             edgecolor="none", linewidth=0, height=1.0)
    ax.barh(y, unserved_o, left=realized_o + ltfu_o, color=RED,
             label="Foregone — unserved intake",
             edgecolor="none", linewidth=0, height=1.0)

    ax.set_yticks([])
    ax.set_xlabel("Total revenue over post-warmup years (USD)",
                   fontsize=10, labelpad=8)
    _format_usd(ax, axis="x")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.grid(axis="x", alpha=0.2)
    ax.invert_yaxis()
    _external_legend(ax, loc="upper right")

    total_m = float(total.mean())
    realized_m = float(realized.mean())
    ltfu_m = float(ltfu_foregone.mean())
    unserved_m = float(unserved_foregone.mean())
    share = lambda v: 100.0 * v / max(total_m, 1.0)
    n_years_post = int(_n_post_warmup_years(df) - 1)
    n_seeds_val = len(order)
    description = (
        f"Each row is one of {n_seeds_val} simulation runs. The total bar length is that run's total addressable "
        f"revenue over the {n_years_post} post-warmup years (scaled up to the full NYP population). "
        f"The three segments split the total into what actually turned into revenue vs what was lost. "
        f"Rows are sorted so the best-performing runs (highest realized share) are at the bottom. "
        f"Average across the {n_seeds_val} runs: total addressable {_fmt_value(total_m, 'usd')}, realized "
        f"{_fmt_value(realized_m, 'usd')} ({share(realized_m):.1f}%), foregone to LTFU "
        f"{_fmt_value(ltfu_m, 'usd')} ({share(ltfu_m):.1f}%), foregone to unserved intake "
        f"{_fmt_value(unserved_m, 'usd')} ({share(unserved_m):.1f}%)."
    )
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Revenue Capture vs Foregone Revenue",
        subtitle="Per-simulation breakdown of addressable revenue over post-warmup years",
        description=description,
        top=0.90, bottom=0.24, left=0.05, right=0.82,
    )
    return _save(fig, viz_dir, "17_revenue_capture")


def render_population_capture(df: pd.DataFrame, viz_dir: str) -> str:
    """18_population_capture — cumulative intake-served/total over time (%)."""
    pivot = _pivot_annual_cumulative(df, "population_capture_rate_pct")
    if pivot.empty:
        return ""
    n_seeds = pivot.shape[1]
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("white")
    _spaghetti_with_mean(ax, pivot, mean_label="Average capture rate")
    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Population capture rate (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Population Capture Rate Over Time",
        subtitle="Cumulative share of eligible patients reached by the screening program",
        description=(
            f"Population capture = share of eligible patients who have been screened at least once since the "
            f"program started. It is cumulative, so once a patient has been captured they stay captured. "
            f"Higher is better — it means the program is reaching more of the target population each year. "
            f"Each of the {n_seeds} thin lines is one simulation run's year-by-year capture rate; the bold line "
            f"is the average capture rate at each year across all runs."
        ),
        top=0.90, bottom=0.20, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "18_population_capture")


def render_foregone_revenue(df: pd.DataFrame, viz_dir: str) -> str:
    """19_foregone_revenue — distribution of per-seed mean annual foregone revenue."""
    per_seed = _per_seed_mean_of_annual_metric(df, "annual_foregone_revenue_usd")
    if per_seed.empty:
        return ""
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("white")
    mean, sd, n = _histogram_panel(
        ax, per_seed, "#E8833A",
        x_label="Average annual foregone revenue per simulation run (USD)",
        x_fmt="usd",
    )
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Annual Foregone Revenue",
        subtitle="Distribution of average annual lost revenue across simulation runs",
        description=(
            f"Foregone revenue is the opportunity cost of the program's access gaps — money NYP would have "
            f"collected if every patient who was queued for a screening / procedure had actually received it, "
            f"and if every eligible patient who was never scheduled had entered the pipeline. "
            f"For each of the {n} simulation runs, we compute the average annual foregone revenue across the 70 "
            f"post-warmup years. The histogram shows how those {n} per-run averages are distributed. "
            f"The dark vertical line is the mean across runs ({_fmt_value(mean, 'usd')}); the dashed lines are "
            f"±1 standard deviation ({_fmt_value(sd, 'usd')})."
        ),
        top=0.90, bottom=0.22, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "19_foregone_revenue")


# =============================================================================
# Cascade / funnel vizzes (end-of-sim totals with error bars)
# =============================================================================

def _cascade_bar(
    viz_dir: str,
    filename: str,
    title: str,
    color_by_label: List[Tuple[str, str, float]],  # (label, color, mean) per row
    sd_by_label: List[float],
    pct_references: List[Optional[Tuple[int, str]]],
    section_markers: List[Tuple[float, str, str]],
    seeds,
    title_color: str,
    per_seed_by_label: Optional[List[pd.Series]] = None,
) -> str:
    """
    Cascade rendered as a horizontal BOX PLOT per stage.
      • Box = IQR, whiskers = 5-95%, every seed shown as a hollow dot.
      • White diamond = mean (still conveys the funnel shape via its x-pos).
      • Per-stage label to the right shows "mean (N% of previous stage)".
    """
    vals = [m for _, _, m in color_by_label]
    labels = [l for l, _, _ in color_by_label]
    colors = [c for _, c, _ in color_by_label]

    fig, ax = plt.subplots(figsize=(12, 0.75 * len(labels) + 3.5))
    fig.patch.set_facecolor("white")

    if per_seed_by_label is None:
        # Degenerate fallback: no per-seed data → plain bars (should not happen)
        ax.barh(range(1, len(labels) + 1), vals, color=colors, alpha=0.7)
        means = vals
    else:
        means = _box_with_strip(ax, per_seed_by_label, colors,
                                orientation="horizontal",
                                box_width=0.55, strip_size=10.0,
                                strip_alpha=0.45, strip_jitter=0.22)

    # Annotate mean + "(X% of previous stage)" outside the box
    max_val = max(means) if means else 1.0
    for i, m in enumerate(means):
        ref = pct_references[i]
        if ref is not None:
            denom = means[ref[0]]
            pct = 100.0 * m / max(denom, 1)
            tail = f"  ({pct:.0f}% of {ref[1]})"
        else:
            tail = ""
        ax.text(max_val * 1.02, i + 1,
                 f"mean {m:,.0f}{tail}",
                 va="center", ha="left", fontsize=9, fontweight="bold")

    # Optional section dividers (not visible labels — just horizontal dashes)
    for y_pos, _, _ in section_markers:
        ax.axhline(y=y_pos + 0.5, color="#888", ls="--", lw=0.8, alpha=0.4)

    ax.set_yticks(range(1, len(labels) + 1))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=12, fontweight="bold", color=title_color)
    ax.set_xlabel("End-of-sim total patients (count)", fontsize=10)
    ax.set_xlim(right=max_val * 1.35)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: f"{v/1e3:.0f}K" if v >= 1000 else f"{v:.0f}"
    ))
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _footer(plt.gcf(), seeds,
            "AGGREGATION: per-stage value = per-seed END-OF-SIM cumulative "
            "total. Box = IQR, whiskers = 5–95%, every dot = ONE seed, "
            "◆ = mean.")
    plt.tight_layout()
    return _save(fig, viz_dir, filename)


def _final_mean_sd(df: pd.DataFrame, metric: str) -> Tuple[float, float, int]:
    vals = _final_values(df, metric)
    if vals.empty:
        return 0.0, 0.0, 0
    m = float(vals.mean())
    s = float(vals.std(ddof=1)) if vals.size > 1 else 0.0
    return m, s, int(vals.size)


def render_cervical_cascade(df: pd.DataFrame, viz_dir: str) -> str:
    """
    12_cervical_cascade — small-multiple HISTOGRAMS, one per cascade stage.
    Per-seed value = MEAN ANNUAL count at each stage, derived from the
    year-over-year Δ of that stage's cp.cum_* metric (so warmup years are
    excluded by construction).
    """

    def _annual_from_sum_of_cums(cum_metrics: List[str]) -> pd.Series:
        """For a stage aggregated from several cp.cum_* series, sum the
        per-seed annual deltas across all components."""
        agg: Optional[pd.Series] = None
        for m in cum_metrics:
            s = _per_seed_annual_mean(df, m)
            if s.empty:
                continue
            agg = s if agg is None else agg.add(s, fill_value=0)
        return agg if agg is not None else pd.Series(dtype=float)

    seen_seed   = _per_seed_annual_mean(df, "cp.cum_n_patients")
    cerv_seed   = _annual_from_sum_of_cums(["cp.cum_n_screened.cervical"])
    abn_seed    = _annual_from_sum_of_cums([
        "cp.cum_cervical_results.ASCUS",
        "cp.cum_cervical_results.LSIL",
        "cp.cum_cervical_results.ASC-H",
        "cp.cum_cervical_results.HSIL",
        "cp.cum_cervical_results.HPV_POSITIVE",
    ])
    colpo_seed  = _per_seed_annual_mean(df, "cp.cum_colposcopy")
    cin1_seed   = _annual_from_sum_of_cums([
        "cp.cum_colposcopy_results.NORMAL",
        "cp.cum_colposcopy_results.CIN1",
    ])
    cin2_seed   = _annual_from_sum_of_cums([
        "cp.cum_colposcopy_results.CIN2",
        "cp.cum_colposcopy_results.CIN3",
    ])
    treated_seed = _annual_from_sum_of_cums([
        "cp.cum_n_treatment.leep",
        "cp.cum_n_treatment.cone_biopsy",
    ])

    stages = [
        ("1. Seen by provider",          "#4472C4", seen_seed,   None),
        ("2. Screened (Cytology / hrHPV)", "#6A9FD9", cerv_seed,   ("seen",         seen_seed)),
        ("3. Abnormal result",           "#D46A2E", abn_seed,    ("screened",     cerv_seed)),
        ("4. Colposcopy completed",      "#E8833A", colpo_seed,  ("abnormal",     abn_seed)),
        ("5. CIN1 / Normal (surveillance)", "#F0A868", cin1_seed,  ("colposcopies", colpo_seed)),
        ("6. CIN2/3 diagnosed",          "#C0392B", cin2_seed,   ("colposcopies", colpo_seed)),
        ("7. Treated (LEEP / cone)",     "#27AE60", treated_seed,("CIN2/3",       cin2_seed)),
    ]
    panels: List[Tuple[str, str, pd.Series]] = []
    for label, color, series, ref in stages:
        if series.empty:
            continue
        m = float(series.mean())
        if ref is None:
            ylabel = f"{label}"
        else:
            denom_label, denom_series = ref
            denom = float(denom_series.mean()) if not denom_series.empty else 1.0
            pct = 100.0 * m / max(denom, 1)
            ylabel = f"{label}  ({pct:.0f}% of {denom_label})"
        panels.append((ylabel, color, series))

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="12_cervical_cascade",
        xscale="log",
        x_label="Average annual count per simulation run (log scale)",
        title="Cervical Cancer Screening Cascade",
        subtitle="Patient flow from pool through screening, colposcopy, and treatment",
        description=(
            f"The cervical pathway flows through seven stages in order: a patient is seen by a provider, "
            f"gets primary screened (cytology or HPV-alone), receives the result, and — if abnormal — is "
            f"referred to colposcopy for a direct visual exam of the cervix. Colposcopy grades the lesion as "
            f"NORMAL, CIN1 (mild dysplasia), CIN2 or CIN3 (moderate / severe dysplasia), or INSUFFICIENT. "
            f"CIN1 and normal results return to routine surveillance; CIN2 / CIN3 are referred to treatment "
            f"(LEEP excision or cone biopsy). "
            f"Each bar is one of {n_seeds} simulation runs. The X-axis is log-scaled because the upstream "
            f"stages are up to 5 orders of magnitude larger than the downstream stages."
        ),
    )


def render_cervical_followup(df: pd.DataFrame, viz_dir: str) -> str:
    """
    13_cervical_followup — small-multiple HISTOGRAMS, one per colposcopy
    result category. Per-seed value = MEAN ANNUAL count (mean of annual Δ
    from cp.cum_colposcopy_results.<cat>, warmup years excluded).
    """
    keys = [("NORMAL", "#95A5A6"), ("CIN1", "#3498DB"),
            ("CIN2", "#F39C12"), ("CIN3", "#E74C3C"),
            ("INSUFFICIENT", "#888")]
    per_seed: Dict[str, pd.Series] = {}
    for name, _ in keys:
        v = _per_seed_annual_mean(df, f"cp.cum_colposcopy_results.{name}")
        if not v.empty:
            per_seed[name] = v

    total_mean = sum(v.mean() for v in per_seed.values()) or 1
    panels: List[Tuple[str, str, pd.Series]] = []
    for name, col in keys:
        if name not in per_seed:
            continue
        v = per_seed[name]
        pct = 100.0 * v.mean() / total_mean
        panels.append((f"{name}  ({pct:.1f}% of total)", col, v))

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="13_cervical_followup",
        x_label="Average annual colposcopies per simulation run",
        title="Colposcopy Result Distribution",
        subtitle="Annual colposcopy outcomes by histology category",
        description=(
            f"After a colposcopy, the gynecologist classifies the cervical tissue into one of five categories: "
            f"NORMAL (no lesion), CIN1 (mild dysplasia, usually returns to routine screening), CIN2 and CIN3 "
            f"(moderate and severe dysplasia — both trigger treatment with LEEP excision or cone biopsy), and "
            f"INSUFFICIENT (the sample could not be graded and the patient is re-screened). "
            f"Each bar is one of {n_seeds} simulation runs; its length is that run's average annual count in "
            f"that result category over the 70 post-warmup years."
        ),
    )


def render_lung_cascade(df: pd.DataFrame, viz_dir: str) -> str:
    """
    14_lung_cascade — small-multiple HISTOGRAMS, one per cascade stage.
    Per-seed value = MEAN ANNUAL count at each stage (mean of annual Δ in
    the stage's cp.cum_* metric, warmup excluded).
    """
    stages = [
        ("1. Eligible (USPSTF)",     "cp.cum_lung_eligible",            "#4472C4"),
        ("2. LDCT order placed",     "cp.cum_lung_referral_placed",     "#6A9FD9"),
        ("3. LDCT scheduled",        "cp.cum_lung_ldct_scheduled",      "#8BB8E3"),
        ("4. LDCT completed",        "cp.cum_lung_ldct_completed",      "#D46A2E"),
        ("5. Biopsy referral",       "cp.cum_lung_biopsy_referral",     "#E8833A"),
        ("6. Biopsy completed",      "cp.cum_lung_biopsy_completed",    "#F0A868"),
        ("7. Malignancy confirmed",  "cp.cum_lung_malignancy_confirmed","#C0392B"),
        ("8. Treatment given",       "cp.cum_lung_treatment_given",     "#27AE60"),
    ]
    panels: List[Tuple[str, str, pd.Series]] = []
    prev_mean: Optional[float] = None
    prev_short: Optional[str] = None
    for label, metric, color in stages:
        s = _per_seed_annual_mean(df, metric)
        if s.empty:
            continue
        m = float(s.mean())
        short = label.split(".", 1)[1].strip().split("(")[0].strip()
        if prev_mean is None:
            ylabel = f"{label}"
        else:
            pct = 100.0 * m / max(prev_mean, 1)
            ylabel = f"{label}  ({pct:.0f}% of '{prev_short}')"
        panels.append((ylabel, color, s))
        prev_mean = m
        prev_short = short

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="14_lung_cascade",
        xscale="log",
        x_label="Average annual count per simulation run (log scale)",
        title="Lung LDCT Screening Cascade",
        subtitle="Patient flow from eligibility through LDCT, biopsy, and treatment",
        description=(
            f"The lung pathway flows through eight stages. USPSTF-eligible patients (age 50–80, 20+ pack-year "
            f"smoking history, still smoking or quit within 15 years) are offered LDCT. Of those eligible, some "
            f"get an LDCT order placed, some get the LDCT scheduled, and fewer still complete it. "
            f"If the LDCT finding is suspicious (Lung-RADS 4), a biopsy is ordered and then performed; if the "
            f"biopsy confirms malignancy, the patient proceeds to treatment (surgery, radiation, or systemic "
            f"therapy). "
            f"Each bar is one of {n_seeds} simulation runs. The X-axis is log-scaled because upstream stages "
            f"(eligible, ordered) are orders of magnitude larger than downstream stages (malignancy, treatment)."
        ),
    )


def render_lung_followup(df: pd.DataFrame, viz_dir: str) -> str:
    """
    15_lung_followup — small-multiple HISTOGRAMS, one per Lung-RADS category.
    Per-seed value = MEAN ANNUAL count (mean of annual Δ in
    cp.cum_lung_rads_distribution.<cat>, warmup excluded).
    """
    categories = [
        ("RADS_0",      "#BDC3C7"),
        ("RADS_1",      "#3498DB"),
        ("RADS_2",      "#2ECC71"),
        ("RADS_3",      "#F39C12"),
        ("RADS_4A",     "#E67E22"),
        ("RADS_4B_4X",  "#E74C3C"),
    ]
    per_seed: Dict[str, pd.Series] = {}
    for cat, _ in categories:
        v = _per_seed_annual_mean(df, f"cp.cum_lung_rads_distribution.{cat}")
        if not v.empty:
            per_seed[cat] = v

    total_mean = sum(v.mean() for v in per_seed.values()) or 1
    panels: List[Tuple[str, str, pd.Series]] = []
    for cat, col in categories:
        if cat not in per_seed:
            continue
        v = per_seed[cat]
        pct = 100.0 * v.mean() / total_mean
        panels.append((f"{cat}  ({pct:.1f}% of total)", col, v))

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="15_lung_followup",
        xscale="log",
        x_label="Average annual LDCT count per simulation run (log scale)",
        title="Lung-RADS Result Distribution",
        subtitle="Annual LDCT outcomes classified by Lung-RADS category",
        description=(
            f"Lung-RADS (Lung CT Screening Reporting & Data System) is the American College of Radiology's "
            f"5-point classification for LDCT findings. RADS 0 means the exam is incomplete or needs "
            f"comparison. RADS 1 (no nodules) and RADS 2 (benign appearance) require no follow-up beyond the "
            f"annual screen. RADS 3 (probably benign) triggers short-interval follow-up at 6 months. "
            f"RADS 4A (suspicious, moderate) and 4B / 4X (suspicious, high probability) trigger biopsy referral. "
            f"Each bar is one of {n_seeds} simulation runs; its length is that run's average annual LDCT count "
            f"landing in that category. Log scale is used because RADS 1 / 2 outnumber RADS 4 by ~100×."
        ),
    )


# =============================================================================
# 09 — Capacity by Stage (STATIC CONFIG, not MC-dependent)
# =============================================================================

def render_capacity_by_stage(df: pd.DataFrame, viz_dir: str) -> str:
    """
    09_capacity_by_stage — daily slot capacity per modality, pulled directly
    from parameters.CAPACITIES. This does NOT vary across seeds (it's a
    scheduling configuration, not a stochastic outcome), so there's nothing
    to Monte-Carlo. We still render it so the visualization set is complete
    and the reader can cross-reference capacity vs. demand (see 14, 15).
    """
    import parameters as cfg
    caps = dict(cfg.CAPACITIES)
    # Group by phase
    groups = [
        ("Primary screening", ["cytology", "hpv_alone", "co_test", "ldct"],
         "#4472C4"),
        ("Secondary / diagnostic", ["colposcopy", "lung_biopsy"],
         "#D46A2E"),
        ("Treatment", ["leep", "cone_biopsy"], "#27AE60"),
    ]
    labels, values, colors = [], [], []
    for phase, keys, col in groups:
        for k in keys:
            if k in caps:
                labels.append(f"{k}")
                values.append(int(caps[k]))
                colors.append(col)

    fig, ax = plt.subplots(figsize=(11, 0.5 * len(labels) + 3.0))
    fig.patch.set_facecolor("white")
    ax.barh(range(len(labels)), values, color=colors, alpha=0.8,
             edgecolor="white", height=0.65)
    for i, v in enumerate(values):
        ax.text(v + max(values) * 0.01, i,
                 f"{v} slots/day", va="center", ha="left",
                 fontsize=9, fontweight="bold")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Daily slots (simulation-scale)", fontsize=10)
    ax.set_title(
        "Capacity by Stage — STATIC CONFIG from parameters.CAPACITIES "
        "(does NOT vary across seeds)",
        fontsize=12, fontweight="bold",
    )
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _footer(fig, df["seed"].unique(),
            "STATIC: capacity is a fixed scheduling parameter, identical across "
            "all MC seeds. See 14 / 15 for demand-vs-capacity time series.")
    plt.tight_layout()
    return _save(fig, viz_dir, "09_capacity_by_stage")


# =============================================================================
# 10 / 11 — Wait times by modality (MC box plots)
# =============================================================================

def render_wait_times(df: pd.DataFrame, viz_dir: str) -> str:
    """
    05_wait_times — ALL modalities on one chart with a SHARED X-axis so
    per-modality distributions can be compared directly. Ridge-bar layout:
    one horizontal bar per seed, grouped by modality, with modality means
    marked as white diamonds on the right. Cervical and lung pathways are
    merged into a single graph (previously split 10 / 11).
    """
    modalities = [
        # label                       color        pathway
        ("cytology",     "#8E44AD"),   # cervical primary
        ("hpv_alone",    "#16A085"),   # cervical primary
        ("co_test",      "#1F78B4"),   # cervical primary
        ("colposcopy",   "#C0392B"),   # cervical secondary
        ("ldct",         "#2980B9"),   # lung primary
        ("lung_biopsy",  "#E67E22"),   # lung secondary
    ]
    panels: List[Tuple[str, str, pd.Series]] = []
    for modality, col in modalities:
        s = _final_values(df, f"final.wait_mean.{modality}")
        if s.empty:
            continue
        panels.append((f"{modality}  (mean {s.mean():.3f}d)", col, s))

    if not panels:
        return ""

    n_seeds = df["seed"].nunique()
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="05_wait_times",
        x_label="Average wait per simulation run (days)",
        title="Wait Times by Screening Modality",
        subtitle="Average days between referral and appointment, per modality",
        description=(
            f"A 'wait' is the number of days between when a patient is placed in the scheduling queue for a "
            f"screening or procedure and when the appointment actually happens. Shorter waits mean better access. "
            f"Modalities shown here: cytology, HPV-alone, and co-test are the three cervical primary screens "
            f"(the first test in the cervical pathway); colposcopy is the cervical follow-up procedure that "
            f"happens after an abnormal primary screen. LDCT (low-dose CT) is the lung primary screen; lung "
            f"biopsy is the lung follow-up procedure. Each bar is one of {n_seeds} simulation runs; its length "
            f"is that run's mean wait across all events of that modality over the full 80 years."
        ),
    )


# =============================================================================
# 14 / 15 — Annual demand vs capacity (MC spaghetti + capacity line)
# =============================================================================

def _annual_capacity(keys: List[str]) -> float:
    """Annual capacity = daily slots summed across modalities × workdays/year.
    Workdays = days where day-of-week NOT in (5, 6) = ~5/7 × 365 ≈ 260 days."""
    import parameters as cfg
    daily = sum(int(cfg.CAPACITIES.get(k, 0)) for k in keys)
    workdays_per_year = int(round(365 * 5 / 7))
    return float(daily * workdays_per_year)


def _render_demand_vs_capacity(
    df: pd.DataFrame, viz_dir: str,
    demand_metrics: List[Tuple[str, str, str]],
    capacity_keys: List[str],
    filename: str, title: str,
):
    """
    Build the demand-vs-capacity spaghetti axes and return the figure plus
    context values for the caller to finalize (title, description, save).
    Returns tuple (fig, ax, cap, daily_slots, workdays, cap_factor, n_seeds)
    or empty string if no data.
    """
    # Combine per-seed annual demand across modalities → single (year × seed) pivot
    combined = None
    for metric, _, _ in demand_metrics:
        delta = _pivot_annual_delta(df, metric)
        if delta.empty:
            continue
        combined = delta if combined is None else combined.add(delta, fill_value=0)
    if combined is None or combined.empty:
        return ""

    n_seeds = combined.shape[1]
    fig, ax = plt.subplots(figsize=(22, 7))
    fig.patch.set_facecolor("white")

    _spaghetti_with_mean(
        ax, combined, color="#2C7BB6", mean_color="#0B3A74",
        mean_label="Average annual demand",
    )

    cap = _annual_capacity(capacity_keys)
    dem_vals = combined.values.flatten()
    dem_vals = dem_vals[~np.isnan(dem_vals)]
    hi = 1.0
    if dem_vals.size:
        lo = float(np.nanmin(dem_vals))
        hi = float(np.nanmax(dem_vals))
        pad = (hi - lo) * 0.15 or 1.0
        ax.set_ylim(max(0, lo - pad), hi + pad)

    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("Screenings per year", fontsize=10)
    _format_thousands(ax)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")

    daily_slots = sum(__import__('parameters').CAPACITIES.get(k, 0)
                      for k in capacity_keys)
    workdays = int(round(365 * 5 / 7))
    cap_factor = cap / max(hi, 1)
    return fig, ax, cap, daily_slots, workdays, cap_factor, n_seeds


def render_primary_demand_vs_capacity(df: pd.DataFrame, viz_dir: str) -> str:
    """06_primary_demand_vs_capacity — annual primary-screening demand vs capacity."""
    result = _render_demand_vs_capacity(
        df, viz_dir,
        demand_metrics=[
            ("cp.cum_cytology",  "cytology",  "#8E44AD"),
            ("cp.cum_hpv_alone", "hpv_alone", "#16A085"),
            ("cp.cum_ldct",      "ldct",      "#2980B9"),
        ],
        capacity_keys=["cytology", "hpv_alone", "co_test", "ldct"],
        filename="06_primary_demand_vs_capacity",
        title="unused",
    )
    if not result:
        return ""
    fig, ax, cap, daily_slots, workdays, cap_factor, n_seeds = result
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Primary Screening Demand Over Time",
        subtitle="Annual primary-screening demand vs available scheduling capacity",
        description=(
            f"Primary screenings are the first test in each cancer pathway: cytology, HPV-alone, and co-test "
            f"(cervical) and LDCT (lung). Demand here = total primary screenings completed each year. "
            f"Annual capacity = {daily_slots} daily scheduling slots × {workdays} workdays per year "
            f"= {cap:,.0f} slots/year — roughly {cap_factor:.1f}× the mean observed demand in this config, "
            f"so capacity sits off the top of the chart. Each of the {n_seeds} thin lines is one simulation run's "
            f"annual demand; the bold line is the average across runs."
        ),
        top=0.90, bottom=0.18, left=0.06, right=0.88,
    )
    return _save(fig, viz_dir, "06_primary_demand_vs_capacity")


def render_secondary_demand_vs_capacity(df: pd.DataFrame, viz_dir: str) -> str:
    """
    07_secondary_demand_vs_capacity — ridge-bar plot. The per-year demand
    series for secondary procedures is essentially FLAT across the sim
    (stationary demand, no time trend), so a time-series spaghetti hides
    the actually-interesting signal: seed-to-seed variation in the overall
    annual average. We plot per-seed mean annual demand by modality as a
    horizontal ridge-bar on a shared X-axis, with static annual capacity
    shown as a labeled dashed reference line.
    """
    import parameters as cfg
    modalities = [
        ("colposcopy",  "cp.cum_colposcopy",  "#E8833A"),
        ("lung_biopsy", "cp.cum_lung_biopsy", "#E67E22"),
    ]
    panels: List[Tuple[str, str, pd.Series]] = []
    cap_by_modality: Dict[str, float] = {}
    workdays_per_year = int(round(365 * 5 / 7))
    for name, metric, color in modalities:
        s = _per_seed_annual_mean(df, metric)
        if s.empty:
            continue
        cap = float(int(cfg.CAPACITIES.get(name, 0)) * workdays_per_year)
        cap_by_modality[name] = cap
        panels.append((f"{name}  (capacity {cap:,.0f}/yr)", color, s))

    n_seeds = df["seed"].nunique()
    cap_parts = ", ".join(f"{k} {v:,.0f}/yr" for k, v in cap_by_modality.items())
    return _grouped_ridge_bars(
        panels=panels,
        seeds=df["seed"].unique(),
        viz_dir=viz_dir,
        filename="07_secondary_demand_vs_capacity",
        x_label="Average annual demand per simulation run",
        title="Secondary Procedure Demand by Modality",
        subtitle="Annual colposcopy and lung biopsy demand across simulation runs",
        description=(
            f"Secondary procedures follow an abnormal primary-screen result. Colposcopy is scheduled after an "
            f"abnormal cervical screen (cytology or HPV); lung biopsy is scheduled after a suspicious LDCT finding. "
            f"Each bar is one of {n_seeds} simulation runs; its length is that run's average annual demand for the "
            f"procedure (computed as yearly change in the cumulative count, averaged over the 70 post-warmup years). "
            f"Scheduling capacity is {cap_parts} — roughly 10–25× mean demand, so capacity is not the binding "
            f"constraint here and is not drawn on the axis. The signal to read is the seed-to-seed variation in "
            f"annual demand, which is what drives wait-time differences downstream."
        ),
    )


# =============================================================================
# 29 / 30 — LTFU rate time series (MC)
# =============================================================================

def _render_ltfu_rate(df: pd.DataFrame, viz_dir: str, metric: str,
                      *, title: str, subtitle: str, description: str,
                      filename: str, color: str, mean_color: str) -> str:
    pivot = _pivot_annual_cumulative(df, metric)
    if pivot.empty:
        return ""
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("white")
    _spaghetti_with_mean(ax, pivot, color=color, mean_color=mean_color,
                         mean_label="Average LTFU rate")
    ax.set_xlabel("Simulation year", fontsize=10)
    ax.set_ylabel("LTFU rate (%)", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.2f}%"))
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title=title, subtitle=subtitle, description=description,
        top=0.90, bottom=0.22, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, filename)


def render_ltfu_rate_primary(df: pd.DataFrame, viz_dir: str) -> str:
    """20_ltfu_rate_primary — cumulative primary-queue LTFU rate over time (MC)."""
    n_seeds = df["seed"].nunique()
    return _render_ltfu_rate(
        df, viz_dir, "ltfu_rate_primary_pct",
        title="Loss-to-Follow-Up Rate — Primary Queue",
        subtitle="Share of primary-screening referrals that never complete the screening",
        description=(
            f"Loss-to-Follow-Up (LTFU) happens when a patient has been queued for a primary screening "
            f"(cytology, HPV-alone, co-test, or LDCT) but does not show up before the scheduling window closes. "
            f"The rate shown is cumulative — at year X it is the share of ALL primary-queue referrals ever "
            f"placed through year X that ended in LTFU. "
            f"Each of the {n_seeds} thin lines is one simulation run; the bold line is the average LTFU rate "
            f"at each year across all runs."
        ),
        filename="20_ltfu_rate_primary",
        color="#E8833A", mean_color="#A14200",
    )


def render_ltfu_rate_secondary(df: pd.DataFrame, viz_dir: str) -> str:
    """21_ltfu_rate_secondary — cumulative secondary-queue LTFU rate over time (MC)."""
    n_seeds = df["seed"].nunique()
    return _render_ltfu_rate(
        df, viz_dir, "ltfu_rate_secondary_pct",
        title="Loss-to-Follow-Up Rate — Secondary Queue",
        subtitle="Share of secondary-procedure referrals that never complete the procedure",
        description=(
            f"After an abnormal primary screen, a patient is queued for a secondary procedure (colposcopy after "
            f"an abnormal Pap or HPV; lung biopsy after a suspicious LDCT). LTFU in the secondary queue means "
            f"the patient never arrived for the follow-up. Because the secondary step is where confirmed "
            f"diagnoses and treatment decisions happen, LTFU here often translates into missed cancer diagnoses. "
            f"The rate is cumulative — at year X it is the share of ALL secondary-queue referrals ever placed "
            f"through year X that ended in LTFU. "
            f"Each of the {n_seeds} thin lines is one simulation run; the bold line is the average LTFU rate "
            f"at each year across all runs."
        ),
        filename="21_ltfu_rate_secondary",
        color="#C0392B", mean_color="#7B1F13",
    )


# =============================================================================
# 31 / 32 — Mean wait time distributions across seeds (MC)
# =============================================================================

def render_mean_wait_primary(df: pd.DataFrame, viz_dir: str) -> str:
    """22_mean_wait_primary — distribution of per-seed mean wait time in the
    primary screening queue (cytology / HPV / co-test / LDCT)."""
    per_seed = _final_values(df, "mean_wait_primary_days")
    if per_seed.empty:
        return ""
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("white")
    mean, sd, n = _histogram_panel(
        ax, per_seed, "#2C7BB6",
        x_label="Average wait per simulation run (days)",
        x_fmt="days",
    )
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Mean Wait Time — Primary Screening",
        subtitle="Distribution of average wait (days) across simulation runs",
        description=(
            f"A 'wait' is the number of days between when a patient is placed in the scheduling queue and "
            f"when the appointment actually takes place. The primary screening queue feeds cytology, HPV-alone, "
            f"co-test (cervical), and LDCT (lung). "
            f"For each of the {n} simulation runs, we compute one number: the mean wait across every primary "
            f"screening event in that run's full 80-year simulation. The histogram shows how those {n} per-run "
            f"means are distributed. "
            f"The dark vertical line is the mean across runs ({mean:.3f} days); the dashed lines are ±1 "
            f"standard deviation ({sd:.3f} days)."
        ),
        top=0.90, bottom=0.22, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "22_mean_wait_primary")


def render_mean_wait_secondary(df: pd.DataFrame, viz_dir: str) -> str:
    """23_mean_wait_secondary — distribution of per-seed mean wait time in the
    secondary queue (colposcopy + lung biopsy)."""
    per_seed = _final_values(df, "mean_wait_secondary_days")
    if per_seed.empty:
        return ""
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor("white")
    mean, sd, n = _histogram_panel(
        ax, per_seed, "#8E44AD",
        x_label="Average wait per simulation run (days)",
        x_fmt="days",
    )
    _external_legend(ax, loc="upper right")
    _finalize_figure(
        fig, df["seed"].unique(),
        title="Mean Wait Time — Secondary Procedure",
        subtitle="Distribution of average wait (days) across simulation runs",
        description=(
            f"After an abnormal primary screen, a patient is referred to a secondary procedure — colposcopy "
            f"(cervical) or lung biopsy (lung) — and waits in the secondary scheduling queue. A 'wait' here is "
            f"the number of days between being placed in that queue and the actual procedure happening. "
            f"For each of the {n} simulation runs, we compute one number: the mean wait across every secondary "
            f"event in that run's full 80-year simulation. The histogram shows how those {n} per-run means are "
            f"distributed. "
            f"The dark vertical line is the mean across runs ({mean:.3f} days); the dashed lines are ±1 "
            f"standard deviation ({sd:.3f} days)."
        ),
        top=0.90, bottom=0.22, left=0.07, right=0.86,
    )
    return _save(fig, viz_dir, "23_mean_wait_secondary")


# =============================================================================
# 00 — Statistical Inference Table (MC across seeds)
# =============================================================================

def _per_seed_annual_mean_from_delta(df: pd.DataFrame, cum_metric: str) -> pd.Series:
    """For a cumulative metric: one number per seed = mean annual Δ across
    post-warmup years."""
    delta = _pivot_annual_delta(df, cum_metric)
    if delta.empty:
        return pd.Series(dtype=float)
    return delta.mean(axis=0)


def _per_seed_year_mean(df: pd.DataFrame, metric: str) -> pd.Series:
    """For an already-annual metric: one number per seed = mean across years."""
    sub = df[df["metric"] == metric].copy()
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
    sub = sub.dropna(subset=["year", "value"])
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.groupby("seed")["value"].mean()


def _per_seed_final(df: pd.DataFrame, metric: str) -> pd.Series:
    """One number per seed from a `final.*` (end-of-sim) metric."""
    return _final_values(df, metric)


def _stats_row(label: str, s: pd.Series, fmt: str = ",.0f",
               is_pct: bool = False) -> List[str]:
    """Return [label, mean, median, sd, min, max, cv%] formatted strings."""
    arr = pd.to_numeric(s, errors="coerce").dropna().values
    if arr.size == 0:
        return [label, "—", "—", "—", "—", "—", "—"]
    mu  = float(np.mean(arr))
    med = float(np.median(arr))
    sd  = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0
    lo  = float(np.min(arr))
    hi  = float(np.max(arr))
    cv  = (sd / mu * 100.0) if mu != 0 else 0.0
    suf = "%" if is_pct else ""
    return [
        label,
        f"{mu:{fmt}}{suf}",
        f"{med:{fmt}}{suf}",
        f"{sd:{fmt}}{suf}",
        f"{lo:{fmt}}{suf}",
        f"{hi:{fmt}}{suf}",
        f"{cv:.1f}%",
    ]


def render_statistical_inference_table(df: pd.DataFrame, viz_dir: str) -> str:
    """
    00_statistical_inference_table — MC-aware stats table.

    For each variable, each seed contributes ONE value (its post-warmup annual
    mean, or its end-of-sim aggregate). We then summarize across seeds: mean,
    median, SD, min, max, CV. This is a proper Monte Carlo inference table:
    variation shown is SEED-TO-SEED (simulation replication noise), not
    year-to-year within a single run.
    """
    seeds = df["seed"].unique()
    n_seeds = int(df["seed"].nunique())

    sections: List[Tuple[str, List[List[str]]]] = []

    rows_pop = [
        _stats_row("Pool size (active patients, per-year average)",
                   _per_seed_year_mean(df, "cp.pool_size")),
        _stats_row("Annual deaths",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_mortality")),
        _stats_row("Annual patients seen",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_n_patients")),
        _stats_row("Annual arrivals into the pool",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_arrivals")),
        _stats_row("Annual exits from the pool",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_exited")),
    ]
    sections.append(("1. POPULATION & POOL", rows_pop))

    rows_cerv = [
        _stats_row("Annual cervical screenings (total)",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_cervical")),
        _stats_row("  Cytology tests per year",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_cytology")),
        _stats_row("  HPV-alone tests per year",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_hpv_alone")),
    ]
    sections.append(("2. PRIMARY SCREENING — CERVICAL", rows_cerv))

    rows_lung = [
        _stats_row("Annual LDCT screenings",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_ldct")),
    ]
    sections.append(("3. PRIMARY SCREENING — LUNG", rows_lung))

    rows_sec = [
        _stats_row("Annual colposcopies",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_colposcopy")),
        _stats_row("Annual lung biopsies",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_lung_biopsy")),
    ]
    sections.append(("4. SECONDARY SCREENING", rows_sec))

    rows_tx = [
        _stats_row("Annual LEEP procedures",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_leep")),
        _stats_row("Annual lung treatments",
                   _per_seed_annual_mean_from_delta(df, "cp.cum_lung_treatment")),
    ]
    sections.append(("5. TREATMENT", rows_tx))

    rows_ltfu = [
        _stats_row("LTFU rate — primary queue",
                   _per_seed_year_mean(df, "ltfu_rate_primary_pct"),
                   fmt=".2f", is_pct=True),
        _stats_row("LTFU rate — secondary queue",
                   _per_seed_year_mean(df, "ltfu_rate_secondary_pct"),
                   fmt=".2f", is_pct=True),
    ]
    sections.append(("6. LOSS TO FOLLOW-UP", rows_ltfu))

    rows_wait = [
        _stats_row("Mean wait — primary screening (days)",
                   _per_seed_final(df, "mean_wait_primary_days"),
                   fmt=".3f"),
        _stats_row("Mean wait — secondary procedure (days)",
                   _per_seed_final(df, "mean_wait_secondary_days"),
                   fmt=".3f"),
    ]
    sections.append(("7. WAIT TIMES", rows_wait))

    rows_fin = [
        _stats_row("Annual realized revenue (USD)",
                   _per_seed_year_mean(df, "annual_realized_revenue_usd"),
                   fmt=",.0f"),
        _stats_row("Annual foregone revenue (USD)",
                   _per_seed_year_mean(df, "annual_foregone_revenue_usd"),
                   fmt=",.0f"),
        _stats_row("Revenue capture rate",
                   _per_seed_year_mean(df, "revenue_capture_rate_pct"),
                   fmt=".2f", is_pct=True),
        _stats_row("Population capture rate",
                   _per_seed_year_mean(df, "population_capture_rate_pct"),
                   fmt=".2f", is_pct=True),
    ]
    sections.append(("8. FINANCIAL", rows_fin))

    total_rows = sum(len(r) for _, r in sections) + len(sections)
    fig_h = max(8.5, 0.34 * total_rows + 4.5)
    fig, ax = plt.subplots(figsize=(16, fig_h))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    header = ["Variable", "Mean", "Median", "Std Dev", "Min", "Max", "CV"]
    cell_text: List[List[str]] = []
    row_colors: List[List[str]] = []
    header_bg = "#1F4E79"
    header_fg = "white"
    section_bg = "#D9E2F3"
    stripe_a = "#FFFFFF"
    stripe_b = "#F5F7FA"

    for sec_title, rows in sections:
        cell_text.append([sec_title, "", "", "", "", "", ""])
        row_colors.append([section_bg] * len(header))
        for i, row in enumerate(rows):
            cell_text.append(row)
            row_colors.append([stripe_a if i % 2 == 0 else stripe_b] * len(header))

    table = ax.table(
        cellText=cell_text,
        colLabels=header,
        cellColours=row_colors,
        colColours=[header_bg] * len(header),
        loc="center",
        cellLoc="left",
        colWidths=[0.46, 0.10, 0.10, 0.10, 0.09, 0.09, 0.06],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    table.scale(1, 1.45)

    for c in range(len(header)):
        cell = table[0, c]
        cell.get_text().set_color(header_fg)
        cell.get_text().set_fontweight("bold")
        cell.set_edgecolor("white")
    r_idx = 1
    for sec_title, rows in sections:
        sec_cell = table[r_idx, 0]
        sec_cell.get_text().set_fontweight("bold")
        sec_cell.get_text().set_color("#1F4E79")
        r_idx += 1 + len(rows)

    _finalize_figure(
        fig, seeds,
        title="Monte Carlo Statistical Inference Summary",
        subtitle="Distribution of key outcomes across simulation replications",
        description=(
            f"Each row summarizes one outcome across {n_seeds} independent simulation runs. "
            f"For each run we compute a single value (the row describes what that value is — for example, "
            f"an average annual count, an average wait, or a cumulative rate); the stat columns then summarize "
            f"the distribution of those per-run values. "
            f"All variation shown is seed-to-seed: it measures how much the outcome changes between replications "
            f"of the simulation, not within a single run. CV (coefficient of variation) is Std Dev ÷ Mean × 100% "
            f"— a unitless measure of stability. Low CV means the outcome is robust to random seed choice; high "
            f"CV means more replications are needed for a trustworthy estimate."
        ),
        wrap_width=200, top=0.93, bottom=0.18, left=0.04, right=0.97,
    )
    return _save(fig, viz_dir, "00_statistical_inference_table")


# =============================================================================
# Orchestrator
# =============================================================================

_RENDERERS: List[Callable[[pd.DataFrame, str], str]] = [
    render_statistical_inference_table,
    render_pool_size,
    render_exit_breakdown,
    render_retention,
    render_mortality,
    render_wait_times,
    render_primary_demand_vs_capacity,
    render_secondary_demand_vs_capacity,
    render_annual_screening_volume,
    render_cervical_uptake,
    render_lung_uptake,
    render_first_stage_screenings,
    render_cervical_cascade,
    render_cervical_followup,
    render_lung_cascade,
    render_lung_followup,
    render_annual_revenue,
    render_revenue_capture,
    render_population_capture,
    render_foregone_revenue,
    render_ltfu_rate_primary,
    render_ltfu_rate_secondary,
    render_mean_wait_primary,
    render_mean_wait_secondary,
]


def render_all_base_mc(
    csv_path: str,
    output_dir: str,
    scenario_tag: Optional[str] = None,
) -> List[str]:
    """Render the MC-averaged base visualizations (inference table + 23 charts)
    into output_dir.

    scenario_tag, when provided, is stamped onto every chart's footer to make
    scenario runs visually distinguishable from baseline. The baseline pipeline
    omits this argument and produces byte-identical output to prior runs."""
    global _scenario_tag
    df = pd.read_csv(csv_path)
    saved: List[str] = []
    _scenario_tag = scenario_tag
    try:
        for fn in _RENDERERS:
            try:
                p = fn(df, output_dir)
                if p:
                    saved.append(p)
            except Exception as e:
                print(f"  [skip] {fn.__name__}: {e}")
    finally:
        _scenario_tag = None
    return saved
