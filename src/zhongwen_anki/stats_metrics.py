"""Metrics and study recommendations computed from the review CSV of
zhongwen-anki-export-stats."""
import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from zhongwen_anki.card_types import INFO_CARD_TYPE
from zhongwen_anki.utilities import is_chinese_character, pinyin_to_numbered

LEVEL_RE = re.compile(r"HSK(\d+)")
NO_LEVEL = "Sin nivel"

# Tone numbers in numbered pinyin ("ai4hao4" -> 4, 4).
_NUMBERED_TONE_RE = re.compile(r"([1-5])")

WEEKDAY_ES = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo",
}
WEEKDAY_ORDER = list(WEEKDAY_ES.values())

LOW_ACCURACY = 0.6
CRITICAL_ACCURACY = 0.5
WEAK_SKILL_ACCURACY = 0.7
EXCELLENT_ACCURACY = 0.9
LOW_EASE_ATTENTION = 200
MANY_LAPSES = 3
EASE_HELL = 150
MIN_ACTIVE_DAYS_FOR_RECS = 7
MIN_REVIEWS_FOR_RECS = 200
# Minimum number of reviews a word/group needs before we report it. Below this
# the figures are just noise (e.g. a tone pattern seen once at 100%).
MIN_GROUP_REVIEWS = 5

# Card types that ask the user to write or recognize pinyin (for tone analysis)
PINYIN_OUTPUT_CARD_TYPES = {"H2P", "M2P", "HM2P"}

# Card types where the hanzi is the target (for character difficulty)
HANZI_TARGET_CARD_TYPES = {"M2H", "PM2H", "P2H"}


def level_from_deck(deck: str) -> str:
    m = LEVEL_RE.search(deck or "")
    return f"HSK{m.group(1)}" if m else NO_LEVEL


def level_sort_key(level: str) -> int:
    m = LEVEL_RE.fullmatch(level)
    return int(m.group(1)) if m else 999


def filter_user_answers(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the user's answers, dropping system actions (reschedules).

    Uses the `is_user_answer` column; CSVs exported before it existed are
    filtered by ease >= 1 and a review type other than manual/rescheduled.
    """
    if "is_user_answer" in df.columns:
        mask = df["is_user_answer"].astype(str).str.lower() == "true"
    else:
        rev_type_col = df["review_type"] if "review_type" in df.columns else pd.Series("", index=df.index)
        rev_type_str = rev_type_col.astype(str).str.lower()
        mask = ~rev_type_str.isin(["manual", "rescheduled", "4", "5"])
        if "ease" in df.columns:
            ease = pd.to_numeric(df["ease"], errors="coerce").fillna(0)
            mask = mask & (ease >= 1)
    return df[mask].copy()


def exclude_info_only(df: pd.DataFrame) -> pd.DataFrame:
    """Exclude informational-only card types from accuracy analysis."""
    if df.empty or "card_type" not in df.columns:
        return df
    return df[df["card_type"] != INFO_CARD_TYPE]


# --- metrics ------------------------------------------------------------

def overview(df: pd.DataFrame, today: Optional[date] = None) -> dict:
    if df.empty:
        return {"total_reviews": 0}
    span_days = (df["review_datetime"].max() - df["review_datetime"].min()).days + 1
    active_days = df["review_datetime"].dt.date.nunique()
    streaks = _calculate_streaks(df, today=today)
    unique_words = df["word"].nunique() if "word" in df.columns else df["note_id"].nunique()
    return {
        "total_reviews": len(df),
        "unique_words": unique_words,
        "unique_card_types": df["card_type"].nunique(),
        "date_start": df["review_datetime"].min(),
        "date_end": df["review_datetime"].max(),
        "span_days": span_days,
        "active_days": active_days,
        "total_time_hours": df["time_seconds"].sum() / 3600,
        "overall_accuracy": df["is_correct"].mean(),
        "avg_reviews_per_active_day": len(df) / active_days if active_days else 0,
        "avg_time_per_review": df["time_seconds"].mean(),
        "current_streak": streaks["current"],
        "longest_streak": streaks["longest"],
        "streak_alive": streaks["alive"],
        "last_streak_ended": streaks.get("last_ended"),
        "total_again": (df["ease_label"] == "Again").sum(),
        "total_hard": (df["ease_label"] == "Hard").sum(),
        "total_good": (df["ease_label"] == "Good").sum(),
        "total_easy": (df["ease_label"] == "Easy").sum(),
    }


def _calculate_streaks(df: pd.DataFrame, today: Optional[date] = None) -> dict:
    """Calculate current and longest study streaks (consecutive active days).

    Streak is 'alive' if the last active day is today or yesterday.
    If last active day is before yesterday, streak is broken.

    Args:
        df: Reviews dataframe with 'review_datetime' column.
        today: Reference date for streak calculation (default: actual today).
    """
    active_dates = sorted(set(df["review_datetime"].dt.date))
    if not active_dates:
        return {"current": 0, "longest": 0, "alive": False}

    reference = today or date.today()
    last_active = active_dates[-1]

    # Calculate longest streak
    longest = 1
    temp = 1
    for i in range(1, len(active_dates)):
        if active_dates[i] - active_dates[i - 1] == timedelta(days=1):
            temp += 1
            longest = max(longest, temp)
        else:
            temp = 1

    # Calculate current streak (going backwards from last active day)
    current = 1
    for i in range(len(active_dates) - 1, 0, -1):
        if active_dates[i] - active_dates[i - 1] == timedelta(days=1):
            current += 1
        else:
            break

    # Check if streak is alive
    days_since_last = (reference - last_active).days
    alive = days_since_last <= 1

    result: dict = {"current": current if alive else 0, "longest": longest, "alive": alive}

    if not alive:
        result["last_ended"] = last_active.strftime("%Y-%m-%d")

    return result


def accuracy_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    g = (
        df.groupby(column)
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"),
             avg_time_s=("time_seconds", "mean"),
             agains=("ease_label", lambda s: (s == "Again").sum()))
        .reset_index()
        .sort_values("accuracy")
    )
    return g


def daily_trend(df: pd.DataFrame) -> pd.DataFrame:
    daily = (
        df.set_index("review_datetime")
        .resample("D")
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"),
             avg_time=("time_seconds", "mean"))
    )
    daily["accuracy_7d"] = daily["accuracy"].rolling(7, min_periods=1).mean()
    daily["reviews_7d"] = daily["reviews"].rolling(7, min_periods=1).mean()
    return daily.dropna(subset=["reviews"]).reset_index()


def retention_by_interval(df: pd.DataFrame) -> pd.DataFrame:
    """Retention by interval bucket with sample sizes and reliability markers.

    Buckets: 1d, 2-3d, 4-7d, 8-30d, >30d
    Shows sample size (n) for each bucket and marks unreliable buckets (n < 5).
    """
    reviewed = df[(df["review_type"] == "review") & (df["last_interval"] > 0)].copy()
    if reviewed.empty:
        return pd.DataFrame(columns=["bucket", "reviews", "accuracy", "reliable"])
    bins = [0, 1, 3, 7, 30, float("inf")]
    labels = ["1d", "2-3d", "4-7d", "8-30d", ">30d"]
    reviewed["bucket"] = pd.cut(reviewed["last_interval"], bins=bins, labels=labels)
    result = (
        reviewed.groupby("bucket", observed=True)
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"))
        .reset_index()
    )
    result["reliable"] = result["reviews"] >= 5
    return result


def accuracy_by_weekday(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["weekday"] = tmp["review_datetime"].dt.day_name().map(WEEKDAY_ES)
    g = (
        tmp.groupby("weekday")
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"))
        .reindex(WEEKDAY_ORDER)
        .dropna(how="all")
        .reset_index()
    )
    return g


def accuracy_by_hour(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["hour"] = tmp["review_datetime"].dt.hour
    g = (
        tmp.groupby("hour")
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"))
        .reset_index()
    )
    return g


# --- deep failure analysis -----------------------------------------------

def hardest_words(df: pd.DataFrame, min_reviews: int = 3, top: int = 25) -> pd.DataFrame:
    """Words with worst accuracy, ranked by (accuracy ASC, Fallos DESC).

    Groups by note_id (not word) since same hanzi can represent different words.
    Includes word column for display (first word value per group).
    """
    agg_dict = {
        "reviews": ("is_correct", "size"),
        "agains": ("ease_label", lambda s: (s == "Again").sum()),
        "accuracy": ("is_correct", "mean"),
        "avg_time": ("time_seconds", "mean"),
    }
    if "word" in df.columns:
        agg_dict["word"] = ("word", "first")
    if "pinyin" in df.columns:
        agg_dict["pinyin"] = ("pinyin", "first")
    if "meaning_es" in df.columns:
        agg_dict["meaning_es"] = ("meaning_es", "first")
    g = (
        df.groupby(["note_id", "card_type"])
        .agg(**agg_dict)
        .reset_index()
    )
    g = g[g["reviews"] >= min_reviews].sort_values(["accuracy", "agains"], ascending=[True, False])
    return g.head(top)


def most_lapsed_words(df: pd.DataFrame, min_lapses: int = 2, top: int = 25) -> pd.DataFrame:
    """Words with the most 'Again' responses, regardless of total reviews.

    Groups by note_id (not word) since same hanzi can represent different words.
    Includes word column for display (first word value per group).
    """
    agg_dict = {
        "reviews": ("is_correct", "size"),
        "agains": ("ease_label", lambda s: (s == "Again").sum()),
        "accuracy": ("is_correct", "mean"),
    }
    if "word" in df.columns:
        agg_dict["word"] = ("word", "first")
    g = (
        df.groupby(["note_id", "card_type"])
        .agg(**agg_dict)
        .reset_index()
    )
    g = g[g["agains"] >= min_lapses].sort_values("agains", ascending=False)
    return g.head(top)


def mastery_curve(df: pd.DataFrame, mastery_streak: int = 4) -> pd.DataFrame:
    """Reviews each word/card type needed to be mastered, i.e. answered right
    *mastery_streak* times in a row in scheduled reviews (not learning steps).

    `reviews_to_mastery` is the number of the review at which the streak was
    reached, or None if it hasn't been yet.
    """
    if df.empty:
        return pd.DataFrame()
    if "review_type" in df.columns:
        reviews_only = df[df["review_type"] == "review"].copy()
    else:
        reviews_only = df.copy()
    if reviews_only.empty:
        return pd.DataFrame()

    results = []
    sorted_df = reviews_only.sort_values("review_datetime")
    for (note_id, card_type), group in sorted_df.groupby(["note_id", "card_type"]):
        streak = 0
        reviews_to_mastery = None
        position = 0
        for _, row in group.iterrows():
            position += 1
            if row["is_correct"]:
                streak += 1
                if streak >= mastery_streak and reviews_to_mastery is None:
                    reviews_to_mastery = position
            else:
                streak = 0
        total = len(group)
        acc = group["is_correct"].mean()
        max_streak = _max_consecutive(group["is_correct"].values)
        entry = {
            "note_id": note_id,
            "card_type": card_type,
            "total_reviews": total,
            "accuracy": acc,
            "reviews_to_mastery": reviews_to_mastery,
            "max_consecutive_correct": max_streak,
            "mastered": reviews_to_mastery is not None,
        }
        if "word" in group.columns:
            entry["word"] = group["word"].iloc[0]
        results.append(entry)
    return pd.DataFrame(results)


def _max_consecutive(arr) -> int:
    """Maximum streak of True values in a boolean array."""
    max_streak = 0
    current = 0
    for v in arr:
        if v:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def lapse_history(df: pd.DataFrame) -> pd.DataFrame:
    """One row per "Again" answer, numbered per word and card type."""
    lapses_df = df[df["ease_label"] == "Again"].copy()
    if lapses_df.empty:
        cols = ["note_id", "card_type", "lapse_date", "lapse_number"]
        if "word" in df.columns:
            cols.append("word")
        return pd.DataFrame(columns=cols)
    result = []
    has_word = "word" in lapses_df.columns
    for (note_id, card_type), group in lapses_df.sort_values("review_datetime").groupby(["note_id", "card_type"]):
        word_val = group["word"].iloc[0] if has_word else None
        for i, (_, row) in enumerate(group.iterrows(), 1):
            entry = {
                "note_id": note_id,
                "card_type": card_type,
                "lapse_date": row["review_datetime"].date(),
                "lapse_number": i,
                "day_of_week": row["review_datetime"].day_name(),
            }
            if has_word:
                entry["word"] = word_val
            result.append(entry)
    return pd.DataFrame(result)


def words_needing_attention(df: pd.DataFrame, cards_df: Optional[pd.DataFrame] = None,
                            min_reviews: int = 5) -> pd.DataFrame:
    """Identify words that currently need focused attention.

    Groups by (note_id, card_type) since the same word can be tested in
    different ways with different difficulty.  Also incorporates cards_df
    data when available to check relearning queue and ease factor.

    Criteria (any of):
      - Accuracy < 60% with ≥min_reviews
      - Currently in relearning queue (from cards snapshot)
      - MANY_LAPSES or more "Again" answers
      - Ease factor < LOW_EASE_ATTENTION (from cards snapshot)
    """
    agg_dict = {
        "reviews": ("is_correct", "size"),
        "accuracy": ("is_correct", "mean"),
        "agains": ("ease_label", lambda s: (s == "Again").sum()),
        "last_review": ("review_datetime", "max"),
    }
    if "word" in df.columns:
        agg_dict["word"] = ("word", "first")
    if "pinyin" in df.columns:
        agg_dict["pinyin"] = ("pinyin", "first")
    if "meaning_es" in df.columns:
        agg_dict["meaning_es"] = ("meaning_es", "first")
    word_stats = (
        df.groupby(["note_id", "card_type"])
        .agg(**agg_dict)
        .reset_index()
    )

    issues = []
    has_word = "word" in word_stats.columns
    for _, row in word_stats.iterrows():
        if row["reviews"] < min_reviews:
            continue
        reasons = []
        if row["accuracy"] < LOW_ACCURACY:
            reasons.append(f"baja precisión ({row['accuracy']:.0%})")
        if row["agains"] >= MANY_LAPSES:
            reasons.append(f"{int(row['agains'])} Fallos")
        if row["accuracy"] < CRITICAL_ACCURACY and row["reviews"] >= 10:
            reasons.append("crítica")

        if cards_df is not None and "note_id" in cards_df.columns:
            card_match = cards_df[
                (cards_df["note_id"] == row["note_id"]) &
                (cards_df["card_type"] == row["card_type"])
            ]
            if not card_match.empty:
                # Card type 3 is relearning.
                if "type" in card_match.columns:
                    if (card_match["type"] == 3).any():
                        reasons.append("en relearning")
                elif "queue_label" in card_match.columns:
                    if card_match["queue_label"].str.contains("relearning", na=False).any():
                        reasons.append("en relearning")
                if "ease_factor_percent" in card_match.columns:
                    if (card_match["ease_factor_percent"] < LOW_EASE_ATTENTION).any():
                        reasons.append("ease factor bajo")

        if reasons:
            priority = "alta" if "crítica" in reasons or "en relearning" in reasons else "media"
            entry = {
                "note_id": row["note_id"],
                "reviews": int(row["reviews"]),
                "accuracy": row["accuracy"],
                "agains": int(row["agains"]),
                "last_review": row["last_review"].strftime("%Y-%m-%d"),
                "reasons": ", ".join(reasons),
                "priority": priority,
            }
            if has_word:
                entry["word"] = row["word"]
            issues.append(entry)

    result = pd.DataFrame(issues)
    if result.empty:
        return result
    result["priority_sort"] = result["priority"].map({"alta": 0, "media": 1})
    return result.sort_values(["priority_sort", "accuracy"], ascending=[True, True]).drop(columns=["priority_sort"])


# --- linguistic analysis -------------------------------------------------

def tone_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy by tone pattern ("4-3" for àihǎo) on the cards that ask for
    the pinyin (H2P, M2P, HM2P), the only ones that test the tones."""
    if "pinyin" not in df.columns:
        return pd.DataFrame()
    df_work = df[df["card_type"].isin(PINYIN_OUTPUT_CARD_TYPES)].copy()
    if "pinyin" not in df_work.columns or df_work["pinyin"].notna().sum() == 0:
        return pd.DataFrame()
    df_work = df_work[df_work["pinyin"].notna() & (df_work["pinyin"] != "")].copy()
    if df_work.empty:
        return pd.DataFrame()

    def extract_tone_pattern(pinyin_str):
        numbered = pinyin_to_numbered(str(pinyin_str))
        if not numbered:
            return None
        tones = _NUMBERED_TONE_RE.findall(numbered)
        if not tones:
            return None
        return "-".join(tones)

    df_work["tone_pattern"] = df_work["pinyin"].apply(extract_tone_pattern)
    df_work = df_work.dropna(subset=["tone_pattern"])
    if df_work.empty:
        return pd.DataFrame()

    g = (
        df_work.groupby("tone_pattern")
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"))
        .reset_index()
    )
    # Drop patterns seen fewer than MIN_GROUP_REVIEWS times: they are noise.
    g = g[g["reviews"] >= MIN_GROUP_REVIEWS]
    return g.sort_values("accuracy")


def character_difficulty(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per character on the cards that ask to draw the hanzi
    (M2H, PM2H, P2H)."""
    df_work = df[df["card_type"].isin(HANZI_TARGET_CARD_TYPES)][["word", "is_correct", "card_type"]].copy()
    df_work["word"] = df_work["word"].astype(str)

    exploded = df_work.assign(char=df_work["word"].apply(list)).explode("char")
    char_df = exploded[exploded["char"].map(lambda c: isinstance(c, str) and is_chinese_character(c))]

    if char_df.empty:
        return pd.DataFrame()

    g = (
        char_df.groupby("char")
        .agg(reviews=("is_correct", "size"), accuracy=("is_correct", "mean"))
        .reset_index()
    )
    # Drop characters seen fewer than MIN_GROUP_REVIEWS times: they are noise.
    g = g[g["reviews"] >= MIN_GROUP_REVIEWS]
    return g.sort_values("accuracy")


# --- response time analysis ----------------------------------------------

def response_time_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Average response time per card type, overall and for right and wrong answers."""
    time_pivot = (
        df.groupby(["card_type", "is_correct"])["time_seconds"]
        .mean()
        .unstack(fill_value=0)
    )

    result = df.groupby("card_type").agg(
        overall_avg=("time_seconds", "mean"),
        reviews=("time_seconds", "size"),
    ).reset_index()

    if 1 in time_pivot.columns:
        result["avg_time_correct"] = result["card_type"].map(time_pivot[1]).fillna(0)
    else:
        result["avg_time_correct"] = 0

    if 0 in time_pivot.columns:
        result["avg_time_incorrect"] = result["card_type"].map(time_pivot[0]).fillna(0)
    else:
        result["avg_time_incorrect"] = 0

    return result


# --- recommendations engine ----------------------------------------------

def generate_recommendations(df: pd.DataFrame, cards_df: Optional[pd.DataFrame] = None,
                             today: Optional[date] = None) -> list:
    """Study recommendations, as dicts with type, priority, title, message
    and an optional action, sorted by priority."""
    recommendations = []

    if df.empty:
        return [{"type": "info", "message": "Aún no hay datos de repasos. Estudia unos días y vuelve a generar el informe."}]

    ov = overview(df, today=today)

    insufficient = ov["active_days"] < MIN_ACTIVE_DAYS_FOR_RECS or ov["total_reviews"] < MIN_REVIEWS_FOR_RECS

    def prio(priority: str) -> str:
        # With too little data, every recommendation is only a hint.
        return "low" if insufficient else priority

    # 1. Overall accuracy check
    if ov["overall_accuracy"] < WEAK_SKILL_ACCURACY:
        recommendations.append({
            "type": "alert",
            "priority": prio("high"),
            "title": "Precisión general baja",
            "message": f"Tu precisión global es {ov['overall_accuracy']:.0%}. Considera reducir el número de tarjetas nuevas por día y enfocarte en repasos.",
        })
    elif ov["overall_accuracy"] >= EXCELLENT_ACCURACY:
        recommendations.append({
            "type": "success",
            "priority": "low",
            "title": "Excelente precisión",
            "message": f"Tu precisión global es {ov['overall_accuracy']:.0%}. ¡Vas muy bien! Puedes considerar aumentar el ritmo de tarjetas nuevas.",
        })

    # 2. Weak skills
    by_type = accuracy_by(df, "card_type")
    weak = by_type[by_type["accuracy"] < WEAK_SKILL_ACCURACY]
    if not weak.empty:
        skills = ", ".join(f"{r['card_type']} ({r['accuracy']:.0%})" for _, r in weak.iterrows())
        recommendations.append({
            "type": "warning",
            "priority": prio("medium"),
            "title": "Habilidades débiles detectadas",
            "message": f"Estas habilidades tienen precisión < 70%: {skills}",
            "action": "Crea un mazo filtrado con: (" + " OR ".join(f"card:{r['card_type']}" for _, r in weak.iterrows()) + ")",
        })

    # 3. Hardest words
    hard = hardest_words(df, min_reviews=5, top=10)
    critical = hard[hard["accuracy"] < CRITICAL_ACCURACY]
    if not critical.empty:
        recommendations.append({
            "type": "warning",
            "priority": prio("high"),
            "title": "Palabras críticamente difíciles",
            "message": f"{len(critical)} combinaciones palabra/tipo tienen precisión < 50% con al menos {MIN_GROUP_REVIEWS} repasos.",
            "action": "Crea un mazo filtrado para estas palabras específicas y revísalas diariamente.",
        })

    # 4. Streak advice
    if not ov.get("streak_alive", True) and ov["total_reviews"] > 0:
        ended_str = f" (última sesión: {ov.get('last_streak_ended', 'desconocida')})" if ov.get("last_streak_ended") else ""
        recommendations.append({
            "type": "info",
            "priority": prio("medium"),
            "title": "Racha de estudio rota",
            "message": f"Has perdido la racha de estudio{ended_str}. ¡Recupérala con una sesión hoy!",
        })
    elif ov.get("current_streak", 0) >= 7 and ov.get("streak_alive", False):
        recommendations.append({
            "type": "success",
            "priority": "low",
            "title": "Buena racha de estudio",
            "message": f"Llevas {ov['current_streak']} días consecutivos estudiando. ¡Sigue así!",
        })

    # 5. Ease hell: many cards with a very low ease factor. FSRS doesn't use
    # the ease factor, so skip it when the collection uses FSRS.
    if cards_df is not None and "ease_factor_percent" in cards_df.columns:
        has_fsrs = any(
            pd.to_numeric(cards_df[col], errors="coerce").notna().any()
            for col in ("fsrs_stability", "fsrs_difficulty") if col in cards_df.columns
        )
        if not has_fsrs:
            ease = pd.to_numeric(cards_df["ease_factor_percent"], errors="coerce")
            ease_hell = cards_df[ease <= EASE_HELL]
            if len(ease_hell) > 10:
                recommendations.append({
                    "type": "alert",
                    "priority": prio("medium"),
                    "title": "Posible ease hell detectado",
                    "message": f"{len(ease_hell)} tarjetas tienen ease factor <= {EASE_HELL}%. Esto significa que los intervalos crecen muy lentamente, lo que puede ralentizar tu progreso.",
                    "action": "Revisa estas tarjetas y considera si necesitas más práctica con ellas o si deberían ser eliminadas.",
                })

    # 6. Review load check
    if ov["avg_reviews_per_active_day"] > 200:
        recommendations.append({
            "type": "info",
            "priority": prio("medium"),
            "title": "Carga de repasos alta",
            "message": f"Promedio de {ov['avg_reviews_per_active_day']:.0f} repasos por día activo. Si te resulta abrumador, reduce las nuevas.",
        })

    # 7. Time per review
    if ov["avg_time_per_review"] > 15:
        recommendations.append({
            "type": "info",
            "priority": "low",
            "title": "Tiempo de respuesta alto",
            "message": f"Promedio {ov['avg_time_per_review']:.1f}s por repaso. Si supera 15s, puede indicar que las tarjetas son complejas o que necesitas más práctica.",
        })

    # Sort by priority, then pin the data-sufficiency warning at the top so it
    # is never displaced by higher-priority items.
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 3))
    if insufficient:
        recommendations.insert(0, {
            "type": "info",
            "priority": "low",
            "title": "Datos aún escasos",
            "message": f"Datos aún escasos ({ov['active_days']} días activos, {ov['total_reviews']} repasos): las recomendaciones son orientativas.",
        })

    return recommendations
