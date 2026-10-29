"""Read-only access to an Anki collection: finding it, opening .colpkg
bundles, and resolving decks, card types and due dates from its schema."""
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from zhongwen_anki.card_types import CARD_NAME_TO_CODE, CARD_TYPE_BY_ORD

# (note type name, template name) -> code, for the "+" note type of earlier
# versions of the deck, whose "Pinyin -> Significado" template actually asked
# for the pinyin (verified against the template's front side). Takes
# precedence over CARD_NAME_TO_CODE.
TEMPLATE_OVERRIDES = {
    ("Chino - HSK (ES/EN)+", "Pinyin -> Significado"): "M2P",
}


def connect_readonly(collection_path: Path) -> sqlite3.Connection:
    """Open the collection read-only, so this tool never modifies it."""
    con = sqlite3.connect(f"file:{collection_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# --- Finding and opening the collection ------------------------------------


def default_collection_candidates() -> list[Path]:
    """Anki Desktop profiles on this system (Windows, macOS and Linux), plus a
    collection.anki2 or collection.colpkg in the current directory."""
    search_paths = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        search_paths.append(Path(appdata) / "Anki2")
    home = Path.home()
    search_paths.append(home / "Library" / "Application Support" / "Anki2")
    search_paths.append(home / ".local" / "share" / "Anki2")

    candidates = []
    for base in search_paths:
        if base.exists():
            candidates.extend(sorted(base.glob("*/collection.anki2")))
    for name in ("collection.anki2", "collection.colpkg"):
        local = Path(name)
        if local.exists() and local not in candidates:
            candidates.append(local)
    return candidates


def find_collection(explicit: Optional[Path]) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"Collection file not found: {explicit}")
        return explicit

    candidates = default_collection_candidates()
    if not candidates:
        raise SystemExit(
            "Could not auto-detect an Anki collection.\n"
            "Options:\n"
            "  1. Sync AnkiDroid → AnkiWeb → Anki Desktop, then re-run.\n"
            "  2. Export a .colpkg bundle from AnkiDroid and pass --collection <path>.\n"
            "  3. Pass --collection <path to collection.anki2 or .colpkg> explicitly."
        )
    if len(candidates) > 1:
        listing = "\n".join(f"  - {c}" for c in candidates)
        raise SystemExit(
            "Multiple Anki profiles found; pass --collection to pick one:\n" + listing
        )
    return candidates[0]


# Collection files inside a .colpkg, newest format first. anki21b is
# zstd-compressed; the others are plain SQLite.
_COLPKG_MEMBERS = (
    "collection.anki21b", "collection.anki21", "collection.anki2",
    "anki21b", "anki21", "anki2",
)


def extract_colpkg(colpkg_path: Path) -> Path:
    """Extract the SQLite collection from a .colpkg into a temporary directory.

    Returns the path of the extracted collection.anki2; the caller removes its
    parent directory when done.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="anki_colpkg_"))
    try:
        with zipfile.ZipFile(colpkg_path, "r") as zf:
            files = zf.namelist()
        chosen = next((name for name in _COLPKG_MEMBERS if name in files), None)
        if chosen is None:
            raise SystemExit(
                "No collection file found in .colpkg archive. "
                "Expected one of: collection.anki21b, collection.anki21, collection.anki2. "
                f"Found: {files}"
            )
        legacy_only = chosen.endswith("anki2")

        output_path = tmp_dir / "collection.anki2"
        with zipfile.ZipFile(colpkg_path, "r") as zf, zf.open(chosen) as src, \
                output_path.open("wb") as out:
            if chosen.endswith("anki21b"):
                try:
                    import zstandard as zstd
                except ImportError:
                    raise SystemExit(
                        "The zstandard package is required to open this .colpkg. "
                        "Install it with: pip install zstandard"
                    )
                with zstd.ZstdDecompressor().stream_reader(src) as reader:
                    shutil.copyfileobj(reader, out)
            else:
                shutil.copyfileobj(src, out)

        # Exports made with "include legacy-compatibility collection" may only
        # carry an empty anki2 placeholder.
        if legacy_only and _revlog_count(output_path) == 0:
            raise SystemExit(
                "The .colpkg only contains the empty legacy-compatibility collection. "
                "Re-export it from Anki/AnkiDroid without checking "
                "'include legacy-compatibility collection'."
            )
        return output_path
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _revlog_count(collection_path: Path) -> int:
    con = connect_readonly(collection_path)
    try:
        return con.execute("SELECT COUNT(*) FROM revlog").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def validate_collection(collection_path: Path, shown_path: Path) -> None:
    """Fail with a clear message unless *collection_path* is an Anki collection.

    *shown_path* is the path the user passed (it differs from
    *collection_path* when a .colpkg was extracted to a temp file).
    """
    try:
        con = connect_readonly(collection_path)
        try:
            con.execute("SELECT 1 FROM revlog LIMIT 1").fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        if "locked" in str(e):
            raise SystemExit(
                f"{shown_path} is locked by Anki. Close Anki (or switch to another "
                "profile) and try again."
            )
        raise SystemExit(
            f"{shown_path} is not a valid Anki collection "
            "(expected collection.anki2 or a .colpkg bundle)."
        )
    except sqlite3.DatabaseError:
        # Not SQLite at all, or SQLite without a revlog table
        # (OperationalError is a DatabaseError subclass).
        raise SystemExit(
            f"{shown_path} is not a valid Anki collection "
            "(expected collection.anki2 or a .colpkg bundle)."
        )


# --- Templates and card types ----------------------------------------------


def load_template_map(con: sqlite3.Connection) -> dict:
    """Return {ntid: {"name": str, "templates": {ord: template_name}}}.

    Uses the ``notetypes`` and ``templates`` tables of Anki 2.1.28+. Returns
    an empty dict for older collections, which then fall back to the
    positional CARD_TYPE_BY_ORD mapping.
    """
    template_map: dict = {}
    try:
        # Both tables use Anki's own "unicase" collation, which must exist to
        # query them; byte order is enough for reading.
        con.create_collation("unicase", lambda a, b: (a > b) - (a < b))
        notetype_names = {
            row["id"]: row["name"] for row in con.execute("SELECT id, name FROM notetypes")
        }
        for row in con.execute("SELECT ntid, ord, name FROM templates"):
            entry = template_map.setdefault(
                row["ntid"], {"name": notetype_names.get(row["ntid"], ""), "templates": {}}
            )
            entry["templates"][row["ord"]] = row["name"]
    except (sqlite3.OperationalError, sqlite3.ProgrammingError):
        return {}
    return template_map


def resolve_card_type(card_ord: int, note_ntid: int, template_map: dict) -> tuple[str, str, str]:
    """Return ``(card_type_code, template_name, notetype_name)`` for a card.

    The code comes from, in order: TEMPLATE_OVERRIDES, CARD_NAME_TO_CODE, the
    template name itself when it is not mapped, and CARD_TYPE_BY_ORD when the
    collection has no template information (the names are then empty).
    """
    nt_map = template_map.get(note_ntid)
    if nt_map is not None:
        notetype_name = nt_map.get("name", "")
        template_name = nt_map.get("templates", {}).get(card_ord)
        if template_name:
            code = (
                TEMPLATE_OVERRIDES.get((notetype_name, template_name))
                or CARD_NAME_TO_CODE.get(template_name)
                or template_name
            )
            return code, template_name, notetype_name
    return CARD_TYPE_BY_ORD.get(card_ord, f"(ord {card_ord})"), "", ""


def extract_qfmt(blob: bytes) -> str:
    """Read the front template (q_format, protobuf field 1) from templates.config."""
    if not blob or blob[0] != 0x0A:
        return ""
    length, shift, i = 0, 0, 1
    while i < len(blob):
        byte = blob[i]
        i += 1
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return blob[i:i + length].decode("utf-8", "replace")
        shift += 7
    return ""


# --- Decks, configuration and dates ----------------------------------------


def load_decks(con: sqlite3.Connection) -> dict[int, str]:
    """Return {deck_id: deck_name}, for both the modern and the legacy schema."""
    try:
        return {row["id"]: row["name"] for row in con.execute("SELECT id, name FROM decks")}
    except sqlite3.OperationalError:
        decks_raw = con.execute("SELECT decks FROM col").fetchone()["decks"]
        return {int(k): v["name"] for k, v in json.loads(decks_raw).items()}


def get_col_crt(con: sqlite3.Connection) -> float:
    """Collection creation time in epoch seconds (0 if unavailable)."""
    try:
        row = con.execute("SELECT crt FROM col").fetchone()
        if row and row["crt"]:
            return float(row["crt"])
    except sqlite3.OperationalError:
        pass
    return 0.0


def get_config_int(con: sqlite3.Connection, key: str) -> Optional[int]:
    """Read an integer from Anki's config table (one row per key; val is JSON)."""
    try:
        row = con.execute("SELECT val FROM config WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    try:
        return int(json.loads(row[0]))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def get_rollover_hour(con: sqlite3.Connection) -> int:
    """The hour at which Anki starts a new day (4 by default)."""
    value = get_config_int(con, "rollover")
    return 4 if value is None else value


def calculate_due_date(
    due: int, queue: int, card_type: int, odue: int,
    col_crt: float, is_filtered: bool, rollover_hour: int = 4,
    creation_offset_min: Optional[int] = None,
) -> str:
    """Due date of a card as YYYY-MM-DD, or "" when it has none.

    The meaning of ``cards.due`` depends on the queue:

    - 0 (new): position in the new queue, not a date.
    - 1 (learning) / 4 (preview): epoch seconds, converted in local time.
    - 2 (review) / 3 (relearning): day number since the collection was
      created, offset by *rollover_hour*. Anki counts it in the collection's
      creation timezone (``creationOffset``, minutes west of UTC); old
      collections without it use local time.
    - -1 (suspended): no due date.
    - -2 / -3 (buried): same as the underlying card type.

    Cards in a filtered deck (*is_filtered*) keep their real due in ``odue``;
    their ``due`` is just a position.
    """
    if queue in (-1, 0):
        return ""
    if is_filtered:
        if not odue:
            return ""
        raw = odue
    else:
        raw = due

    if raw > 1_000_000_000:
        return datetime.fromtimestamp(raw).strftime("%Y-%m-%d")

    if (card_type in (2, 3) or queue in (2, 3, -2, -3)) and col_crt > 0:
        if creation_offset_min is not None:
            tz = timezone(timedelta(minutes=-creation_offset_min))
            created = datetime.fromtimestamp(col_crt, tz)
        else:
            created = datetime.fromtimestamp(col_crt)
        day0 = (created - timedelta(hours=rollover_hour)).date()
        return (day0 + timedelta(days=raw)).strftime("%Y-%m-%d")
    return ""
