"""Card types of the shared note type, used by deck building and the stats tools.

A card type's code says what the card shows and what it asks for: H (hanzi),
P (pinyin) and M (meaning), shown before the "2", asked after it.
"""

INFO_CARD_TYPE = "Toda la información"

# (code, folder under card_template/, description). The position in this list
# is the template's `ord` in Anki, which keys each card's review history: only
# ever APPEND new card types, never reorder or insert in the middle.
CARD_TYPES = [
    ("H2M", "h2m", "hanzi → significado"),
    ("M2H", "m2h", "significado → dibujar hanzi"),
    ("H2P", "h2p", "hanzi → escribir pinyin"),
    ("PM2H", "pm2h", "pinyin + significado → dibujar hanzi"),
    ("P2M", "p2m", "pinyin → significado"),
    ("M2P", "m2p", "significado → escribir pinyin"),
    ("P2H", "p2h", "pinyin → dibujar hanzi"),
    ("HM2P", "hm2p", "hanzi + significado → escribir pinyin"),
    ("HP2M", "hp2m", "hanzi + pinyin → significado"),
    (INFO_CARD_TYPE, "all_information", "consulta"),
]

CARD_TYPE_BY_ORD = {ord_: code for ord_, (code, _, _) in enumerate(CARD_TYPES)}
CARD_TYPE_DESCRIPTIONS = {code: description for code, _, description in CARD_TYPES}

# Template names used by earlier versions of the deck, before the codes.
LEGACY_TEMPLATE_NAMES = {
    "Hanzi -> Significado": "H2M",
    "Significado -> Hanzi": "M2H",
    "Hanzi -> Pinyin": "H2P",
    "Escribir Pinyin": "H2P",
    "Pinyin + Significado -> Hanzi": "PM2H",
    "Escribir Hanzi": "PM2H",
    "Pinyin -> Significado": "P2M",
    "Significado -> Pinyin": "M2P",
    "Pinyin -> Hanzi": "P2H",
    "Hanzi + Significado -> Pinyin": "HM2P",
    "Hanzi + Pinyin -> Significado": "HP2M",
    "PS2H": "PM2H",
    "P2S": "P2M",
    "S2P": "M2P",
    "HS2P": "HM2P",
    "HP2S": "HP2M",
    "Todas las tarjetas": INFO_CARD_TYPE,
    "All Information": INFO_CARD_TYPE,
}

# Template name (current code or legacy name) -> card type code.
CARD_NAME_TO_CODE = {code: code for code in CARD_TYPE_BY_ORD.values()} | LEGACY_TEMPLATE_NAMES
