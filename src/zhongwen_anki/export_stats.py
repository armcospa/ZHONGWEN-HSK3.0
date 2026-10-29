"""Export Anki's review history (revlog) to CSV for external analysis.

Reads the local collection.anki2 (read-only, so it is safe even while Anki is
running) or a .colpkg bundle, and writes one row per review, joined with the
deck name, the note's first field (the Chinese word) and the card type code.

Usage:
    zhongwen-anki-export-stats
    zhongwen-anki-export-stats --deck "HSK1" -o docs/local/estadisticas/anki_reviews.csv
    zhongwen-anki-export-stats --collection "C:\\path\\to\\collection.colpkg"
    zhongwen-anki-export-stats --since 2026-08-01   # only recent reviews
    zhongwen-anki-export-stats --card-snapshot      # also export card state
    zhongwen-anki-export-stats --vocab-dir data     # enrich with TSV data

To use reviews made in AnkiDroid, either sync AnkiDroid -> AnkiWeb -> Anki
Desktop and export from the desktop collection, or export a .colpkg from
AnkiDroid (deck list -> menu -> Export collection) and pass it with
--collection.

Notes on the columns (kept close to Anki's schema):
    - `interval` / `last_interval`: positive = days, negative = seconds
      (Anki uses seconds for the short intervals of learning steps).
    - `ease_factor_percent`: 250 means the interval grows 2.5x on a correct
      review. Empty for new cards.
    - `is_user_answer`: the row is an answer (ease >= 1), not a system action
      such as a manual reschedule (review types 4 and 5).
    - `is_correct`: 1 unless the answer was "Again"; empty for system actions.
    - `tags` are the note's tags at export time, not at the time of the review.
    - `current_filtered_deck` is only filled if the card is still in a
      filtered deck at export time: the revlog doesn't record which deck a
      review happened in. `card_type` is the durable way to know which skill
      a review trained.
"""
import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from zhongwen_anki.anki_collection import (
    calculate_due_date,
    connect_readonly,
    extract_colpkg,
    extract_qfmt,
    find_collection,
    get_col_crt,
    get_config_int,
    get_rollover_hour,
    load_decks,
    load_template_map,
    resolve_card_type,
    validate_collection,
)

EASE_LABELS = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
REVIEW_TYPE_LABELS = {
    0: "learning",
    1: "review",
    2: "relearning",
    3: "cram/filtered",
    4: "manual",
    5: "rescheduled",
}
QUEUE_LABELS = {
    -3: "buried/user",
    -2: "buried/scheduler",
    -1: "suspended",
    0: "new",
    1: "learning",
    2: "review",
    3: "relearning",
    4: "preview",
}
TYPE_LABELS = {0: "new", 1: "learning", 2: "review", 3: "relearning"}

REVIEW_COLUMNS = [
    "review_id", "review_datetime", "deck", "current_filtered_deck", "note_id", "card_id",
    "word", "card_type", "notetype", "template_name", "tags", "note_guid",
    "ease", "ease_label", "is_user_answer", "is_correct",
    "interval", "last_interval", "ease_factor_percent",
    "time_seconds", "review_type",
]
VOCAB_COLUMNS = [
    "pinyin", "meaning", "meaning_es", "vocab_level", "traditional",
    "sentence", "sentence_meaning", "synonyms",
]
# Keys of load_vocab_data()'s entries, in VOCAB_COLUMNS order.
_VOCAB_KEYS = [
    "pinyin", "meaning", "meaning_es", "level", "traditional",
    "sentence", "sentence_meaning", "synonyms",
]
SNAPSHOT_COLUMNS = [
    "card_id", "note_id", "note_guid", "deck", "orig_deck",
    "word", "card_type", "notetype", "template_name", "tags",
    "queue", "queue_label", "type", "type_label",
    "due", "due_date", "interval", "ease_factor_percent", "lapses",
    "reps", "fsrs_stability", "fsrs_difficulty",
]


def load_vocab_data(vocab_dir: Path) -> dict:
    """Index every data/<level>/input.tsv by GUID.

    Returns {guid: {pinyin, meaning, meaning_es, level, ...}}. Files are read
    in sorted order, so if a GUID appears in two levels the higher one wins.
    """
    vocab = {}
    for tsv in sorted(vocab_dir.glob("*/input.tsv")):
        level = tsv.parent.name.upper()
        try:
            df = pd.read_csv(tsv, sep="\t", dtype=str).fillna("")
        except (OSError, pd.errors.ParserError) as e:
            print(f"Warning: skipping {tsv}: {e}", file=sys.stderr)
            continue
        for _, row in df.iterrows():
            guid = row.get("Guid", "").strip()
            if not guid:
                continue
            vocab[guid] = {
                "pinyin": row.get("Pinyin", ""),
                "meaning": row.get("Meaning", ""),
                "meaning_es": row.get("MeaningES", ""),
                "level": level,
                "traditional": row.get("Traditional", ""),
                "sentence": row.get("SentenceSimplified", ""),
                "sentence_meaning": row.get("SentenceMeaning", ""),
                "synonyms": row.get("Synonyms", ""),
            }
    return vocab


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in con.execute(f"PRAGMA table_info({table})"))


def _ease_factor_percent(factor: int):
    """Anki stores the ease factor in permille; 0 means the card is new."""
    return round(factor / 10, 1) if factor else ""


def _build_review_row(row, decks: dict, vocab: dict, deck_filter: Optional[str],
                      template_map: dict) -> Optional[list]:
    """CSV values for one revlog row, or None if its deck doesn't match *deck_filter*."""
    home_deck_id = row["orig_deck_id"] or row["deck_id"]
    deck_name = decks.get(home_deck_id, "")
    if deck_filter and deck_filter.lower() not in deck_name.lower():
        return None
    filtered_deck_name = decks.get(row["deck_id"], "") if row["orig_deck_id"] else ""
    card_type, template_name, notetype = resolve_card_type(
        row["card_ord"], row["note_ntid"], template_map
    )
    note_guid = row["note_guid"] or ""
    ease = row["ease"]
    review_type = row["review_type"]
    is_system_action = ease == 0 or review_type in (4, 5)

    values = [
        row["review_epoch_ms"],
        datetime.fromtimestamp(row["review_epoch_ms"] / 1000).isoformat(sep=" ", timespec="seconds"),
        deck_name,
        filtered_deck_name,
        row["note_id"],
        row["card_id"],
        row["flds"].split("\x1f")[0],
        card_type,
        notetype,
        template_name,
        row["tags"].strip(),
        note_guid,
        ease,
        EASE_LABELS.get(ease, ease),
        not is_system_action,
        "" if is_system_action else int(ease >= 2),
        row["interval"],
        row["last_interval"],
        _ease_factor_percent(row["factor"]),
        round(row["time_ms"] / 1000, 1),
        REVIEW_TYPE_LABELS.get(review_type, review_type),
    ]
    if vocab:
        entry = vocab.get(note_guid, {})
        values.extend(entry.get(key, "") for key in _VOCAB_KEYS)
    return values


def _read_existing_review_ids(output_path: Path, columns: list) -> set:
    """Review IDs already in *output_path*, checking that its header matches."""
    with output_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if header != columns:
            missing = [c for c in columns if c not in header]
            extra = [c for c in header if c not in columns]
            parts = []
            if missing:
                parts.append(f"missing=[{', '.join(missing)}]")
            if extra:
                parts.append(f"extra=[{', '.join(extra)}]")
            detail = " ".join(parts) or "column order differs"
            raise SystemExit(
                f"The header of {output_path} does not match the current export format"
                f" ({detail}).\n"
                "Repeat your original export command (without --append) to regenerate it,\n"
                "with the same options (e.g. --vocab-dir) as the original export."
            )
        return {r[0] for r in reader if r}


def export_reviews(
    collection_path: Path,
    output_path: Path,
    deck_filter: Optional[str] = None,
    since: Optional[str] = None,
    vocab_dir: Optional[Path] = None,
    append: bool = False,
) -> int:
    """Export the review history to CSV and return the number of rows written.

    *deck_filter* keeps decks whose name contains that text; *since*
    (YYYY-MM-DD, local time) keeps reviews from that day on; *vocab_dir* adds
    the vocabulary columns; *append* adds only the reviews that aren't
    already in an existing *output_path*.
    """
    vocab = load_vocab_data(vocab_dir) if vocab_dir is not None and vocab_dir.exists() else {}
    columns = REVIEW_COLUMNS + (VOCAB_COLUMNS if vocab else [])

    con = connect_readonly(collection_path)
    try:
        decks = load_decks(con)
        template_map = load_template_map(con)
        note_ntid = "notes.mid" if _has_column(con, "notes", "mid") else "0"
        query = f"""
            SELECT revlog.id AS review_epoch_ms, revlog.cid AS card_id, revlog.ease,
                   revlog.ivl AS interval, revlog.lastIvl AS last_interval,
                   revlog.factor AS factor, revlog.time AS time_ms, revlog.type AS review_type,
                   cards.did AS deck_id, cards.odid AS orig_deck_id, cards.nid AS note_id,
                   cards.ord AS card_ord, notes.flds AS flds, notes.tags AS tags,
                   notes.guid AS note_guid, {note_ntid} AS note_ntid
            FROM revlog
            JOIN cards ON cards.id = revlog.cid
            JOIN notes ON notes.id = cards.nid
        """
        params = {}
        if since:
            query += " WHERE revlog.id >= :since_ms"
            params["since_ms"] = int(datetime.strptime(since, "%Y-%m-%d").timestamp() * 1000)
        rows = con.execute(query + " ORDER BY revlog.id", params).fetchall()
    finally:
        con.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # An empty file (e.g. left by an interrupted export) has no header to
    # check against, so it is treated like a missing one: full export.
    if append and output_path.exists() and output_path.stat().st_size > 0:
        existing_ids = _read_existing_review_ids(output_path, columns)
        appended = already_present = filtered_out = 0
        with output_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in rows:
                if str(row["review_epoch_ms"]) in existing_ids:
                    already_present += 1
                    continue
                values = _build_review_row(row, decks, vocab, deck_filter, template_map)
                if values is None:
                    filtered_out += 1
                    continue
                writer.writerow(values)
                appended += 1
        print(
            f"Appended {appended} new reviews "
            f"({already_present} already present, {filtered_out} filtered out)"
        )
        return appended

    written = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            values = _build_review_row(row, decks, vocab, deck_filter, template_map)
            if values is not None:
                writer.writerow(values)
                written += 1
    label = f"since {since}" if since else "total"
    print(f"Wrote {written:,} reviews ({label}) -> {output_path}")
    return written


def _fsrs_state(raw_data: str) -> tuple:
    """(stability, difficulty) from the cards.data JSON, or empty strings."""
    if raw_data:
        try:
            data = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            return data.get("s", ""), data.get("d", "")
    return "", ""


def export_card_snapshot(
    collection_path: Path,
    output_path: Path,
    deck_filter: Optional[str] = None,
) -> int:
    """Export the current scheduling state of every card (interval, ease,
    lapses, queue, due date, FSRS memory state) and return the row count."""
    con = connect_readonly(collection_path)
    try:
        decks = load_decks(con)
        template_map = load_template_map(con)
        col_crt = get_col_crt(con)
        rollover = get_rollover_hour(con)
        creation_offset = get_config_int(con, "creationOffset")

        # Older Anki versions lack some of these columns.
        optional = {"odue": "0", "reps": "0", "data": "''"}
        optional_select = ", ".join(
            f"cards.{col}" if _has_column(con, "cards", col) else f"{default} AS {col}"
            for col, default in optional.items()
        )
        note_ntid = "notes.mid" if _has_column(con, "notes", "mid") else "0"
        rows = con.execute(f"""
            SELECT cards.id AS card_id, cards.nid AS note_id,
                   cards.did AS deck_id, cards.odid AS orig_deck_id, cards.ord AS card_ord,
                   cards.due, cards.ivl, cards.factor, cards.lapses, cards.queue, cards.type,
                   {optional_select},
                   notes.flds AS flds, notes.tags AS tags,
                   notes.guid AS note_guid, {note_ntid} AS note_ntid
            FROM cards
            JOIN notes ON notes.id = cards.nid
        """).fetchall()
    finally:
        con.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SNAPSHOT_COLUMNS)
        for row in rows:
            home_deck_id = row["orig_deck_id"] or row["deck_id"]
            deck_name = decks.get(home_deck_id, "")
            if deck_filter and deck_filter.lower() not in deck_name.lower():
                continue
            card_type, template_name, notetype = resolve_card_type(
                row["card_ord"], row["note_ntid"], template_map
            )
            queue, ctype = row["queue"], row["type"]
            due_date = calculate_due_date(
                due=row["due"],
                queue=queue,
                card_type=ctype,
                odue=row["odue"] or 0,
                col_crt=col_crt,
                is_filtered=bool(row["orig_deck_id"]),
                rollover_hour=rollover,
                creation_offset_min=creation_offset,
            )
            stability, difficulty = _fsrs_state(row["data"])
            writer.writerow([
                row["card_id"],
                row["note_id"],
                row["note_guid"] or "",
                deck_name,
                decks.get(row["orig_deck_id"], "") if row["orig_deck_id"] else "",
                row["flds"].split("\x1f")[0],
                card_type,
                notetype,
                template_name,
                row["tags"].strip(),
                queue,
                QUEUE_LABELS.get(queue, queue),
                ctype,
                TYPE_LABELS.get(ctype, ctype),
                row["due"],
                due_date,
                row["ivl"],
                _ease_factor_percent(row["factor"]),
                row["lapses"],
                row["reps"] or 0,
                stability,
                difficulty,
            ])
            written += 1
    print(f"Wrote {written:,} cards -> {output_path}")
    return written


# --- --list-templates ------------------------------------------------------


def _front_fields(qfmt: str, max_len: int = 60) -> str:
    """Unique field names {{...}} shown on the front, in order of appearance.

    HTML comments and scripts are skipped (commented-out links and the
    embedded HanziWriter data would otherwise flood the column).
    """
    visible = re.sub(r"<!--.*?-->|<script.*?</script>", "", qfmt, flags=re.S)
    fields = dict.fromkeys(re.findall(r"\{\{([^#^/!}][^}]*)\}\}", visible))
    text = ", ".join(fields) or "-"
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def list_templates(collection_path: Path, show_all: bool = False) -> None:
    """Print every template with the card type code it resolves to, to check
    how a collection's reviews will be labelled."""
    con = connect_readonly(collection_path)
    try:
        template_map = load_template_map(con)
        try:
            configs = {
                (row["ntid"], row["ord"]): row["config"]
                for row in con.execute("SELECT ntid, ord, config FROM templates")
            }
        except sqlite3.OperationalError:
            configs = {}
        try:
            review_counts = {
                (row["ntid"], row["ord"]): row["cnt"]
                for row in con.execute("""
                    SELECT notes.mid AS ntid, cards.ord AS ord, COUNT(*) AS cnt
                    FROM revlog
                    JOIN cards ON cards.id = revlog.cid
                    JOIN notes ON notes.id = cards.nid
                    GROUP BY notes.mid, cards.ord
                """)
            }
        except sqlite3.OperationalError:
            review_counts = {}
        try:
            # Built-in note types (Basic, Cloze...) usually have no notes.
            used_notetypes = {row["mid"] for row in con.execute("SELECT DISTINCT mid FROM notes")}
        except sqlite3.OperationalError:
            used_notetypes = set(template_map)
    finally:
        con.close()

    headers = ["notetype", "ntid", "ord", "template_name", "front_fields", "resolved_code", "n_reviews"]
    table = []
    for ntid in sorted(template_map):
        if not show_all and ntid not in used_notetypes:
            continue
        templates = template_map[ntid].get("templates", {})
        for ord_ in sorted(templates):
            code, _, _ = resolve_card_type(ord_, ntid, template_map)
            table.append([
                template_map[ntid].get("name", ""), str(ntid), str(ord_), templates[ord_],
                _front_fields(extract_qfmt(configs.get((ntid, ord_), b""))),
                code, str(review_counts.get((ntid, ord_), 0)),
            ])

    widths = [max(len(r[i]) for r in [headers] + table) + 2 for i in range(len(headers))]
    header_line = "".join(h.ljust(w) for h, w in zip(headers, widths))
    print(header_line)
    print("-" * len(header_line))
    for row in table:
        print("".join(v.ljust(w) for v, w in zip(row, widths)))


# --- CLI -------------------------------------------------------------------


def _validate_since(since_str: str) -> str:
    try:
        datetime.strptime(since_str, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: '{since_str}'. Expected YYYY-MM-DD (e.g. 2025-01-15)."
        )
    return since_str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Anki review history and card state to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  zhongwen-anki-export-stats                             # all reviews
  zhongwen-anki-export-stats --since 2026-08-01          # reviews since a date
  zhongwen-anki-export-stats --card-snapshot --vocab-dir data

AnkiDroid users: sync AnkiDroid -> AnkiWeb -> Anki Desktop and run this tool,
or export a .colpkg from AnkiDroid (deck list -> menu -> Export collection)
and pass it with --collection. Anki Desktop backups (*.colpkg) also work.
""",
    )
    parser.add_argument("--collection", type=Path, default=None,
                        help="Path to collection.anki2 or .colpkg (auto-detected if omitted).")
    parser.add_argument("-o", "--output", type=Path,
                        default=Path("docs/local/estadisticas/anki_reviews.csv"),
                        help="Output CSV for reviews (default: %(default)s).")
    parser.add_argument("--deck", type=str, default=None,
                        help="Only include reviews/cards from decks whose name contains this text.")
    parser.add_argument("--since", type=_validate_since, default=None,
                        help="Only export reviews on or after this date (YYYY-MM-DD, local time).")
    parser.add_argument("--append", action="store_true",
                        help="Append only new reviews to an existing CSV (requires matching header).")
    parser.add_argument("--vocab-dir", type=Path, default=None,
                        help="Directory with <level>/input.tsv files to enrich the export (e.g. 'data').")
    parser.add_argument("--card-snapshot", action="store_true",
                        help="Also export the current state of every card (interval, lapses, queue...).")
    parser.add_argument("--card-snapshot-output", type=Path, default=None,
                        help="Output path for the card snapshot (default: <output>.cards.csv).")
    parser.add_argument("--list-templates", action="store_true",
                        help="Print every template (notetype, ord, code, front fields) and exit.")
    parser.add_argument("--all", action="store_true", dest="show_all_templates",
                        help="With --list-templates: also show built-in note types (Basic, Cloze...).")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    shown_collection = find_collection(args.collection)
    collection = shown_collection

    extracted = None
    if collection.suffix == ".colpkg":
        try:
            extracted = extract_colpkg(collection)
        except (OSError, zipfile.BadZipFile) as e:
            raise SystemExit(f"Failed to extract .colpkg: {e}")
        collection = extracted

    try:
        validate_collection(collection, shown_collection)
        if args.list_templates:
            list_templates(collection, show_all=args.show_all_templates)
            return
        export_reviews(
            collection, args.output,
            deck_filter=args.deck,
            since=args.since,
            vocab_dir=args.vocab_dir,
            append=args.append,
        )
        if args.card_snapshot:
            snapshot_path = args.card_snapshot_output or args.output.with_stem(args.output.stem + ".cards")
            export_card_snapshot(collection, snapshot_path, deck_filter=args.deck)
    finally:
        if extracted is not None:
            shutil.rmtree(extracted.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
