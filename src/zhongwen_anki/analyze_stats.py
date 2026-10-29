"""Turn the CSV from zhongwen-anki-export-stats into an HTML learning report.

Reads the reviews CSV (and optionally the card snapshot) and writes a
self-contained HTML file with charts embedded as images:

  - Overview (reviews, words, accuracy, time, streaks)
  - Accuracy by card type and HSK level, daily volume and accuracy trend
  - Retention by interval, accuracy by weekday and hour
  - Hardest and most failed words, mastery, words needing attention
  - Tone patterns and characters that cost the most
  - Response time by card type and study recommendations

Usage:
    zhongwen-anki-analyze-stats
    zhongwen-anki-analyze-stats -i docs/local/estadisticas/anki_reviews.csv \
                                -o docs/local/estadisticas/report.html \
                                --cards docs/local/estadisticas/anki_reviews.cards.csv
"""
import argparse
import html as html_module
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from zhongwen_anki.card_types import CARD_NAME_TO_CODE
from zhongwen_anki.stats_charts import (
    BLUE,
    GREEN,
    GRID,
    INK_MUTED,
    INK_PRIMARY,
    INK_SECONDARY,
    ORANGE,
    RED,
    SURFACE,
    chart_accuracy_by_card_type,
    chart_accuracy_trend,
    chart_daily_reviews,
    chart_hard_words,
    chart_hour,
    chart_mastery_distribution,
    chart_response_time_by_type,
    chart_retention,
    chart_weekday,
)
from zhongwen_anki.stats_metrics import (
    MIN_GROUP_REVIEWS,
    accuracy_by,
    accuracy_by_hour,
    accuracy_by_weekday,
    character_difficulty,
    daily_trend,
    exclude_info_only,
    filter_user_answers,
    generate_recommendations,
    hardest_words,
    lapse_history,
    level_from_deck,
    level_sort_key,
    mastery_curve,
    most_lapsed_words,
    overview,
    response_time_analysis,
    retention_by_interval,
    tone_analysis,
    words_needing_attention,
)

UNKNOWN_CARD_TYPE = "Desconocido"


def load(csv_path: Path, cards_csv: Optional[Path] = None) -> tuple:
    """Return (reviews, card snapshot or None) read from the exported CSVs."""
    df = pd.read_csv(csv_path, parse_dates=["review_datetime"])
    if "card_type" not in df.columns:
        df["card_type"] = UNKNOWN_CARD_TYPE
    # CSVs exported before the card type codes hold template names.
    df["card_type"] = df["card_type"].fillna(UNKNOWN_CARD_TYPE).replace(CARD_NAME_TO_CODE)
    df["level"] = df["deck"].fillna("").map(level_from_deck)

    # CSVs exported before the `word` column existed.
    if "word" not in df.columns:
        df["word"] = df["note_id"].astype(str)

    # The level from the vocabulary (--vocab-dir) beats the one in the deck name.
    if "vocab_level" in df.columns and df["vocab_level"].notna().any():
        df["level"] = df["vocab_level"].where(
            df["vocab_level"].notna() & (df["vocab_level"] != ""), df["level"]
        )

    cards = None
    if cards_csv is not None and cards_csv.exists():
        cards = pd.read_csv(cards_csv, dtype=str)
        numeric = ["card_id", "note_id", "queue", "type", "ease_factor_percent", "lapses",
                   "fsrs_stability", "fsrs_difficulty"]
        for col in numeric:
            if col in cards.columns:
                cards[col] = pd.to_numeric(cards[col], errors="coerce")

    return df, cards


# --- HTML assembly --------------------------------------------------------

_PAGE_CSS = f"""
body {{ background:{SURFACE}; color:{INK_PRIMARY}; font-family: 'Segoe UI', Arial, sans-serif;
        max-width: 920px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
h1 {{ font-size: 1.6rem; margin-bottom: 0.2rem; }}
h2 {{ font-size: 1.15rem; margin-top: 2.5rem; border-bottom: 1px solid {GRID}; padding-bottom: 0.3rem; }}
h3 {{ font-size: 1rem; color: {INK_SECONDARY}; margin-top: 1.5rem; }}
.subtitle {{ color:{INK_SECONDARY}; margin-top: 0; }}
.stats-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin: 1rem 0; }}
.stat-tile {{ background:{SURFACE}; border:1px solid {GRID}; border-radius: 8px;
              padding: 0.75rem 1rem; }}
.stat-tile .value {{ font-size: 1.3rem; font-weight: 600; }}
.stat-tile .label {{ color:{INK_SECONDARY}; font-size: 0.8rem; }}
img.chart {{ max-width: 100%; display: block; margin: 0.75rem 0; border-radius: 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; margin: 0.5rem 0; }}
th, td {{ text-align: left; padding: 0.4rem 0.7rem; border-bottom: 1px solid {GRID}; }}
th {{ color:{INK_SECONDARY}; font-weight: 600; }}
.muted {{ color:{INK_MUTED}; font-size: 0.85rem; }}
.rec-box {{ border-left: 4px solid {BLUE}; padding: 0.75rem 1rem; margin: 0.75rem 0;
            background: rgba(42,120,214,0.05); border-radius: 0 6px 6px 0; }}
.rec-box.alert {{ border-left-color: {RED}; background: rgba(192,57,43,0.05); }}
.rec-box.warning {{ border-left-color: {ORANGE}; background: rgba(230,126,34,0.05); }}
.rec-box.success {{ border-left-color: {GREEN}; background: rgba(39,174,96,0.05); }}
.rec-title {{ font-weight: 600; margin-bottom: 0.25rem; }}
.rec-action {{ color:{BLUE}; font-weight: 500; margin-top: 0.3rem; }}
.badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }}
.badge-alta {{ background: rgba(192,57,43,0.15); color: {RED}; }}
.badge-media {{ background: rgba(230,126,34,0.15); color: {ORANGE}; }}
.badge-baja {{ background: rgba(39,174,96,0.15); color: {GREEN}; }}
"""


def _stat_tile(value: str, label: str) -> str:
    return f'<div class="stat-tile"><div class="value">{value}</div><div class="label">{label}</div></div>'


def _empty_report_html() -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Informe de aprendizaje</title><style>{_PAGE_CSS}</style></head><body>"
        "<h1>Informe de aprendizaje</h1>"
        "<p>Todavía no hay repasos en el CSV exportado. Estudia unos días en Anki, "
        "sincroniza con AnkiWeb, corre <code>zhongwen-anki-export-stats</code> de nuevo "
        "y vuelve a generar este informe.</p></body></html>"
    )


def _table_html(df: pd.DataFrame, columns: dict, max_rows: Optional[int] = None) -> str:
    """Generate HTML table. *columns* maps df column -> (header label, formatter, raw_html?).

    Formatter can return raw HTML (for badges) if the third element of the tuple is True.
    When raw_html is True, the cell content is NOT escaped.
    """
    data = df.head(max_rows) if max_rows else df
    head = "".join(f"<th>{html_module.escape(label)}</th>" for label, _, _ in columns.values())
    rows = []
    for _, row in data.iterrows():
        cells = "".join(
            f"<td>{fmt(row[col]) if raw else html_module.escape(str(fmt(row[col])))}</td>"
            for col, (_, fmt, raw) in columns.items()
        )
        rows.append(f"<tr>{cells}</tr>")
    footer = ""
    if max_rows and len(df) > max_rows:
        footer = f'<tr><td colspan="{len(columns)}" class="muted">... y {len(df) - max_rows} más</td></tr>'
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}{footer}</tbody></table>"


def _rec_html(rec: dict) -> str:
    cls = rec.get("type", "info")
    title = html_module.escape(rec.get("title", "Sugerencia"))
    msg = html_module.escape(rec["message"])
    action = rec.get("action", "")
    action_html = f'<div class="rec-action">Acción: {html_module.escape(action)}</div>' if action else ""
    return (
        f'<div class="rec-box {cls}">'
        f'<div class="rec-title">{title}</div>'
        f'<div>{msg}</div>'
        f"{action_html}"
        f"</div>"
    )


def build_report_html(df: pd.DataFrame, cards_df: Optional[pd.DataFrame] = None,
                      min_reviews: int = 3, mastery_streak: int = 4,
                      today: Optional[date] = None) -> str:
    """Build the full HTML report.

    Filters the input dataframe internally: user answers only, then excludes
    info-only card types for accuracy metrics.

    Args:
        df: Reviews dataframe.
        cards_df: Optional card snapshot dataframe.
        min_reviews: Minimum reviews for hardest-words table.
        mastery_streak: Consecutive correct answers needed for mastery.
        today: Reference date for streak calculation (for testing).
    """
    # Filter to user answers only
    df_user = filter_user_answers(df)

    # Exclude info-only card type from accuracy analysis
    df_accuracy = exclude_info_only(df_user)

    if df_user.empty:
        return _empty_report_html()

    # Volume metrics use every answer; accuracy leaves out the info-only card.
    ov = overview(df_user, today=today)
    if df_accuracy.empty:
        ov["overall_accuracy"] = float("nan")
    else:
        ov["overall_accuracy"] = df_accuracy["is_correct"].mean()
    if ov["total_reviews"] == 0:
        return _empty_report_html()

    by_card_type = accuracy_by(df_accuracy, "card_type")
    by_level = accuracy_by(df_accuracy, "level")
    daily = daily_trend(df_user)
    retention = retention_by_interval(df_user)
    weekday = accuracy_by_weekday(df_accuracy)
    hour = accuracy_by_hour(df_user)
    hard = hardest_words(df_accuracy, min_reviews=min_reviews)
    most_lapsed = most_lapsed_words(df_accuracy, min_lapses=2)
    mastery = mastery_curve(df_accuracy, mastery_streak=mastery_streak)
    attn = words_needing_attention(df_accuracy, cards_df, min_reviews=5)
    tone = tone_analysis(df_accuracy)
    char_diff = character_difficulty(df_accuracy)
    resp_time = response_time_analysis(df_accuracy)
    recommendations = generate_recommendations(df_accuracy, cards_df, today=today)

    accuracy_note = "<p class='muted'>La tarjeta <em>Toda la información</em> (consulta) no cuenta en la precisión.</p>"

    # Pinyin/meaning enrichment is only present when the export used --vocab-dir.
    has_pinyin_info = "pinyin" in df.columns and "meaning_es" in df.columns

    def _pinyin_cell(row):
        parts = []
        p = str(row.get("pinyin") or "").strip()
        m = str(row.get("meaning_es") or "").strip()
        if p:
            parts.append(p)
        if m:
            parts.append(m)
        return " · ".join(parts)

    # --- Stats tiles ---
    if ov.get("streak_alive", True):
        current_streak_display = str(ov["current_streak"])
    else:
        last_date = ov.get("last_streak_ended", "")
        if last_date:
            current_streak_display = f"0 (última sesión: {last_date})"
        else:
            current_streak_display = "0"
    accuracy_display = "—" if pd.isna(ov["overall_accuracy"]) else f"{ov['overall_accuracy'] * 100:.0f}%"
    stats = "".join([
        _stat_tile(f"{ov['total_reviews']:,}", "Repasos totales"),
        _stat_tile(f"{ov['unique_words']:,}", "Notas distintas"),
        _stat_tile(accuracy_display, "Precisión global"),
        _stat_tile(f"{ov['total_time_hours']:.1f} h", "Tiempo total"),
        _stat_tile(f"{ov['active_days']} / {ov['span_days']}", "Días activos / periodo"),
        _stat_tile(f"{ov['avg_reviews_per_active_day']:.0f}", "Repasos/día activo"),
        _stat_tile(f"{ov['avg_time_per_review']:.1f}s", "Tiempo/repaso"),
        _stat_tile(current_streak_display, "Racha actual"),
        _stat_tile(str(ov["longest_streak"]), "Racha máxima"),
    ])

    # --- Recommendations ---
    rec_section = ""
    if recommendations:
        recs_html = "".join(_rec_html(r) for r in recommendations)
        rec_section = f"<h2>Recomendaciones personalizadas</h2>{recs_html}"

    # --- Card type accuracy ---
    card_type_section = (
        "<h2>Precisión por tipo de tarjeta</h2>"
        "<p class='muted'>En qué habilidad conviene incidir. Cada código indica qué se muestra y qué "
        "se pide: H = hanzi, P = pinyin, M = significado (p. ej. PM2H: pinyin + significado → dibujar hanzi).</p>"
        f"<img class='chart' src='{chart_accuracy_by_card_type(by_card_type)}' alt='Precisión por tipo de tarjeta'>"
        f"{accuracy_note}"
    )

    # --- Level section ---
    level_section = ""
    if by_level["level"].nunique() > 1:
        level_sorted = by_level.sort_values("level", key=lambda s: s.map(level_sort_key))
        level_rows = "".join(
            f"<tr><td>{html_module.escape(str(row['level']))}</td><td>{int(row['reviews'])}</td>"
            f"<td>{float(row['accuracy']) * 100:.0f}%</td><td>{float(row['avg_time_s']):.1f}s</td></tr>"
            for _, row in level_sorted.iterrows()
        )
        level_section = (
            "<h2>Precisión por nivel</h2>"
            "<table><thead><tr><th>Nivel</th><th>Repasos</th><th>Precisión</th><th>Tiempo medio</th></tr></thead>"
            f"<tbody>{level_rows}</tbody></table>"
        )

    # --- Daily trends ---
    daily_section = (
        "<h2>Volumen de repasos por día</h2>"
        f"<img class='chart' src='{chart_daily_reviews(daily)}' alt='Repasos por día'>"
        "<h2>Tendencia de precisión (media móvil 7 días)</h2>"
        f"<img class='chart' src='{chart_accuracy_trend(daily)}' alt='Tendencia de precisión'>"
    )

    # --- Retention ---
    retention_section = ""
    if not retention.empty:
        retention_section = (
            "<h2>Retención por intervalo</h2>"
            "<p class='muted'>Precisión en repasos programados (no en pasos de aprendizaje), "
            "agrupada por cuánto tiempo había pasado desde el repaso anterior. Si baja mucho en "
            "los intervalos largos, Anki está espaciando demasiado agresivo para ese contenido. "
            "Buckets marcados con * tienen muestra pequeña (n<5) y son menos fiables.</p>"
            f"<img class='chart' src='{chart_retention(retention)}' alt='Retención por intervalo'>"
        )
    else:
        retention_section = (
            "<h2>Retención por intervalo</h2>"
            "<p class='muted'>Aún no hay repasos programados (tipo <em>review</em>): todas las tarjetas "
            "siguen en aprendizaje. Esta sección aparecerá cuando Anki empiece a espaciar repasos.</p>"
        )

    # --- Weekday ---
    weekday_section = ""
    if not weekday.empty:
        weekday_section = (
            "<h2>Precisión por día de la semana</h2>"
            f"<img class='chart' src='{chart_weekday(weekday)}' alt='Precisión por día de la semana'>"
        )

    # --- Hour ---
    hour_section = ""
    if not hour.empty:
        hour_chart = chart_hour(hour)
        hour_section = (
            "<h2>Repasos y precisión por hora del día</h2>"
            "<p class='muted'>La línea de precisión solo se dibuja en horas con al menos "
            f"{MIN_GROUP_REVIEWS} repasos.</p>"
            f"<img class='chart' src='{hour_chart}' alt='Repasos y precisión por hora del día'>"
        )

    # --- Response time ---
    resp_section = ""
    if not resp_time.empty:
        resp_section = (
            "<h2>Tiempo de respuesta por habilidad</h2>"
            "<p class='muted'>Tiempo medio en responder correctamente vs. incorrectamente. "
            "Si el tiempo incorrecto es muy alto, puede indicar confusión (no solo olvido).</p>"
            f"<img class='chart' src='{chart_response_time_by_type(resp_time)}' alt='Tiempo de respuesta'>"
        )

    # --- Hard words ---
    hard_section = ""
    if not hard.empty:
        hard_chart = chart_hard_words(hard, min_reviews=min_reviews)
        hard_disp = hard.copy()
        hard_columns = {
            "word": ("Palabra", lambda v: str(v), False),
            "card_type": ("Tipo", str, False),
            "reviews": ("Repasos", int, False),
            "agains": ("Fallos (Again)", int, False),
            "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
            "avg_time": ("Tiempo medio", lambda v: f"{v:.1f}s", False),
        }
        if has_pinyin_info:
            hard_disp["pinyin_meaning"] = hard_disp.apply(_pinyin_cell, axis=1)
            hard_columns["pinyin_meaning"] = ("Pinyin / significado", str, False)
        hard_table = _table_html(hard_disp, hard_columns)
        hard_section = (
            "<h2>Palabras más difíciles</h2>"
            "<p class='muted'>Las combinaciones nota/tipo con peor precisión "
            f"(mínimo {min_reviews} repasos). Candidatas prioritarias para repaso dirigido.</p>"
            f"<img class='chart' src='{hard_chart}' alt='Palabras más difíciles'>"
            f"{hard_table}"
        )

    # --- Most lapsed ---
    lapsed_section = ""
    if not most_lapsed.empty:
        lapsed_table = _table_html(most_lapsed.head(20), {
            "word": ("Palabra", lambda v: str(v), False),
            "card_type": ("Tipo", str, False),
            "agains": ("Fallos (Again)", int, False),
            "reviews": ("Repasos", int, False),
            "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
        })
        recent_lapses = ""
        lapses_hist = lapse_history(df_accuracy)
        if not lapses_hist.empty:
            latest = lapses_hist.sort_values("lapse_date", ascending=False).head(20)
            recent_lapses = (
                "<h3>Últimos fallos</h3>"
                "<p class='muted'>Los 20 fallos (Again) más recientes: cuándo, qué palabra y de qué tipo.</p>"
                + _table_html(latest, {
                    "lapse_date": ("Fecha", str, False),
                    "word": ("Palabra", lambda v: str(v), False),
                    "card_type": ("Tipo", str, False),
                    "lapse_number": ("Nº de fallo", int, False),
                })
            )
        lapsed_section = (
            "<h3>Palabras con más fallos acumulados</h3>"
            f"{lapsed_table}"
            f"{recent_lapses}"
        )

    # --- Mastery ---
    mastery_section = ""
    if not mastery.empty:
        mastered_count = mastery["mastered"].sum()
        total_combos = len(mastery)
        mastery_chart = chart_mastery_distribution(mastery, mastery_streak=mastery_streak)
        mastery_img = f"<img class='chart' src='{mastery_chart}' alt='Distribución de dominio'>" if mastery_chart else ""
        mastery_stats = "".join([
            _stat_tile(str(mastered_count), "Combinaciones dominadas"),
            _stat_tile(str(total_combos - mastered_count), "Sin dominar"),
            _stat_tile(f"{mastered_count / total_combos * 100:.0f}%", "Tasa de dominio"),
        ])
        not_mastered = mastery[~mastery["mastered"]].sort_values("total_reviews", ascending=False).head(15)
        if not not_mastered.empty:
            mastery_table = _table_html(not_mastered, {
                "word": ("Palabra", lambda v: str(v), False),
                "card_type": ("Tipo", str, False),
                "total_reviews": ("Repasos", int, False),
                "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
                "max_consecutive_correct": ("Racha máxima", int, False),
            })
        else:
            mastery_table = "<p class='muted'>Todas las combinaciones están dominadas.</p>"
        mastery_section = (
            "<h2>Curva de dominio</h2>"
            f"<p class='muted'>Dominio = {mastery_streak} aciertos consecutivos en el mismo tipo de tarjeta (solo repasos programados).</p>"
            f"<div class='stats-row'>{mastery_stats}</div>"
            f"{mastery_img}"
            f"<h3>Combinaciones que aún no dominan</h3>"
            f"{mastery_table}"
        )
    else:
        mastery_section = (
            "<h2>Curva de dominio</h2>"
            "<p class='muted'>Aún no hay repasos programados (tipo <em>review</em>) "
            f"con al menos {mastery_streak} aciertos consecutivos: no se puede calcular el dominio.</p>"
        )

    # --- Words needing attention ---
    attention_section = ""
    if not attn.empty:
        attn_disp = attn.copy()
        attn_columns = {
            "word": ("Palabra", lambda v: str(v), False),
            "priority": ("Prioridad", lambda v: f'<span class="badge badge-{v}">{v}</span>', True),
            "reviews": ("Repasos", int, False),
            "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
            "agains": ("Fallos (Again)", int, False),
            "reasons": ("Motivo", str, False),
            "last_review": ("Último repaso", str, False),
        }
        if has_pinyin_info:
            attn_disp["pinyin_meaning"] = attn_disp.apply(_pinyin_cell, axis=1)
            attn_columns["pinyin_meaning"] = ("Pinyin / significado", str, False)
        attn_table = _table_html(attn_disp, attn_columns)
        attention_section = (
            "<h2>Palabras que necesitan atención</h2>"
            "<p class='muted'>Palabras con precisión críticamente baja o muchos fallos. "
            "Son las prioritarias para un repaso enfocado.</p>"
            f"{attn_table}"
        )

    # --- Linguistic analysis ---
    linguistic_section = ""
    if not tone.empty:
        tone_table = _table_html(tone.head(20), {
            "tone_pattern": ("Patrón tonal", lambda v: v.replace("-", " → "), False),
            "reviews": ("Repasos", int, False),
            "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
        })
        linguistic_section += (
            "<h2>Análisis lingüístico</h2>"
            "<h3>Dificultad por patrón tonal</h3>"
            "<p class='muted'>Solo incluye tarjetas que piden escribir pinyin (H2P, M2P, HM2P) "
            f"y patrones con al menos {MIN_GROUP_REVIEWS} repasos. "
            "Los patrones con peor precisión indican combinaciones de tonos que cuestan más.</p>"
            f"{tone_table}"
        )
    else:
        if "pinyin" in df.columns:
            tone_empty_msg = (
                "Aún no hay repasos de tarjetas que piden escribir pinyin (H2P, M2P, HM2P). "
                "Esta sección aparecerá cuando empieces a estudiarlas."
            )
        else:
            tone_empty_msg = (
                "No hay datos de pinyin para analizar tonos. Exporta con "
                "<code>zhongwen-anki-export-stats --vocab-dir data</code> para incluir el pinyin."
            )
        linguistic_section += (
            "<h2>Análisis lingüístico</h2>"
            f"<p class='muted'>{tone_empty_msg}</p>"
        )

    if not char_diff.empty:
        char_table = _table_html(char_diff.head(20), {
            "char": ("Carácter", str, False),
            "reviews": ("Apariciones", int, False),
            "accuracy": ("Precisión", lambda v: f"{v * 100:.0f}%", False),
        })
        linguistic_section += (
            "<h3>Caracteres más difíciles de escribir</h3>"
            "<p class='muted'>Solo incluye tarjetas que piden dibujar el hanzi (M2H, PM2H, P2H) "
            f"y caracteres vistos al menos {MIN_GROUP_REVIEWS} veces. "
            "Caracteres que aparecen en palabras con baja precisión pueden indicar dificultad intrínseca (trazos, confusión visual).</p>"
            f"{char_table}"
        )

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Informe de aprendizaje — Chino HSK</title>
<style>{_PAGE_CSS}</style>
</head><body>
<h1>Informe de aprendizaje — Chino HSK</h1>
<p class="subtitle">{ov['date_start']:%Y-%m-%d} → {ov['date_end']:%Y-%m-%d} · {ov['span_days']} días</p>
<div class="stats-row">{stats}</div>

{rec_section}

{card_type_section}
{level_section}

{daily_section}
{retention_section}
{weekday_section}
{hour_section}
{resp_section}

{hard_section}
{lapsed_section}

{mastery_section}
{attention_section}
{linguistic_section}

<hr style="margin:2rem 0;border:none;border-top:1px solid {GRID}">
<p class="muted">Generado con <code>zhongwen-anki-analyze-stats</code> · {datetime.now():%Y-%m-%d %H:%M}</p>
</body></html>"""


# --- CLI -----------------------------------------------------------------

def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an HTML learning report from the CSV of zhongwen-anki-export-stats."
    )
    parser.add_argument("-i", "--input", type=Path,
                        default=Path("docs/local/estadisticas/anki_reviews.csv"),
                        help="Reviews CSV exported by zhongwen-anki-export-stats.")
    parser.add_argument("-o", "--output", type=Path,
                        default=Path("docs/local/estadisticas/report.html"),
                        help="Output HTML report path.")
    parser.add_argument("--cards", type=Path, default=None,
                        help="Card snapshot CSV (--card-snapshot from export).")
    parser.add_argument("--min-reviews", type=int, default=3,
                        help="Minimum reviews for a word to appear in hardest-words table. Default: 3.")
    parser.add_argument("--mastery-streak", type=int, default=4,
                        help="Consecutive correct answers needed for mastery. Default: 4.")
    parser.add_argument("--today", type=_parse_date, default=None,
                        help="Reference date for the streaks (YYYY-MM-DD). Default: today.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.input.exists():
        raise SystemExit(f"{args.input} not found. Run `zhongwen-anki-export-stats` first.")
    if args.cards is not None and not args.cards.exists():
        raise SystemExit(
            f"{args.cards} not found. Run `zhongwen-anki-export-stats --card-snapshot` first."
        )

    df, cards = load(args.input, args.cards)
    report = build_report_html(
        df, cards,
        min_reviews=args.min_reviews,
        mastery_streak=args.mastery_streak,
        today=args.today,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote report -> {args.output}")


if __name__ == "__main__":
    main()
