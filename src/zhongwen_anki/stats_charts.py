"""Charts of the learning report, rendered with matplotlib as PNG data URIs."""
import base64
import io

import pandas as pd

try:
    import matplotlib
except ImportError:
    raise SystemExit('matplotlib is required for the report: pip install -e ".[analyze]"')
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from zhongwen_anki.card_types import CARD_TYPE_DESCRIPTIONS
from zhongwen_anki.stats_metrics import MIN_GROUP_REVIEWS

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514d"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"
BLUE = "#2a78d6"
LIGHT_BLUE = "#5598e7"
RED = "#c0392b"
ORANGE = "#e67e22"
GREEN = "#27ae60"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Segoe UI", "Arial", "DejaVu Sans"],
    "text.color": INK_PRIMARY,
    "axes.labelcolor": INK_SECONDARY,
    "axes.edgecolor": GRID,
    "xtick.color": INK_MUTED,
    "ytick.color": INK_MUTED,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.size": 10,
})


def _fig_to_data_uri(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _style_ax(ax, x_grid: bool = False) -> None:
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="x" if x_grid else "y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _accuracy_color(acc: float) -> str:
    if acc >= 0.9:
        return GREEN
    if acc >= 0.75:
        return BLUE
    if acc >= 0.6:
        return ORANGE
    return RED


def chart_accuracy_by_card_type(g: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 0.45 * len(g) + 1))
    labels = [
        f"{t} · {CARD_TYPE_DESCRIPTIONS.get(t, t)}" for t in g["card_type"]
    ]
    colors = [_accuracy_color(acc) for acc in g["accuracy"]]
    bars = ax.barh(labels, g["accuracy"] * 100, color=colors, height=0.55, zorder=3)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Precisión (%)")
    _style_ax(ax, x_grid=True)
    for bar, acc, n in zip(bars, g["accuracy"], g["reviews"]):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{acc * 100:.0f}% (n={n})", va="center", fontsize=9, color=INK_SECONDARY)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_daily_reviews(daily: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(8.4, 2.8))
    ax.bar(daily["review_datetime"], daily["reviews"], color=LIGHT_BLUE, width=0.85, zorder=3, alpha=0.8)
    ax.set_ylabel("Repasos / día")
    _style_ax(ax)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_accuracy_trend(daily: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(8.4, 2.8))
    ax.plot(daily["review_datetime"], daily["accuracy_7d"] * 100, color=BLUE, linewidth=2.2, zorder=3)
    ax.fill_between(daily["review_datetime"], daily["accuracy_7d"] * 100, alpha=0.1, color=BLUE)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Precisión media 7 días (%)")
    _style_ax(ax)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_retention(g: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.6, 3))
    # Use proper bucket labels with reliability markers
    labels = []
    bar_colors = []
    for _, row in g.iterrows():
        label = str(row["bucket"])
        if not row.get("reliable", True):
            label += " *"
        labels.append(label)
        bar_colors.append(_accuracy_color(row["accuracy"]))
    bars = ax.bar(labels, g["accuracy"] * 100, color=bar_colors, zorder=3)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Precisión (%)")
    ax.set_xlabel("Intervalo desde el repaso anterior")
    _style_ax(ax)
    for bar, n in zip(bars, g["reviews"]):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2, f"n={n}",
                ha="center", fontsize=8, color=INK_SECONDARY)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_weekday(g: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(6.6, 3))
    colors = [_accuracy_color(acc) for acc in g["accuracy"]]
    ax.bar(g["weekday"], g["accuracy"] * 100, color=colors, zorder=3)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Precisión (%)")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_hour(hour_df: pd.DataFrame) -> str:
    """Bar chart of reviews per hour, with accuracy (n>=5 only) on a second axis."""
    fig, ax = plt.subplots(figsize=(8.4, 2.8))
    hours = hour_df["hour"].astype(int).tolist()
    reviews = hour_df["reviews"].tolist()
    ax.bar(hours, reviews, color=LIGHT_BLUE, width=0.7, zorder=3, alpha=0.8)
    ax.set_xlabel("Hora del día")
    ax.set_ylabel("Repasos")
    ax.set_xticks(hours)

    # Accuracy as a secondary axis, only for hours with at least 5 reviews.
    valid = hour_df[hour_df["reviews"] >= MIN_GROUP_REVIEWS]
    if not valid.empty:
        ax2 = ax.twinx()
        ax2.plot(
            valid["hour"].astype(int).tolist(),
            [a * 100 for a in valid["accuracy"]],
            color=RED, marker="o", markersize=4, linewidth=1.5, zorder=4,
        )
        ax2.set_ylabel("Precisión (%)")
        ax2.set_ylim(0, 105)
        ax2.tick_params(axis="y", colors=RED)

    _style_ax(ax)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_response_time_by_type(g: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    x = range(len(g))
    width = 0.35
    ax.bar([i - width / 2 for i in x], g["avg_time_correct"], width, label="Correcto", color=GREEN, alpha=0.8, zorder=3)
    ax.bar([i + width / 2 for i in x], g["avg_time_incorrect"], width, label="Incorrecto", color=RED, alpha=0.8, zorder=3)
    ax.set_xticks(list(x))
    ax.set_xticklabels(g["card_type"], rotation=30, ha="right")
    ax.set_ylabel("Tiempo medio (segundos)")
    ax.legend(fontsize=9)
    _style_ax(ax)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_mastery_distribution(mastery_df: pd.DataFrame, mastery_streak: int = 4) -> str:
    """Pie chart of mastered vs. not mastered word/card combos."""
    fig, ax = plt.subplots(figsize=(4, 4))
    mastered = mastery_df["mastered"].sum()
    not_mastered = (~mastery_df["mastered"]).sum()
    if mastered + not_mastered == 0:
        return ""
    sizes = [mastered, not_mastered]
    labels = [f"Dominadas ({mastered})", f"No dominadas ({not_mastered})"]
    colors = [GREEN, ORANGE]
    ax.pie(sizes, labels=labels, colors=colors, autopct="%1.0f%%", startangle=90)
    ax.set_title(f"Dominio ({mastery_streak} aciertos consecutivos)")
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def chart_hard_words(hard_df: pd.DataFrame, top: int = 15, min_reviews: int = 3) -> str:
    """Horizontal bar chart of hardest words."""
    top_words = hard_df.head(top)
    fig, ax = plt.subplots(figsize=(7.2, 0.45 * len(top_words) + 1))
    labels = []
    for _, r in top_words.iterrows():
        word = r.get("word", str(r["note_id"]))
        labels.append(f"{word} ({r['card_type']})")
    colors = [_accuracy_color(acc) for acc in top_words["accuracy"]]
    bars = ax.barh(labels, top_words["accuracy"] * 100, color=colors, height=0.55, zorder=3)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Precisión (%)")
    ax.set_title(f"Palabras más difíciles (mín. {min_reviews} repasos)")
    ax.invert_yaxis()
    _style_ax(ax, x_grid=True)
    for bar, acc, agains in zip(bars, top_words["accuracy"], top_words["agains"]):
        label = f"{acc * 100:.0f}%"
        if agains > 0:
            label += f" ({int(agains)} fallos)"
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                label, va="center", fontsize=8, color=INK_SECONDARY)
    fig.tight_layout()
    return _fig_to_data_uri(fig)
