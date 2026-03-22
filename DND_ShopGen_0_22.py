"""
D&D Shop Generator — DND_ShopGen_0v22.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import csv
import json
import math
import sqlite3
import random
import re
from datetime import datetime
from pathlib import Path

# ── Quantity generation ────────────────────────────────────────────────────────

_TGS_SOURCES = {"TGS1", "TGS2", "TGS3", "TGS4", "TGS5"}

_VEHICLE_NAME_FRAGMENTS = {
    "ship", "galley", "longship", "keelboat", "rowboat", "warship",
    "whaleboat", "carriage", "wagon", "cart", "sled", "dogsled", "chariot",
}

def _is_vehicle(name: str) -> bool:
    return any(frag in name.lower() for frag in _VEHICLE_NAME_FRAGMENTS)

def _is_generic_variant(item: dict) -> bool:
    return ("Generic Variant" in item.get("Tags", "")
            or "Generic Variant" in item.get("Type", ""))

_SIZE_MOD_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "Village":    {"mundane": (0, 5),  "common": (0, 2),  "uncommon": (0, 0),
                   "rare":    (0, 0),  "very rare": (0, 0), "legendary": (0, 0)},
    "Town":       {"mundane": (0, 10), "common": (0, 4),  "uncommon": (0, 2),
                   "rare":    (0, 0),  "very rare": (0, 0), "legendary": (0, 0)},
    "City":       {"mundane": (2, 15), "common": (1, 5),  "uncommon": (1, 4),
                   "rare":    (0, 3),  "very rare": (0, 1), "legendary": (0, 0)},
    "Metropolis": {"mundane": (5, 30), "common": (3, 15), "uncommon": (2, 6),
                   "rare":    (0, 5),  "very rare": (0, 1), "legendary": (0, 0)},
}

def _get_size_mod(city_size: str, rarity: str) -> float:
    rarity_key = rarity.lower().strip()
    if rarity_key in ("none", ""):
        rarity_key = "mundane"
    if rarity_key in ("artifact", "varies", "unknown"):
        return 0.0
    size_table = _SIZE_MOD_RANGES.get(city_size, _SIZE_MOD_RANGES["Town"])
    lo, hi = size_table.get(rarity_key, (0, 0))
    return random.uniform(lo, hi) if hi > 0 else 0.0

def _get_item_weight(item: dict, tags: set[str]) -> int:
    """Return stackability weight (0 = singular/always qty 1, up to 3 = stacks heavily).

    Checks the Quantity column first as a manual override, then infers from
    rarity, source, tags, and item properties.
    """
    _CONSUMABLE_TAGS = {"Potion", "Scroll", "Ammunition", "Oil", "Dust/Powder", "Food/Drink"}
    rarity = item.get("Rarity", "").strip().lower()
    source = item.get("Source", "").strip()
    name   = item.get("Name",   "").lower()
    text   = item.get("Text",   "").lower()

    col_val = item.get("Quantity", "")
    if col_val and str(col_val).strip().isdigit():
        return int(str(col_val).strip())

    if rarity in ("legendary", "artifact"):          return 0
    if rarity == "very rare":                         return 0
    if source in _TGS_SOURCES and rarity == "rare":  return 0
    if "sentient" in text:                            return 0
    if _is_generic_variant(item):                     return 0
    if _is_vehicle(name):                             return 0

    if tags & _CONSUMABLE_TAGS:
        if rarity in ("mundane", "none", "common"):  return 3
        if rarity == "uncommon":                     return 2
        return 1  # rare consumables still stack a little

    if rarity in ("mundane", "none"):  return 2
    if rarity == "common":             return 1
    if rarity == "uncommon":           return 1
    return 0

def generate_item_quantity(item: dict, city_size: str = "Town", wealth: str = "Average") -> int:
    """Qty = ceil((size_mod * weight) + 1), floored at 1.

    size_mod: random float from the city+rarity range table (0.0 when the
              rarity doesn't appear at that city size).
    weight:   stackability score 0-3 inferred from item properties.
    A weight of 0 always produces exactly 1 regardless of city size.
    """
    tags     = {t.strip() for t in item.get("Tags", "").split(",") if t.strip()}
    rarity   = item.get("Rarity", "").strip().lower()
    weight   = _get_item_weight(item, tags)
    size_mod = _get_size_mod(city_size, rarity)
    return max(1, math.ceil((size_mod * weight) + 1))

# ── Cultural tag filter ────────────────────────────────────────────────────────

# Every tag that marks an item as culturally specific.
# Items with NONE of these tags are Universal — they pass any culture filter.
# Items with one of these tags only pass when their culture is active.
CULTURAL_TAGS: set[str] = {
    "Draconic", "Drow", "Dwarven", "Elven", "Fey",
    "Fiendish", "Giant",
}

def culture_match(item: dict, active_culture: str | None) -> bool:
    """Return True if the item is compatible with the active culture filter.

    Rules:
      - No active culture  → everything passes.
      - Item has no cultural tag → Universal, always passes.
      - Item has a cultural tag  → passes only if it matches active_culture.
    """
    if not active_culture:
        return True
    item_tags = {t.strip() for t in item.get("Tags", "").split(",") if t.strip()}
    item_cultures = item_tags & CULTURAL_TAGS
    if not item_cultures:          # no cultural tag = Universal
        return True
    return active_culture in item_cultures


# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "shop_data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH   = DATA_DIR / "shops.db"

# Single master CSV — all items with Shop_Pools column
MASTER_CSV = BASE_DIR / "Items_Beta.csv"

SHOP_TYPE_TO_POOL = {
    "Alchemy":               "alchemy",
    "Armory":                "armory",
    "Blacksmith":            "blacksmith",
    "Fletcher & Bowyer":     "fletcher_bowyer",
    "General Store":         "general_store",
    "Jeweler & Curiosities": "jeweler",
    "Magic":                 "magic",
    "Scribe & Scroll":       "scribe_scroll",
    "Stables & Outfitter":   "stables",
    "Tavern & Inn":          "tavern",
}

# ── Rarity sort order ──────────────────────────────────────────────────────────
RARITY_ORDER = {
    "mundane": 0, "none": 0, "common": 1, "uncommon": 2, "rare": 3,
    "very rare": 4, "legendary": 5, "artifact": 6,
    "varies": 7, "unknown": 8, "unknown (magic)": 9,
}

# ── DM's Guide price ranges ────────────────────────────────────────────────────
RARITY_PRICE_RANGES = {
    "mundane":         (1,      50),
    "none":            (1,      50),
    "common":          (50,     100),
    "uncommon":        (101,    500),
    "rare":            (501,    5000),
    "very rare":       (5001,   50000),
    "legendary":       (50001,  500000),
    "artifact":        (100000, 1000000),
    "varies":          (10,     500),
    "unknown":         (10,     100),
    "unknown (magic)": (50,     500),
}

# ── City size → item count ranges ─────────────────────────────────────────────
CITY_SIZE_RANGES = {
    "Village":    (10, 15),
    "Town":       (15, 25),
    "City":       (25, 35),
    "Metropolis": (35, 60),
}

# ── Wealth → rarity distribution ──────────────────────────────────────────────
WEALTH_DEFAULTS = {
    "Poor":    {"common": 55, "uncommon": 30, "rare": 15, "very rare": 0,  "legendary": 0,  "artifact": 0},
    "Average": {"common": 40, "uncommon": 30, "rare": 24, "very rare": 5,  "legendary": 1,  "artifact": 0},
    "Rich":    {"common": 30, "uncommon": 25, "rare": 20, "very rare": 15, "legendary": 10, "artifact": 0},
}

# ── Generative shop name parts ────────────────────────────────────────────────
# Six patterns are assembled at random from these pools:
#   A) "The [Adj] [Noun]"       e.g. "The Bubbling Cauldron"
#   B) "[Name]'s [Noun]"        e.g. "Aldric's Elixirs"
#   C) "The [Noun] & [Noun2]"   e.g. "The Quill & Candle"
#   D) "[Adj] [Trade]"          e.g. "Ironblood Smithy"
#   E) "[Name]'s [Trade]"       e.g. "Gornak's Forge"
#   F) "[Noun] & [Noun2]"       e.g. "Shield & Sword"
SHOP_NAME_PARTS: dict[str, dict[str, list[str]]] = {
    "Alchemy": {
        "adjectives":   ["Bubbling", "Smoky", "Gilded", "Cobalt", "Silver", "Amber",
                         "Dripping", "Misty", "Fizzling", "Sputtering", "Boiling",
                         "Leaking", "Crimson", "Verdant", "Acrid", "Fuming"],
        "nouns":        ["Cauldron", "Crucible", "Retort", "Vial", "Flask", "Mortar",
                         "Phial", "Alembic", "Tincture", "Concoction", "Burner", "Still"],
        "second_nouns": ["Bottle", "Smoke", "Powder", "Fume", "Extract", "Ember",
                         "Flame", "Vapour", "Ash"],
        "trade_words":  ["Apothecary", "Formulae", "Mixtures", "Remedies",
                         "Concoctions", "Philtres", "Elixirs", "Potions", "Distillery"],
        "npc_names":    ["Mira", "Aldric", "Yzara", "Seraphel", "Fizzwick",
                         "Madame Voss", "Thornwick", "Brimstone", "Ember", "Cobalt",
                         "Sable", "Orwick", "Fenrath"],
    },
    "Armory": {
        "adjectives":   ["Iron", "Brazen", "Tempered", "Steel", "Unyielding", "Dented",
                         "Cold", "Sunken", "Wyrm-Scale", "Gilded", "Forged", "Battle-Worn"],
        "nouns":        ["Bastion", "Bulwark", "Pauldron", "Visor", "Curtain",
                         "Guard", "Shell", "Plate", "Greave", "Vambrace"],
        "second_nouns": ["Sword", "Shield", "Mail", "Rivet", "Helm", "Buckler", "Hauberk"],
        "trade_words":  ["Armory", "Armaments", "Harness", "Outfitters", "Arms", "Works"],
        "npc_names":    ["Velthurin", "Harkon", "Ironforge", "Dragonsteel", "Aegis",
                         "Crestfall", "Coldmere", "Rampart", "Valdris", "Morthane"],
    },
    "Blacksmith": {
        "adjectives":   ["Red", "White-Hot", "Sooty", "Bent", "Clanging", "Ashen",
                         "Deepfire", "Glowing", "Cracked", "Hammered", "Scorched"],
        "nouns":        ["Anvil", "Hammer", "Forge", "Hearth", "Trough",
                         "Nail", "Spark", "Slag", "Bellows", "Tong"],
        "second_nouns": ["Flame", "Steel", "Cinder", "Ember", "Ash", "Coal", "Iron", "Blade"],
        "trade_words":  ["Smithy", "Forge", "Ironworks", "Smithworks",
                         "Metalworks", "Foundry", "Works"],
        "npc_names":    ["Gornak", "Halverson", "Embric", "Bram", "Stonemaul",
                         "Ironblood", "Deepfire", "Ashfall", "Thunderstrike",
                         "Durnok", "Heldra", "Korrund"],
    },
    "Fletcher & Bowyer": {
        "adjectives":   ["Singing", "Fletched", "Taut", "Notched", "Straight",
                         "Loosed", "Drawn", "Swift", "Silent", "Keen"],
        "nouns":        ["Quiver", "Arrow", "Stave", "String", "Nock",
                         "Bolt", "Shaft", "Wing", "Bow", "Fletch"],
        "second_nouns": ["Reed", "Feather", "Yew", "Goose-Feather", "Birch",
                         "Sinew", "Ash", "Maple"],
        "trade_words":  ["Archery", "Bowyers", "Fletchers", "Bowworks",
                         "Quarrels", "Arrowcraft"],
        "npc_names":    ["Elara", "Mirethil", "Farryn", "Silvan", "Windwhisper",
                         "Thornfield", "Ashwood", "Bramblewood", "Pinecroft",
                         "Sylvara", "Hethrin"],
    },
    "General Store": {
        "adjectives":   ["Dusty", "Packed", "Cluttered", "Overstuffed", "Reliable",
                         "Common", "Wandering", "Worn", "Trusty", "Humble"],
        "nouns":        ["Counter", "Satchel", "Shelf", "Stall", "Purse",
                         "Post", "Barrel", "Crate", "Rack"],
        "second_nouns": ["Rope", "Pack", "Goods", "Finds", "Wares", "Odds", "Ends"],
        "trade_words":  ["Mercantile", "Provisions", "Supplies", "Emporium",
                         "Wares", "Depot", "Trading Post", "General"],
        "npc_names":    ["Halvard", "Millbrook", "Thorngate", "Greymarsh",
                         "Briarvale", "Cobblestone", "Dunmore", "Aldwick",
                         "Ferris", "Hadley"],
    },
    "Jeweler & Curiosities": {
        "adjectives":   ["Gilded", "Hidden", "Polished", "Glinting", "Shining",
                         "Whispering", "Lustrous", "Peculiar", "Gleaming", "Veiled"],
        "nouns":        ["Gem", "Jewel", "Stone", "Cabinet", "Hoard",
                         "Cache", "Trinket", "Find", "Vault", "Eye"],
        "second_nouns": ["Onyx", "Opal", "Varnish", "Luster", "Pearl",
                         "Sapphire", "Garnet", "Crystal", "Amber"],
        "trade_words":  ["Jewellers", "Gems", "Treasures", "Curios",
                         "Oddments", "Ornaments", "Antiquities"],
        "npc_names":    ["Tindra", "Aurelius", "Crystalveil", "Silverthread",
                         "Velvet", "Opalvane", "Lumen", "Gemwright",
                         "Sorra", "Nilvaris"],
    },
    "Magic": {
        "adjectives":   ["Arcane", "Enchanted", "Glowing", "Runic", "Umbral",
                         "Veilborn", "Starlit", "Bound", "Drifting", "Mystic",
                         "Wandering", "Whispering", "Sigil-Touched"],
        "nouns":        ["Attic", "Cache", "Shelf", "Grimoire", "Tome",
                         "Pocket", "Circle", "Vestibule", "Nook", "Alcove"],
        "second_nouns": ["Seal", "Sorcery", "Mist", "Veil", "Rune",
                         "Sigil", "Cantrip", "Aether", "Void"],
        "trade_words":  ["Magicka", "Arcana", "Enchantments", "Wares",
                         "Curios", "Magics", "Emporium", "Curiosities"],
        "npc_names":    ["Mystara", "Vexor", "Elara", "Mirrorgate", "Aethermist",
                         "Umbral", "Stardust", "Veilborn", "Zephyra",
                         "Mordecai", "Thessaly", "Ilvar"],
    },
    "Scribe & Scroll": {
        "adjectives":   ["Blotted", "Dusty", "Sealed", "Pressed", "Open",
                         "Careful", "Illumined", "Faded", "Inked", "Worn"],
        "nouns":        ["Quill", "Feather", "Folio", "Seal", "Hand",
                         "Letter", "Page", "Tome", "Scroll", "Script"],
        "second_nouns": ["Parchment", "Candle", "Vellum", "Ink", "Sigil",
                         "Wax", "Reed", "Ribbon", "Clasp"],
        "trade_words":  ["Scrivenery", "Transcripts", "Scrollworks",
                         "Scripts", "Manuscripts", "Calligraphy", "Bindery"],
        "npc_names":    ["Aldenmoor", "Thornwick", "Pencraft", "Reedham",
                         "Memoranda", "Vellum", "Inksworth", "Quillsby",
                         "Harrold", "Cressida"],
    },
    "Stables & Outfitter": {
        "adjectives":   ["Dusty", "Muddy", "Padded", "Tired", "Open",
                         "Stamping", "Cobbled", "Worn", "Weathered"],
        "nouns":        ["Hoof", "Saddle", "Shoe", "Paddock", "Yard",
                         "Spur", "Gate", "Stable", "Post"],
        "second_nouns": ["Tack", "Trail", "Feed", "Bridle", "Stirrup",
                         "Harness", "Rein", "Mane"],
        "trade_words":  ["Stables", "Livery", "Outfitters",
                         "Equestrian", "Mounts", "Feed & Tack"],
        "npc_names":    ["Ironmane", "Farrow", "Thunderhoof", "Crossroads",
                         "Briarvale", "Greystream", "Cloverfield", "Stonepath",
                         "Willowmere", "Crestfall", "Dusthoof", "Mirren"],
    },
    "Tavern & Inn": {
        "adjectives":   ["Rusty", "Golden", "Broken", "Tipsy", "Stumbling",
                         "Forgotten", "Half-Empty", "Laughing", "Sleeping",
                         "Salted", "Warm", "Last", "Crooked", "Drunken"],
        "nouns":        ["Flagon", "Goose", "Compass", "Cup", "Bard",
                         "Hearth", "Lantern", "Skull", "Dragon", "Boot",
                         "Griffon", "Boar", "Hound", "Raven"],
        "second_nouns": ["Moon", "Mead", "Wound", "Coin", "Candle",
                         "Ember", "Barrel", "Sword", "Pipe"],
        "trade_words":  ["Inn", "Tavern", "Rest", "Lodge",
                         "Alehouse", "Boarding House", "Roadhouse"],
        "npc_names":    ["Widow Harken", "Old Marten", "Crossroads",
                         "Hearthstone", "Dunwall", "Gretta", "Tobbin",
                         "Mirla", "Aldous", "Fenwick"],
    },
}

_NAME_PATTERNS = [
    "the_adj_noun",      # "The Bubbling Cauldron"
    "name_noun",         # "Aldric's Elixirs"
    "the_noun_and_noun", # "The Quill & Candle"
    "adj_trade",         # "Ironblood Smithy"
    "name_trade",        # "Gornak's Forge"
    "noun_and_noun",     # "Shield & Sword"
]

def generate_shop_name(shop_type: str) -> str:
    """Assemble a shop name from parts using a random structural pattern."""
    parts = SHOP_NAME_PARTS.get(shop_type)
    if not parts:
        return f"The {shop_type} Shop"

    pattern  = random.choice(_NAME_PATTERNS)
    adj      = random.choice(parts["adjectives"])
    noun     = random.choice(parts["nouns"])
    trade    = random.choice(parts["trade_words"])
    npc      = random.choice(parts["npc_names"])
    all_nouns = parts["nouns"] + parts["second_nouns"]
    n1       = random.choice(all_nouns)
    n2       = random.choice([n for n in all_nouns if n != n1] or all_nouns)

    if pattern == "the_adj_noun":
        return f"The {adj} {noun}"
    elif pattern == "name_noun":
        return f"{npc}'s {noun}"
    elif pattern == "the_noun_and_noun":
        return f"The {n1} & {n2}"
    elif pattern == "adj_trade":
        return f"{adj} {trade}"
    elif pattern == "name_trade":
        return f"{npc}'s {trade}"
    else:
        return f"{n1} & {n2}"


# ── Tag filter categories ──────────────────────────────────────────────────────
TAG_CATEGORIES = {
    "🧝 Race/Creature": [
        "Drow", "Draconic", "Dwarven", "Elven", "Fey", "Fiendish", "Giant",
    ],
    "🔥 Damage/Element": [
        "Acid", "Fire", "Force", "Ice/Cold", "Lightning", "Necrotic",
        "Poison", "Psychic", "Radiant", "Thunder", "Slashing", "Piercing", "Bludgeoning",
    ],
    "🎒 Item Slot/Form": [
        "Adventuring Gear", "Ammunition", "Artisans", "Tools", "Amulet/Necklace",
        "Belt", "Book/Tome", "Boots/Footwear", "Card/Deck", "Cloak",
        "Dust/Powder", "Figurine", "Food/Drink", "Gloves/Bracers", "Headwear",
        "Instrument", "Potion", "Ring", "Rod", "Scroll", "Staff", "Tattoo",
        "Wand", "Other", "Trade Good", "Spellcasting Focus",
    ],
    "⚔️ Weapon & Armor": [
        "Armor", "Finesse", "Generic Variant", "Heavy Armor", "Heavy Weapon",
        "Light Armor", "Light Weapon", "Medium Armor", "Melee", "Ranged Weapon",
        "Shield", "Thrown", "Two-Handed", "Versatile", "Weapon",
    ],
    "🎲 Rarity": [
        "Artifact", "Common", "Legendary", "Mundane", "Rare", "Uncommon", "Very Rare",
    ],
}

# ── Alternating row palette ────────────────────────────────────────────────────
ROW_ODD          = "#1e1e30"
ROW_EVEN         = "#171725"
ROW_LOCKED_ODD   = "#1e1e10"
ROW_LOCKED_EVEN  = "#17170d"
ROW_SELECTED     = "#2e2a14"


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def normalize_rarity(r: str) -> str:
    return (r or "").strip().lower()

def rarity_rank(r: str) -> int:
    return RARITY_ORDER.get(normalize_rarity(r), 99)

def parse_given_cost(value_str: str) -> float | None:
    if not value_str:
        return None
    s = value_str.upper().replace(",", "")
    m = re.search(r"([\d.]+)\s*(GP|SP|CP)", s)
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(2)
    if unit == "SP": amount /= 10
    elif unit == "CP": amount /= 100
    return round(amount, 2)

def weighted_rarity_pick(weights: dict[str, int]) -> str:
    pool: list[str] = []
    for rarity, pct in weights.items():
        pool.extend([rarity] * pct)
    while len(pool) < 100:
        pool.append("common")
    return random.choice(pool)

def format_currency(gp_value) -> str:
    """Convert a GP float to a multi-denomination display string.

    Internally everything is stored and calculated in GP (float).
    This converts to the smallest necessary denominations for display:
      1 gp = 10 sp = 100 cp

    Examples:
      15.0   → "15 gp"
      1.5    → "1 gp 5 sp"
      0.5    → "5 sp"
      0.07   → "7 cp"
      12.34  → "12 gp 3 sp 4 cp"
      0.0    → "—"
    """
    if gp_value is None or gp_value == "":
        return "—"
    try:
        total_cp = round(float(gp_value) * 100)
    except (TypeError, ValueError):
        return "—"
    if total_cp <= 0:
        return "—"
    gp = total_cp // 100
    sp = (total_cp % 100) // 10
    cp = total_cp % 10
    parts = []
    if gp: parts.append(f"{gp:,} gp")
    if sp: parts.append(f"{sp} sp")
    if cp: parts.append(f"{cp} cp")
    return " ".join(parts) if parts else "—"

def split_description_paragraphs(text: str) -> str:
    if not text or "\n" in text:
        return text

    # Pass 1: paragraph breaks at sentence-ending punctuation before a capital
    text = re.sub(r'(?<=[a-z])([.!?])(\)?)(?=[A-Z])', r'\1\2\n\n', text)

    # Pass 2: table cell/row breaks at every direct lowercase→uppercase boundary
    text = re.sub(r'(?<=[a-z])(?=[A-Z])', '\n', text)

    # Pass 3: numbered-list entry breaks — digit immediately before a capital,
    # preceded by a lowercase letter, but NOT if the two chars before the digit
    # form [0-9][dD] (which would indicate dice notation like "+1d4")
    text = re.sub(r'(?<=[a-z])(?<![0-9][dD])(\d+)(?=[A-Z])', r'\n\1', text)

    # Pass 4: digit→uppercase transitions not yet split (e.g. "30Rare", "+1d4Uncommon").
    # Only fires when the uppercase letter is followed by a lowercase letter so that
    # all-caps abbreviations such as "DC" are left alone.
    text = re.sub(r'(?<=[0-9])(?=[A-Z][a-z])', '\n', text)

    return text



# ── Rich description parser ────────────────────────────────────────────────────

def _is_valid_header(cell: str) -> bool:
    """Return False if a cell looks like a data value rather than a column header.

    Rejects:
      * Bonus-dice notation  e.g. "+1d4", "+3d6"   (data modifier cells)
      * Pure integers        e.g. "30", "75"        (numeric value cells)
      * Number + unit        e.g. "8 ounces", "4 gallons" (measurement cells)
    Allows:
      * Plain dice labels    e.g. "1d6", "d10"      (roll-table column headers)
      * Multi-word phrases   e.g. "Dragon Age", "Max. Amount"
      * Simple nouns         e.g. "Rarity", "Liquid"
    """
    if re.match(r'^\+\d+d\d', cell):
        return False
    if re.match(r'^\d+$', cell):
        return False
    if re.match(r'^\d+\s', cell):
        return False
    return True


def _split_table_cells(block: str) -> list:
    """Apply table-splitting passes to a raw block and return a flat list of cells.

    Passes applied in order (no Pass 1 — paragraph splits are already done):
      Pass 2: direct lowercase→uppercase         "AgeBonus"   → "Age\\nBonus"
      Pass 5: lowercase before +digit modifier   "ling+1d4"   → "ling\\n+1d4"
      Pass 3: lowercase + digits before uppercase "claw2A"     → "claw\\n2A"
              (excludes dice tails e.g. "1d" preceding the digit)
      Pass 4: digit before TitleCase             "4Uncommon"  → "4\\nUncommon"
      Pass 6: lowercase directly before digit+space "Acid8 oz"→ "Acid\\n8 oz"
      Pass 7: lowercase before digits at line/string end "Rare120" → "Rare\\n120"
              (excludes dice tails)
    """
    t = block
    t = re.sub(r'(?<=[a-z])(?=[A-Z])',                     '\n',    t)  # 2
    t = re.sub(r'(?<=[a-z])(?=\+\d)',                      '\n',    t)  # 5
    t = re.sub(r'(?<=[a-z])(?<![0-9][dD])(\d+)(?=[A-Z])', r'\n\1', t)  # 3
    t = re.sub(r'(?<=[0-9])(?=[A-Z][a-z])',                '\n',    t)  # 4
    t = re.sub(r'(?<=[a-z])(?=\d+ )',                      '\n',    t)  # 6
    t = re.sub(r'(?<=[a-z])(?<![0-9]d)(\d+)(?=\n|$)',     r'\n\1', t)  # 7
    return [c.strip() for c in t.split('\n') if c.strip()]


def _try_parse_table_block(cells: list) -> list:
    """Given a flat cell list, return a list of table-segment dicts.

    Each dict: {"type": "table", "headers": [...], "rows": [[...], ...]}
    with an optional "title" key for a spanning header row.

    Strategy (tried in order):
      1. Single table  — smallest C in [2,3,4] that evenly divides n with ≥2 rows
                         and valid column headers.
      2. Two adjacent tables — scan split points, require valid headers on both sides.
      3. Title stripping — if n-1 divides evenly and the first cell passes as a title
                           (does not start with a digit), strip it and retry.
      4. Fallback — two-column layout.
    """
    n = len(cells)
    if n < 4:
        return [{"type": "table", "headers": cells[:1], "rows": [cells[1:]]}]

    # ── 1. Single table ──
    for c in range(2, 5):
        if n % c == 0 and n // c >= 2:
            hdrs = cells[:c]
            if all(_is_valid_header(h) for h in hdrs):
                return [{"type": "table",
                         "headers": hdrs,
                         "rows": [cells[i:i+c] for i in range(c, n, c)]}]

    # ── 2. Two adjacent tables ──
    for split in range(4, n - 3):
        left, right = cells[:split], cells[split:]
        for c1 in range(2, 5):
            if split % c1 != 0 or split // c1 < 2:
                continue
            if not all(_is_valid_header(left[i]) for i in range(c1)):
                continue
            for c2 in range(2, 5):
                if len(right) % c2 != 0 or len(right) // c2 < 2:
                    continue
                if not all(_is_valid_header(right[i]) for i in range(c2)):
                    continue
                return [
                    {"type": "table",
                     "headers": left[:c1],
                     "rows": [left[i:i+c1] for i in range(c1, split, c1)]},
                    {"type": "table",
                     "headers": right[:c2],
                     "rows": [right[i:i+c2] for i in range(c2, len(right), c2)]},
                ]

    # ── 3. Title stripping ──
    if not re.match(r'^\d', cells[0]):
        trimmed = cells[1:]
        m = len(trimmed)
        for c in range(2, 5):
            if m % c == 0 and m // c >= 2:
                hdrs = trimmed[:c]
                if all(_is_valid_header(h) for h in hdrs):
                    return [{"type": "table",
                             "title": cells[0],
                             "headers": hdrs,
                             "rows": [trimmed[i:i+c] for i in range(c, m, c)]}]

    # ── 4. Fallback: two-column ──
    return [{"type": "table",
             "headers": cells[:2],
             "rows": [cells[i:i+2] for i in range(2, n - 1, 2)]}]


def parse_description_rich(text: str) -> list:
    if not text:
        return []
    if '\n' in text:
        return [{"type": "prose", "text": text}]

    # Pass 1 — paragraph breaks at sentence-end before uppercase
    paragraphs = re.sub(
        r'(?<=[a-z])([.!?])(\)?)(?=[A-Z])', r'\1\2\n\n', text
    ).split('\n\n')

    segments: list = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Table fingerprint: direct lowercase→uppercase or digit→TitleCase boundary
        if re.search(r'[a-z][A-Z]', para) or re.search(r'[0-9][A-Z][a-z]', para):
            cells = _split_table_cells(para)
            if len(cells) >= 4:
                segments.extend(_try_parse_table_block(cells))
                continue
        segments.append({"type": "prose", "text": para})

    return segments



def apply_price_mod(cost_str: str, mod: int) -> str:
    if mod == 100 or not cost_str or cost_str == "—":
        return cost_str or "—"
    val = parse_given_cost(cost_str)
    if val is None:
        return cost_str
    modified = max(0.01, val * mod / 100)   # floor at 1 cp (0.01 gp)
    return format_currency(modified)


# ══════════════════════════════════════════════════════════════════════════════
#  Database
# ══════════════════════════════════════════════════════════════════════════════
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS towns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            city_size TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            town_id INTEGER REFERENCES towns(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            shop_type TEXT,
            wealth TEXT,
            last_restocked TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER REFERENCES shops(id) ON DELETE CASCADE,
            item_id TEXT,
            name TEXT,
            rarity TEXT,
            item_type TEXT,
            source TEXT,
            page TEXT,
            cost_given TEXT,
            quantity TEXT,
            locked INTEGER DEFAULT 0,
            attunement TEXT,
            damage TEXT,
            properties TEXT,
            mastery TEXT,
            weight TEXT,
            tags TEXT,
            description TEXT
        );
    """)
    con.commit()

    try:
        con.execute("ALTER TABLE shops ADD COLUMN notes TEXT DEFAULT ''")
        con.commit()
    except sqlite3.OperationalError:
        pass
    con.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Item Loading
# ══════════════════════════════════════════════════════════════════════════════
ALL_ITEMS: dict[str, list[dict]] = {}   # pool_key → [item, ...]
ALL_ITEMS_FLAT: list[dict] = []         # all items for sell lookup

def load_all_items():
    global ALL_ITEMS, ALL_ITEMS_FLAT
    if not MASTER_CSV.exists():
        print(f"[ERROR] Master CSV not found: {MASTER_CSV}")
        return

    # Build pool buckets from Shop_Pools column
    pool_buckets: dict[str, list[dict]] = {
        pool_key: [] for pool_key in SHOP_TYPE_TO_POOL.values()
    }
    all_flat: list[dict] = []

    with open(MASTER_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k: (v.strip() if isinstance(v, str) else "") for k, v in row.items()}
            all_flat.append(row)
            pools = [p.strip() for p in row.get("Pools", "").split("|") if p.strip()]
            for pool_key in pools:
                if pool_key in pool_buckets:
                    pool_buckets[pool_key].append(row)

    # Map pool keys back to display names for ALL_ITEMS
    pool_to_display = {v: k for k, v in SHOP_TYPE_TO_POOL.items()}
    for pool_key, items in pool_buckets.items():
        display_name = pool_to_display.get(pool_key, pool_key)
        ALL_ITEMS[display_name] = items

    ALL_ITEMS_FLAT.extend(all_flat)
    print(f"[INFO] Loaded {len(all_flat)} items from master CSV.")
    for display, items in sorted(ALL_ITEMS.items(), key=lambda x: -len(x[1])):
        print(f"         {display}: {len(items)} items")


# ══════════════════════════════════════════════════════════════════════════════
#  Shop Generation
# ══════════════════════════════════════════════════════════════════════════════
def generate_shop_items(
    shop_type: str,
    count: int,
    rarity_weights: dict[str, int],
    existing_locked: list[dict] | None = None,
    tag_filters: set[str] | None = None,
    tag_excludes: set[str] | None = None,
    city_size: str = "Town",
    wealth: str = "Average",
    culture: str | None = None,
) -> list[dict]:
    if shop_type not in ALL_ITEMS or not ALL_ITEMS[shop_type]:
        return []

    source_items = ALL_ITEMS[shop_type]
    buckets: dict[str, list[dict]] = {}
    for item in source_items:
        r = normalize_rarity(item.get("Rarity", "mundane"))
        buckets.setdefault(r, []).append(item)

    locked_items   = existing_locked or []
    locked_names   = {i["name"] for i in locked_items}
    needed         = count - len(locked_items)
    if needed <= 0:
        return locked_items

    generated      = list(locked_items)
    existing_names = set(locked_names)
    attempts       = 0
    fallback_order = ["mundane", "common", "uncommon", "rare", "none", "very rare", "legendary"]

    def tag_match(item: dict) -> bool:
        """Exclude beats include. Excluded tags hard-block; include filters
        then require at least one match (OR logic). No filters = allow all.
        Culture filter: items without any cultural tag are Universal (always
        pass); items with a cultural tag only pass if it matches active culture."""
        item_tags = {t.strip() for t in item.get("Tags", "").split(",") if t.strip()}
        if tag_excludes and (item_tags & tag_excludes):
            return False
        if tag_filters and not (item_tags & tag_filters):
            return False
        if not culture_match(item, culture):
            return False
        return True

    while len(generated) - len(locked_items) < needed and attempts < needed * 20:
        attempts += 1
        rarity = weighted_rarity_pick(rarity_weights)
        chosen_item = None
        for r in [rarity] + [x for x in fallback_order if x != rarity]:
            bucket    = buckets.get(r, [])
            available = [x for x in bucket
                         if x["Name"] not in existing_names and tag_match(x)]
            if available:
                chosen_item = random.choice(available)
                break
        if not chosen_item:
            continue

        cost_given = chosen_item.get("Value", "")
        quantity   = str(generate_item_quantity(chosen_item, city_size, wealth))

        generated.append({
            "item_id":    chosen_item.get("Item ID", ""),
            "name":       chosen_item.get("Name", ""),
            "rarity":     chosen_item.get("Rarity", ""),
            "item_type":  chosen_item.get("Type", ""),
            "source":     chosen_item.get("Source", ""),
            "page":       chosen_item.get("Page", ""),
            "cost_given": cost_given,
            "quantity":   quantity,
            "locked":     False,
            "attunement": chosen_item.get("Attunement", ""),
            "damage":     chosen_item.get("Damage", ""),
            "properties": chosen_item.get("Properties", ""),
            "mastery":    chosen_item.get("Mastery", ""),
            "weight":     chosen_item.get("Weight", ""),
            "tags":       chosen_item.get("Tags", ""),
            "description": chosen_item.get("Text", ""),
        })
        existing_names.add(chosen_item["Name"])

    return generated


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════
class ShopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("D&D Shop Generator")
        self.geometry("1380x860")
        self.minsize(1100, 700)
        self.configure(bg="#1a1a2e")
        self._apply_theme()

        # ── State ─────────────────────────────────────────────────────────────
        self.current_items: list[dict] = []
        self.current_shop_type = tk.StringVar(value="Magic")
        self.city_size_var     = tk.StringVar(value="Town")
        self.wealth_var        = tk.StringVar(value="Average")
        self.culture_var       = tk.StringVar(value="")   # "" = no culture filter
        self.shop_name_var     = tk.StringVar(value="")
        self.selected_row      = None
        self._sort_col         = "rarity"
        self._sort_asc         = True
        self.price_modifier    = tk.IntVar(value=100)
        self._inspect_expanded = False   # whether inspector is in focus mode

        # Rarity slider vars
        self.rarity_sliders: dict[str, tk.IntVar] = {
            r: tk.IntVar(value=v)
            for r, v in WEALTH_DEFAULTS["Average"].items()
        }

    
        self.active_tag_filters:   set[str] = set()
        self.excluded_tag_filters: set[str] = set()
        self._tag_state_vars:      dict[str, tk.IntVar] = {}


        self.sell_search_var    = tk.StringVar()
        self.sell_pct_var       = tk.IntVar(value=80)
        self.sell_selected_item = None   # dict with item data + buy_price
        self._sell_popup        = None   # Toplevel dropdown
        self.shop_notes_widget  = None   # Text widget for shop notes (set in _build_save_tab)

        init_db()
        load_all_items()
        self._build_ui()
        self._refresh_campaign_list()

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        bg, fg, accent, sel = "#1a1a2e", "#e0d8c0", "#c9a84c", "#2d2d4e"
        hdr = "#0f0f1e"

        style.configure(".",           background=bg, foreground=fg, font=("Georgia", 10))
        style.configure("TNotebook",   background=hdr, borderwidth=0)
        style.configure("TNotebook.Tab", background=sel, foreground=fg,
                        padding=[14, 6], font=("Georgia", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", accent)],
                  foreground=[("selected", hdr)])
        style.configure("TFrame",  background=bg)
        style.configure("TLabel",  background=bg, foreground=fg)
        style.configure("TButton", background=accent, foreground=hdr,
                        font=("Georgia", 10, "bold"), padding=6, relief="flat")
        style.map("TButton", background=[("active", "#e6c06a")])
        style.configure("Danger.TButton", background="#8b0000", foreground="#e0d8c0")
        style.map("Danger.TButton", background=[("active", "#b22222")])
        style.configure("Treeview",
                        background=ROW_ODD, foreground=fg,
                        fieldbackground=ROW_ODD, rowheight=26,
                        font=("Consolas", 9))
        style.configure("Treeview.Heading",
                        background=hdr, foreground=accent,
                        font=("Georgia", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", accent)],
                  foreground=[("selected", hdr)])
        style.configure("TCombobox", fieldbackground=sel, background=sel, foreground=fg)
        style.configure("TScale",  background=bg, troughcolor=sel)
        style.configure("TEntry",  fieldbackground=sel, foreground=fg, insertcolor=fg)
        style.configure("TSeparator", background=accent)
        self.colors = {"bg": bg, "fg": fg, "accent": accent, "sel": sel, "hdr": hdr}

    # ── Main UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        c = self.colors

        # ── Top bar ──
        top = tk.Frame(self, bg=c["hdr"], pady=6)
        top.pack(fill="x")

        tk.Label(top, text="⚔  D&D Shop Generator",
                 font=("Georgia", 15, "bold"),
                 bg=c["hdr"], fg=c["accent"]).pack(side="left", padx=14)

        tk.Label(top, text="Shop:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 10)).pack(side="left", padx=(16, 4))
        shop_combo = ttk.Combobox(top, textvariable=self.current_shop_type,
                                  values=list(SHOP_TYPE_TO_POOL.keys()), width=22,
                                  state="readonly")
        shop_combo.pack(side="left", padx=(0, 10))
        shop_combo.bind("<<ComboboxSelected>>", self._on_shop_type_change)

        tk.Label(top, text="Name:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 10)).pack(side="left", padx=(0, 4))
        tk.Entry(top, textvariable=self.shop_name_var, width=26,
                 bg=c["sel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Georgia", 10)).pack(side="left", padx=(0, 8))

        ttk.Button(top, text="🎯 Name",
                   command=self._random_name).pack(side="left", padx=(0, 16))

        tk.Label(top, text="Culture:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 10)).pack(side="left", padx=(0, 4))
        culture_opts = ["(None)"] + sorted(CULTURAL_TAGS)
        culture_combo = ttk.Combobox(top, textvariable=self.culture_var,
                                     values=culture_opts, width=12,
                                     state="readonly")
        culture_combo.pack(side="left", padx=(0, 10))
        culture_combo.set("(None)")

        # ── Notebook ───────────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.tab_action   = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        self.tab_sell     = ttk.Frame(nb)
        self.tab_save     = ttk.Frame(nb)
        self.tab_gallery  = ttk.Frame(nb)
        nb.add(self.tab_action,   text="  ⚔ Action  ")
        nb.add(self.tab_settings, text="  ⚙ Stock Settings  ")
        nb.add(self.tab_sell,     text="  💰 Sell Item  ")
        nb.add(self.tab_save,     text="  💾 Campaigns & Saves  ")
        nb.add(self.tab_gallery,  text="  📚 Item Gallery  ")

        self._build_action_tab()
        self._build_settings_tab()
        self._build_sell_tab()
        self._build_save_tab()
        self._build_gallery_tab()

    # ── Sell Tab ──────────────────────────────────────────────────────────────
    def _build_sell_tab(self):
        c = self.colors
        f = self.tab_sell

        # ── Left: search + results list ───────────────────────────────────────
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        # Header
        tk.Label(left, text="💰  Sell Item Lookup",
                 font=("Georgia", 12, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", pady=(0, 8))

        # Search bar
        search_row = tk.Frame(left, bg=c["bg"])
        search_row.pack(fill="x", pady=(0, 6))
        tk.Label(search_row, text="🔍 Search:", bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 10)).pack(side="left", padx=(0, 6))
        self.sell_entry = tk.Entry(search_row, textvariable=self.sell_search_var,
                                   width=36, bg=c["sel"], fg=c["fg"],
                                   insertbackground=c["fg"], relief="flat",
                                   font=("Georgia", 10))
        self.sell_entry.pack(side="left")
        self.sell_search_var.trace_add("write", self._on_sell_search)

        # Results listbox area
        tk.Label(left, text="Results  (click to select):",
                 bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 8, "italic")).pack(anchor="w", pady=(4, 2))

        results_frame = tk.Frame(left, bg=c["bg"])
        results_frame.pack(fill="both", expand=True)

        self.sell_results_tree = ttk.Treeview(
            results_frame,
            columns=("name", "rarity", "type", "buy_price"),
            show="headings",
            selectmode="browse",
            height=18,
        )
        self.sell_results_tree.heading("name",      text="Name")
        self.sell_results_tree.heading("rarity",    text="Rarity")
        self.sell_results_tree.heading("type",      text="Type")
        self.sell_results_tree.heading("buy_price", text="Buy Price")
        self.sell_results_tree.column("name",      width=280, anchor="w")
        self.sell_results_tree.column("rarity",    width=90,  anchor="center")
        self.sell_results_tree.column("type",      width=180, anchor="w")
        self.sell_results_tree.column("buy_price", width=110, anchor="center")

        # Rarity foreground tags
        RARITY_COLORS = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        for rarity, color in RARITY_COLORS.items():
            self.sell_results_tree.tag_configure(
                rarity.replace(" ", "_"), foreground=color)
        # alternating rows
        self.sell_results_tree.tag_configure("odd",  background=ROW_ODD)
        self.sell_results_tree.tag_configure("even", background=ROW_EVEN)

        vsb = ttk.Scrollbar(results_frame, orient="vertical",
                            command=self.sell_results_tree.yview)
        self.sell_results_tree.configure(yscrollcommand=vsb.set)
        self.sell_results_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.sell_results_tree.bind("<<TreeviewSelect>>", self._on_sell_result_select)

        # ── Right: pricing panel (scrollable) ──
        right = tk.Frame(f, bg=c["hdr"], width=320)
        right.pack(side="right", fill="y", padx=(0, 10), pady=10)
        right.pack_propagate(False)

        tk.Label(right, text="🪙  Sell Price",
                 font=("Georgia", 12, "bold"),
                 bg=c["hdr"], fg=c["accent"]).pack(pady=(14, 4))
        ttk.Separator(right, orient="horizontal").pack(fill="x", padx=10)

        # Scrollable canvas for the sell panel contents
        sell_canvas = tk.Canvas(right, bg=c["hdr"], highlightthickness=0)
        sell_vsb    = ttk.Scrollbar(right, orient="vertical",
                                    command=sell_canvas.yview)
        sell_canvas.configure(yscrollcommand=sell_vsb.set)
        sell_vsb.pack(side="right", fill="y")
        sell_canvas.pack(side="left", fill="both", expand=True)

        self.sell_panel = tk.Frame(sell_canvas, bg=c["hdr"])
        self._sell_panel_window = sell_canvas.create_window(
            (0, 0), window=self.sell_panel, anchor="nw")

        def _on_sell_panel_configure(event):
            sell_canvas.configure(scrollregion=sell_canvas.bbox("all"))
            sell_canvas.itemconfig(self._sell_panel_window,
                                   width=sell_canvas.winfo_width())

        self.sell_panel.bind("<Configure>", _on_sell_panel_configure)
        sell_canvas.bind("<Configure>",
                         lambda e: sell_canvas.itemconfig(
                             self._sell_panel_window, width=e.width))

        # Mouse-wheel scroll on the sell panel
        def _sell_scroll(event):
            sell_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        sell_canvas.bind_all("<MouseWheel>", _sell_scroll)

        self._draw_sell_panel_empty()

    def _draw_sell_panel_empty(self):
        for w in self.sell_panel.winfo_children():
            w.destroy()
        tk.Label(self.sell_panel,
                 text="Search and select an item\nto calculate a sell price.",
                 bg=self.colors["hdr"], fg=self.colors["fg"],
                 font=("Georgia", 9, "italic"),
                 justify="center").pack(pady=30)

    def _draw_sell_panel(self, item: dict, buy_price: int):
        c = self.colors
        for w in self.sell_panel.winfo_children():
            w.destroy()

        RARITY_COLORS = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        rcolor = RARITY_COLORS.get(normalize_rarity(item.get("Rarity", "")), c["fg"])

        # Item name
        tk.Label(self.sell_panel, text=item.get("Name", ""),
                 bg=c["hdr"], fg=rcolor,
                 font=("Georgia", 11, "bold"),
                 wraplength=260, justify="left").pack(anchor="w", pady=(0, 4))

        ttk.Separator(self.sell_panel).pack(fill="x", pady=4)

        def info_row(label, val):
            if not val: return
            row = tk.Frame(self.sell_panel, bg=c["hdr"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 8, "bold"), width=12,
                     anchor="w").pack(side="left")
            tk.Label(row, text=val, bg=c["hdr"], fg=c["fg"],
                     font=("Consolas", 9),
                     anchor="w").pack(side="left")

        info_row("Rarity",    item.get("Rarity", ""))
        info_row("Type",      item.get("Type", ""))
        info_row("Source",    item.get("Source", ""))
        info_row("Attunement", item.get("Attunement", ""))
        info_row("Damage",    item.get("Damage", ""))
        info_row("Weight",    item.get("Weight", ""))
        info_row("List Price", item.get("Value", "") or "—")
        info_row("Buy Price", format_currency(buy_price) if buy_price else item.get("Value", "") or "—")

        ttk.Separator(self.sell_panel).pack(fill="x", pady=10)

        # Sell % slider
        tk.Label(self.sell_panel, text="Shop's Buy Cut:",
                 bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9, "bold")).pack(anchor="w", pady=(0, 4))

        slider_row = tk.Frame(self.sell_panel, bg=c["hdr"])
        slider_row.pack(fill="x", pady=(0, 4))

        self.sell_pct_var.set(80)
        ttk.Scale(slider_row, from_=10, to=100,
                  variable=self.sell_pct_var, orient="horizontal",
                  length=180, command=self._on_sell_slider
                  ).pack(side="left")

        self.sell_pct_disp = tk.Label(slider_row, text="80%",
                                       bg=c["hdr"], fg=c["accent"],
                                       font=("Consolas", 10, "bold"), width=5)
        self.sell_pct_disp.pack(side="left", padx=6)

        ttk.Separator(self.sell_panel).pack(fill="x", pady=6)

        # Offer price (big display)
        tk.Label(self.sell_panel, text="Offer to Seller:",
                 bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9)).pack(anchor="w")
        self.sell_offer_disp = tk.Label(self.sell_panel, text="",
                                         bg=c["hdr"], fg="#1eff00",
                                         font=("Georgia", 16, "bold"))
        self.sell_offer_disp.pack(anchor="w", pady=(2, 0))

        self._update_sell_offer()

        # Description
        desc = item.get("Text", "")
        if desc:
            desc = split_description_paragraphs(desc)
            ttk.Separator(self.sell_panel).pack(fill="x", pady=(10, 6))
            tk.Label(self.sell_panel, text="DESCRIPTION",
                     bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 8, "bold"), anchor="w").pack(fill="x")
            tk.Label(self.sell_panel, text=desc,
                     bg=c["hdr"], fg=c["fg"],
                     font=("Consolas", 8),
                     wraplength=280, justify="left",
                     anchor="w").pack(fill="x", pady=(4, 0))

    def _on_sell_search(self, *_):
        q = self.sell_search_var.get().strip().lower()
        # Clear results tree
        if not hasattr(self, "sell_results_tree"):
            return
        self.sell_results_tree.delete(*self.sell_results_tree.get_children())

        if len(q) < 2:
            return

        matches = [i for i in ALL_ITEMS_FLAT
                   if q in i.get("Name", "").lower()][:80]

        RARITY_COLORS = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        for row_idx, item in enumerate(matches):
            calc_p_val = parse_given_cost(item.get("Value", ""))
            calc_p  = calc_p_val if calc_p_val else 0.0   # keep as float — sub-GP values matter
            rnorm   = normalize_rarity(item.get("Rarity", ""))
            r_tag   = rnorm.replace(" ", "_")
            parity  = "odd" if row_idx % 2 == 0 else "even"
            self.sell_results_tree.insert("", "end",
                values=(
                    item.get("Name", ""),
                    item.get("Rarity", "—"),
                    item.get("Type", "—"),
                    format_currency(calc_p),
                ),
                tags=(parity, r_tag),
                iid=f"sell_{row_idx}",
            )
            # store item+price on the iid for retrieval
            self.sell_results_tree.set(f"sell_{row_idx}", "buy_price",
                                        format_currency(calc_p))
            # stash raw data as hidden attribute via tag trick
            self._sell_result_data = getattr(self, "_sell_result_data", {})
            self._sell_result_data[f"sell_{row_idx}"] = (item, calc_p)

    def _on_sell_result_select(self, _=None):
        sel = self.sell_results_tree.selection()
        if not sel:
            return
        iid = sel[0]
        data = getattr(self, "_sell_result_data", {})
        if iid not in data:
            return
        item, buy_price = data[iid]
        self.sell_selected_item = {"item": item, "buy_price": buy_price}
        self._draw_sell_panel(item, buy_price)

    def _on_sell_slider(self, _=None):
        pct = int(float(self.sell_pct_var.get()))
        self.sell_pct_var.set(pct)
        if hasattr(self, "sell_pct_disp"):
            self.sell_pct_disp.configure(text=f"{pct}%")
        self._update_sell_offer()

    def _update_sell_offer(self):
        if not self.sell_selected_item:
            return
        pct   = int(self.sell_pct_var.get())
        buy_p = self.sell_selected_item["buy_price"]
        offer = max(0.01, float(buy_p) * pct / 100)
        if hasattr(self, "sell_offer_disp"):
            self.sell_offer_disp.configure(text=format_currency(offer))

    # ── Action Tab ────────────────────────────────────────────────────────────
    def _build_action_tab(self):
        c = self.colors
        f = self.tab_action

        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True)

        # Button bar
        btn_bar = tk.Frame(left, bg=c["hdr"], pady=6)
        btn_bar.pack(fill="x")
        ttk.Button(btn_bar, text="🎲  Generate Shop",
                   command=self._run_generate).pack(side="left", padx=6)
        ttk.Button(btn_bar, text="🔄  Reroll (10–30%)",
                   command=self._reroll).pack(side="left", padx=6)
        ttk.Button(btn_bar, text="🗑  Clear Shop",
                   style="Danger.TButton",
                   command=self._clear).pack(side="left", padx=6)

        # ── Discount / Markup slider ──
        tk.Frame(btn_bar, bg=c["sel"], width=2, height=26).pack(
            side="left", padx=(10, 8))

        tk.Label(btn_bar, text="💲 Price Adjust:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9)).pack(side="left")

        self.price_mod_slider = ttk.Scale(
            btn_bar, from_=50, to=125,
            variable=self.price_modifier,
            orient="horizontal", length=130,
            command=self._on_price_modifier)
        self.price_mod_slider.pack(side="left", padx=(4, 2))

        self.price_mod_label = tk.Label(
            btn_bar, text="100%", width=5,
            bg=c["hdr"], fg=c["accent"],
            font=("Consolas", 9, "bold"))
        self.price_mod_label.pack(side="left", padx=(0, 4))

        ttk.Button(btn_bar, text="↺",
                   command=self._reset_price_modifier).pack(side="left", padx=(0, 6))

        # Search bar
        tk.Label(btn_bar, text="🔍", bg=c["hdr"], fg=c["fg"]).pack(side="right", padx=(0, 2))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._populate_table(self.current_items))
        tk.Entry(btn_bar, textvariable=self.search_var, width=22,
                 bg=c["sel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Consolas", 9)).pack(side="right", padx=(0, 8))
        tk.Label(btn_bar, text="Filter:", bg=c["hdr"], fg=c["fg"]).pack(side="right")

        # Treeview
        cols   = ("name", "rarity", "cost", "quantity", "locked")
        hdrs   = ("Name", "Rarity", "Cost", "Qty", "Locked")
        widths = (310, 100, 130, 70, 60)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        self.tree = ttk.Treeview(tree_frame, columns=cols,
                                  show="headings", selectmode="browse")

        for col, hdr, w in zip(cols, hdrs, widths):
            self.tree.heading(col, text=hdr,
                              command=lambda c=col: self._on_sort(c))
            self.tree.column(col, width=w,
                             anchor="w" if col == "name" else "center")

        # Row tags
        self.tree.tag_configure("odd",          background=ROW_ODD)
        self.tree.tag_configure("even",         background=ROW_EVEN)
        self.tree.tag_configure("locked_odd",   background=ROW_LOCKED_ODD)
        self.tree.tag_configure("locked_even",  background=ROW_LOCKED_EVEN)
        self.tree.tag_configure("selected_row", background=ROW_SELECTED)

        RARITY_FG = {
            "mundane":   "#c8c8c8",
            "none":      "#c8c8c8",
            "common":    "#c8c8c8",
            "uncommon":  "#1eff00",
            "rare":      "#0070dd",
            "very rare": "#a335ee",
            "legendary": "#ff8000",
            "artifact":  "#d4af37",
        }
        for rarity, color in RARITY_FG.items():
            tag = rarity.replace(" ", "_")
            self.tree.tag_configure(tag, foreground=color)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>",         self._on_double_click)

        # Status bar
        self.status_var = tk.StringVar(value="No shop generated.")
        tk.Label(left, textvariable=self.status_var,
                 bg=c["hdr"], fg=c["accent"],
                 font=("Georgia", 9), anchor="w").pack(fill="x", padx=6, pady=2)

        # ── Inspector panel ──────────────────────────────────────────────────

        self._action_left = left
        self._inspect_width_collapsed = 310
        self._inspect_width_expanded  = None

        self.inspect_panel = tk.Frame(f, bg=c["hdr"])
        # Initial placement: right-anchored, full height, 310px wide
        self.inspect_panel.place(relx=1.0, rely=0.0,
                                  anchor="ne",
                                  width=self._inspect_width_collapsed,
                                  relheight=1.0)

        left.pack_configure(padx=(0, self._inspect_width_collapsed + 6))

        # ── Inspector header row (title + expand button) ──
        hdr_row = tk.Frame(self.inspect_panel, bg=c["hdr"])
        hdr_row.pack(fill="x", padx=8, pady=(10, 0))

        tk.Label(hdr_row, text="📖  Item Inspector",
                 font=("Georgia", 11, "bold"),
                 bg=c["hdr"], fg=c["accent"]).pack(side="left")

        self.expand_btn = tk.Label(
            hdr_row, text="⤢", font=("Georgia", 13),
            bg=c["hdr"], fg=c["fg"], cursor="hand2", padx=4)
        self.expand_btn.pack(side="right")
        self.expand_btn.bind("<Button-1>", lambda e: self._toggle_inspect_expand())
        self.expand_btn.bind("<Enter>",
            lambda e: self.expand_btn.configure(fg=c["accent"]))
        self.expand_btn.bind("<Leave>",
            lambda e: self.expand_btn.configure(fg=c["fg"]))

        ttk.Separator(self.inspect_panel, orient="horizontal").pack(
            fill="x", padx=8, pady=(4, 0))

        # Scrollable inner area
        inspect_canvas = tk.Canvas(self.inspect_panel, bg=c["hdr"],
                                    highlightthickness=0)
        inspect_vsb = ttk.Scrollbar(self.inspect_panel, orient="vertical",
                                     command=inspect_canvas.yview)
        inspect_canvas.configure(yscrollcommand=inspect_vsb.set)
        inspect_vsb.pack(side="right", fill="y")
        inspect_canvas.pack(side="left", fill="both", expand=True)

        self.inspect_frame = tk.Frame(inspect_canvas, bg=c["hdr"])
        self._inspect_canvas_win = inspect_canvas.create_window(
            (0, 0), window=self.inspect_frame, anchor="nw")

        def _on_inspect_frame_configure(event):
            inspect_canvas.configure(scrollregion=inspect_canvas.bbox("all"))

        def _on_inspect_canvas_configure(event):
            inspect_canvas.itemconfig(
                self._inspect_canvas_win, width=event.width)

        self.inspect_frame.bind("<Configure>", _on_inspect_frame_configure)
        inspect_canvas.bind("<Configure>", _on_inspect_canvas_configure)

        def _scroll_inspect(event):
            inspect_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        inspect_canvas.bind("<MouseWheel>", _scroll_inspect)
        self.inspect_frame.bind("<MouseWheel>", _scroll_inspect)

        self._clear_inspect()

    # ── Inspector expand / collapse ───────────────────────────────────────────
    def _toggle_inspect_expand(self):
        expanding = not self._inspect_expanded

        if self._inspect_width_expanded is None or expanding:
            self.update_idletasks()
            win_w = self.winfo_width()
            self._inspect_width_expanded = max(600, int(win_w * 0.56))

        w = (self._inspect_width_expanded if expanding
             else self._inspect_width_collapsed)

        self.inspect_panel.place_configure(width=w)
        self._action_left.pack_configure(padx=(0, w + 6))
        self._inspect_expanded = expanding
        self.expand_btn.configure(text="⤡" if expanding else "⤢")
        if self.selected_row:
            self._show_inspect(self.selected_row)

    # ── Sort ──────────────────────────────────────────────────────────────────
    def _on_sort(self, col: str):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._populate_table(self.current_items)

    def _sorted_items(self, items: list[dict]) -> list[dict]:
        col = self._sort_col
        asc = self._sort_asc

        if not col or col == "rarity":
            return sorted(
                items,
                key=lambda i: (rarity_rank(i.get("rarity", "")),
                               i.get("name", "").lower()),
                reverse=not asc,
            )

        def key(item):
            if col == "name":
                return item.get("name", "").lower()
            if col == "cost":
                return parse_given_cost(item.get("cost_given", "")) or 0
            if col == "quantity":
                try: return int(item.get("quantity", "1") or "1")
                except: return 1
            if col == "locked":
                return int(item.get("locked", False))
            return str(item.get(col, "")).lower()

        return sorted(items, key=key, reverse=not asc)

    # ── Table ─────────────────────────────────────────────────────────────────
    def _populate_table(self, items: list[dict]):
        q = self.search_var.get().lower() if hasattr(self, "search_var") else ""
        self.tree.delete(*self.tree.get_children())

        visible = [i for i in items
                   if not q
                   or q in i["name"].lower()
                   or q in (i.get("rarity") or "").lower()
                   or q in (i.get("item_type") or "").lower()]

        visible = self._sorted_items(visible)

        for row_idx, item in enumerate(visible):
            is_locked  = item.get("locked", False)
            parity     = "odd" if row_idx % 2 == 0 else "even"
            bg_tag     = f"locked_{parity}" if is_locked else parity
            rarity_tag = normalize_rarity(item.get("rarity", "none")).replace(" ", "_")
            tags       = (bg_tag, rarity_tag)

            mod       = self.price_modifier.get()
            cost_disp = apply_price_mod(item.get("cost_given", ""), mod)
            qty_disp  = item.get("quantity", "1") or "1"
            lock_sym  = "🔒" if is_locked else "☐"
            self.tree.insert("", "end",
                values=(
                    item["name"],
                    item.get("rarity", ""),
                    cost_disp,
                    qty_disp,
                    lock_sym,
                ),
                tags=tags,
                iid=item["name"])

    # ── Inspector ─────────────────────────────────────────────────────────────
    def _clear_inspect(self):
        for w in self.inspect_frame.winfo_children():
            w.destroy()
        tk.Label(self.inspect_frame, text="Select an item to inspect.",
                 bg=self.colors["hdr"], fg=self.colors["fg"],
                 font=("Georgia", 9, "italic")).pack(pady=20)

    def _show_inspect(self, item: dict):
        for w in self.inspect_frame.winfo_children():
            w.destroy()
        if self._inspect_expanded:
            self._render_inspect_expanded(item)
        else:
            self._render_inspect_collapsed(item)

    # ── Collapsed layout (original compact view) ──────────────────────────────
    # ── Description rich renderer ─────────────────────────────────────────────
    def _make_table_frame(self, parent, headers: list, rows: list,
                          title: str = "") -> tk.Frame:

        c       = self.colors
        n_cols  = max(len(headers), 1)
        TTL_BG  = "#111128"
        HDR_BG  = "#1c1c3a"
        HDR_FG  = c["accent"]
        ROW_A   = "#1a1a30"
        ROW_B   = "#1e1e38"
        SEP_CLR = "#2a2a50"

        outer = tk.Frame(parent, bg=SEP_CLR, bd=0, relief="flat")

        # Optional title row (spans all columns)
        if title:
            ttl_row = tk.Frame(outer, bg=TTL_BG)
            ttl_row.pack(fill="x", pady=(0, 1))
            tk.Label(
                ttl_row, text=title,
                bg=TTL_BG, fg=HDR_FG,
                font=("Georgia", 9, "bold italic"),
                padx=8, pady=4, anchor="w",
            ).pack(fill="x")

        # Header row
        hdr_row = tk.Frame(outer, bg=HDR_BG)
        hdr_row.pack(fill="x", pady=(0, 1))
        for col_idx, header in enumerate(headers):
            tk.Label(
                hdr_row, text=header,
                bg=HDR_BG, fg=HDR_FG,
                font=("Consolas", 8, "bold"),
                padx=8, pady=4, anchor="w",
            ).grid(row=0, column=col_idx, sticky="ew", padx=(0, 1))
            hdr_row.columnconfigure(col_idx, weight=1, minsize=60)

        # Data rows
        for row_idx, row in enumerate(rows):
            bg = ROW_A if row_idx % 2 == 0 else ROW_B
            row_frame = tk.Frame(outer, bg=bg)
            row_frame.pack(fill="x", pady=(0, 1))
            for col_idx in range(n_cols):
                cell = row[col_idx] if col_idx < len(row) else ""
                tk.Label(
                    row_frame, text=cell,
                    bg=bg, fg=c["fg"],
                    font=("Consolas", 8),
                    padx=8, pady=3, anchor="w",
                ).grid(row=0, column=col_idx, sticky="ew", padx=(0, 1))
                row_frame.columnconfigure(col_idx, weight=1, minsize=60)

        return outer

    def _render_description_rich(self, txt: tk.Text, raw_text: str) -> int:
        segments  = parse_description_rich(raw_text)
        est_lines = 0
        first     = True

        for seg in segments:
            if not first:
                txt.insert("end", "\n\n")
                est_lines += 2
            first = False

            if seg["type"] == "prose":
                txt.insert("end", seg["text"])
                est_lines += max(1, len(seg["text"]) // 34) + seg["text"].count("\n")

            elif seg["type"] == "table":
                frame = self._make_table_frame(
                    txt, seg["headers"], seg["rows"],
                    title=seg.get("title", ""),
                )
                txt.window_create("end", window=frame, padx=2, pady=4)
                est_lines += len(seg["rows"]) + 3   # header + rows + breathing room

        return est_lines

    def _render_inspect_collapsed(self, item: dict):
        c = self.colors
        RARITY_FG = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        rcolor = RARITY_FG.get(normalize_rarity(item.get("rarity", "")), c["fg"])
        wrap   = 270

        tk.Label(self.inspect_frame, text=item["name"],
                 bg=c["hdr"], fg=rcolor,
                 font=("Georgia", 11, "bold"),
                 wraplength=wrap, justify="left").pack(fill="x", pady=(0, 4))
        ttk.Separator(self.inspect_frame).pack(fill="x")

        # Reroll button (only for shop items, not gallery view)
        if not item.get("_gallery"):
            btn = ttk.Button(self.inspect_frame, text="🎲 Reroll This Item",
                             command=lambda i=item: self._reroll_single_item(i))
            btn.pack(anchor="w", pady=(6, 2))

        def field(label, val, multiline=False):
            if not val:
                return
            tk.Label(self.inspect_frame, text=label,
                     bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 8, "bold"), anchor="w").pack(fill="x", pady=(4, 0))
            if multiline:
                avg = 34
                wrapped = sum(max(1, -(-len(ln) // avg))
                              for ln in val.split("\n"))
                height = max(3, min(wrapped + val.count("\n"), 40))
                txt = tk.Text(self.inspect_frame, bg=c["sel"], fg=c["fg"],
                              wrap="word", height=height, relief="flat",
                              font=("Consolas", 8), padx=4, pady=4)
                txt.insert("1.0", val)
                txt.configure(state="disabled")
                txt.pack(fill="x")
            else:
                tk.Label(self.inspect_frame, text=val,
                         bg=c["hdr"], fg=c["fg"],
                         font=("Consolas", 9), anchor="w",
                         wraplength=wrap, justify="left").pack(fill="x")

        src = item.get("source", "")
        pg  = item.get("page", "")
        field("Item ID",    item.get("item_id"))
        field("Type",       item.get("item_type"))
        field("Rarity",     item.get("rarity"))
        field("Source",     f"{src} p.{pg}" if pg else src)
        field("Attunement", item.get("attunement"))
        field("Damage",     item.get("damage"))
        field("Properties", item.get("properties"))
        field("Mastery",    item.get("mastery"))
        field("Weight",     item.get("weight"))
        field("Tags",       item.get("tags"))

        field("Cost",     item.get("cost_given"))
        field("Quantity",  item.get("quantity"))

        if item.get("description"):
            tk.Label(self.inspect_frame, text="Description",
                     bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 8, "bold"), anchor="w").pack(fill="x", pady=(4, 0))
            txt = tk.Text(self.inspect_frame, bg=c["sel"], fg=c["fg"],
                          wrap="word", relief="flat",
                          font=("Consolas", 8), padx=4, pady=4)
            est = self._render_description_rich(txt, item["description"])
            txt.configure(height=max(3, min(est, 40)), state="disabled")
            txt.pack(fill="x")

    # ── Expanded layout (spacious, readable) ─────────────────────────────────
    def _render_inspect_expanded(self, item: dict):
        c   = self.colors
        pad = 16
        RARITY_FG = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        rarity = item.get("rarity", "")
        rcolor = RARITY_FG.get(normalize_rarity(rarity), c["fg"])

        # ── Title ──
        title_frame = tk.Frame(self.inspect_frame, bg=c["hdr"])
        title_frame.pack(fill="x", padx=pad, pady=(12, 0))

        tk.Label(title_frame, text=item["name"],
                 bg=c["hdr"], fg=rcolor,
                 font=("Georgia", 17, "bold"),
                 wraplength=480, justify="left").pack(anchor="w")

        sub_parts = [p for p in [rarity.title(), item.get("item_type", "")] if p]
        if sub_parts:
            tk.Label(title_frame, text="  ·  ".join(sub_parts),
                     bg=c["hdr"], fg=c["fg"],
                     font=("Georgia", 10, "italic")).pack(anchor="w", pady=(3, 0))

        # Reroll button (only for shop items, not gallery view)
        if not item.get("_gallery"):
            ttk.Button(title_frame, text="🎲  Reroll This Item",
                       command=lambda i=item: self._reroll_single_item(i)
                       ).pack(anchor="w", pady=(8, 0))

        ttk.Separator(self.inspect_frame).pack(fill="x", padx=pad, pady=10)

        # ── Stats — single column, generous sizing ──
        src = item.get("source", "")
        pg  = item.get("page", "")

        stats = [
            ("Item ID",     item.get("item_id", "")),
            ("Type",        item.get("item_type", "")),
            ("Rarity",      rarity.title()),
            ("Source",      f"{src} p.{pg}" if pg else src),
            ("Attunement",  item.get("attunement", "")),
            ("Damage",      item.get("damage", "")),
            ("Properties",  item.get("properties", "")),
            ("Mastery",     item.get("mastery", "")),
            ("Weight",      item.get("weight", "")),
            ("Tags",        item.get("tags", "")),
            ("Cost",        item.get("cost_given", "")),
            ("Quantity",    item.get("quantity", "")),
        ]
        stats = [(lbl, val) for lbl, val in stats if val]

        stats_frame = tk.Frame(self.inspect_frame, bg=c["hdr"])
        stats_frame.pack(fill="x", padx=pad)

        for lbl, val in stats:
            row = tk.Frame(stats_frame, bg=c["hdr"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=lbl,
                     bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 9, "bold"),
                     width=11, anchor="w").pack(side="left", padx=(0, 10))
            tk.Label(row, text=val,
                     bg=c["hdr"], fg=c["fg"],
                     font=("Georgia", 10),
                     wraplength=380, justify="left",
                     anchor="w").pack(side="left", fill="x", expand=True)

        # ── Description ──
        desc = item.get("description", "")
        if desc:
            ttk.Separator(self.inspect_frame).pack(
                fill="x", padx=pad, pady=(12, 8))

            tk.Label(self.inspect_frame, text="DESCRIPTION",
                     bg=c["hdr"], fg=c["accent"],
                     font=("Georgia", 10, "bold"),
                     anchor="w").pack(fill="x", padx=pad, pady=(0, 6))

            desc_frame = tk.Frame(self.inspect_frame, bg="#16162a",
                                  highlightbackground=c["border"] if "border" in c else "#2a2a4a",
                                  highlightthickness=1)
            desc_frame.pack(fill="x", padx=pad, pady=(0, 14))

            desc_txt = tk.Text(desc_frame,
                               bg="#16162a", fg="#d8d0b8",
                               wrap="word", relief="flat",
                               font=("Georgia", 11),
                               padx=14, pady=12,
                               spacing1=4, spacing2=2, spacing3=4,
                               height=10,
                               cursor="arrow")
            desc_vsb = ttk.Scrollbar(desc_frame, orient="vertical",
                                     command=desc_txt.yview)
            desc_txt.configure(yscrollcommand=desc_vsb.set)
            desc_vsb.pack(side="right", fill="y")
            desc_txt.pack(side="left", fill="both", expand=True)
            self._render_description_rich(desc_txt, desc)
            desc_txt.configure(state="disabled")

    # ── Settings Tab ──────────────────────────────────────────────────────────
    def _build_settings_tab(self):
        c = self.colors
        f = self.tab_settings
        outer = ttk.Frame(f)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        left_col = ttk.Frame(outer)
        left_col.pack(side="left", fill="y", padx=(0, 30))

        tk.Label(left_col, text="🏙  City Size",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", pady=(0, 6))
        for size, (lo, hi) in CITY_SIZE_RANGES.items():
            tk.Radiobutton(left_col,
                           text=f"{size}  ({lo}–{hi} items)",
                           variable=self.city_size_var, value=size,
                           bg=c["bg"], fg=c["fg"], selectcolor=c["sel"],
                           activebackground=c["bg"], activeforeground=c["accent"],
                           font=("Georgia", 10)).pack(anchor="w", pady=2)

        ttk.Separator(left_col, orient="horizontal").pack(fill="x", pady=10)

        tk.Label(left_col, text="💰  Wealth Level",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", pady=(0, 6))
        for wealth in WEALTH_DEFAULTS:
            tk.Radiobutton(left_col, text=wealth,
                           variable=self.wealth_var, value=wealth,
                           command=self._on_wealth_change,
                           bg=c["bg"], fg=c["fg"], selectcolor=c["sel"],
                           activebackground=c["bg"], activeforeground=c["accent"],
                           font=("Georgia", 10)).pack(anchor="w", pady=2)

        # Rarity sliders
        right_col = ttk.Frame(outer)
        right_col.pack(side="left", fill="y", padx=(0, 20))

        tk.Label(right_col, text="🎲  Rarity Distribution (%)",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", pady=(0, 4))
        tk.Label(right_col,
                 text="Adjust sliders to override wealth presets. Total should equal 100%.",
                 bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 8, "italic")).pack(anchor="w", pady=(0, 8))

        RARITY_COLORS = {
            "common": "#c8c8c8", "uncommon": "#1eff00",
            "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        self.slider_labels: dict[str, tk.StringVar] = {}

        for rarity in ["common", "uncommon", "rare", "very rare", "legendary", "artifact"]:
            row = tk.Frame(right_col, bg=c["bg"])
            row.pack(fill="x", pady=4)
            color = RARITY_COLORS.get(rarity, c["fg"])
            tk.Label(row, text=rarity.title(), width=12, anchor="w",
                     bg=c["bg"], fg=color,
                     font=("Georgia", 10)).pack(side="left")

            lbl_var = tk.StringVar(value=f"{self.rarity_sliders[rarity].get():>3}%")
            self.slider_labels[rarity] = lbl_var

            ttk.Scale(row, from_=0, to=100,
                      variable=self.rarity_sliders[rarity],
                      orient="horizontal", length=260,
                      command=lambda v, r=rarity: self._on_slider(r, v)
                      ).pack(side="left", padx=8)

            tk.Label(row, textvariable=lbl_var, width=5,
                     bg=c["bg"], fg=color,
                     font=("Consolas", 10)).pack(side="left")

        self.total_pct_var = tk.StringVar(value="Total: 100%")
        self.total_pct_label = tk.Label(right_col, textvariable=self.total_pct_var,
                 bg=c["bg"], fg=c["accent"],
                 font=("Georgia", 10, "bold"))
        self.total_pct_label.pack(anchor="w", pady=(8, 4))

        ttk.Button(right_col, text="↺  Reset Distribution",
                   command=self._reset_distribution).pack(anchor="w")

        # Tag filter panel — third column, takes remaining space
        tag_col = ttk.Frame(outer)
        tag_col.pack(side="left", fill="both", expand=True)
        self._build_tag_filter(tag_col)

    # ── Tag filter UI ────────────────────────────────────────────────────────
    def _build_tag_filter(self, parent: tk.Frame):
        """Build the collapsible tag category sections inside parent."""
        c = self.colors

        # Header row with active filter summary + clear button
        hdr = tk.Frame(parent, bg=c["bg"])
        hdr.pack(fill="x", pady=(0, 8))
        tk.Label(hdr, text="🏷️  Item Tag Filters",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(side="left")
        tk.Label(hdr,
                 text="Click once to include (✓), again to exclude (✗), again to clear.",
                 bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 8, "italic")).pack(side="left", padx=(10, 0))
        ttk.Button(hdr, text="✕ Clear All",
                   command=self._clear_tag_filters).pack(side="right")
        ttk.Button(hdr, text="☑ Include All",
                   command=self._select_all_tag_filters).pack(side="right", padx=(0, 4))

        self.tag_active_label = tk.Label(hdr, text="",
                                          bg=c["bg"], fg="#ff9900",
                                          font=("Georgia", 8, "bold"))
        self.tag_active_label.pack(side="right", padx=6)

        # Scrollable canvas for all category sections
        canvas_frame = tk.Frame(parent, bg=c["bg"])
        canvas_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(canvas_frame, bg=c["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=c["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(win, width=e.width)
        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        inner.bind("<MouseWheel>",
                   lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Build one collapsible section per category
        RARITY_COLORS = {
            "none": "#c8c8c8", "mundane": "#c8c8c8", "common": "#c8c8c8",
            "uncommon": "#1eff00", "rare": "#0070dd",
            "very rare": "#a335ee", "legendary": "#ff8000", "artifact": "#d4af37",
        }
        for cat_name, tags in TAG_CATEGORIES.items():
            self._build_tag_section(inner, cat_name, tags, c, RARITY_COLORS)

    def _build_tag_section(self, parent, cat_name: str, tags: list[str],
                            c: dict, rarity_colors: dict):
        """Build one collapsible category section with 3-state cycle buttons."""
        section = tk.Frame(parent, bg=c["bg"],
                           highlightbackground=c["sel"],
                           highlightthickness=1)
        section.pack(fill="x", padx=4, pady=3)

        collapsed = tk.BooleanVar(value=True)

        hdr = tk.Frame(section, bg=c["sel"], cursor="hand2")
        hdr.pack(fill="x")

        arrow_lbl = tk.Label(hdr, text="▶", font=("Consolas", 8),
                              bg=c["sel"], fg=c["accent"], width=2)
        arrow_lbl.pack(side="left", padx=(6, 2))
        tk.Label(hdr, text=cat_name, font=("Georgia", 9, "bold"),
                 bg=c["sel"], fg=c["fg"]).pack(side="left", pady=4)

        count_var = tk.StringVar(value="")
        count_lbl = tk.Label(hdr, textvariable=count_var,
                              bg=c["sel"], fg="#ff9900",
                              font=("Consolas", 8))
        count_lbl.pack(side="right", padx=8)

        body = tk.Frame(section, bg=c["bg"])

        # ── State colours ──────────────────────────────────────────────────────
        STATE_FG  = {0: c["fg"],    1: "#1eff00", 2: "#ff4444"}
        STATE_BG  = {0: c["bg"],    1: "#0d1f0d", 2: "#1f0d0d"}
        STATE_PFX = {0: "  ",       1: "✓ ",      2: "✗ "}

        def _refresh_count():
            n_inc = sum(1 for t in tags
                        if self._tag_state_vars.get(t, tk.IntVar()).get() == 1)
            n_exc = sum(1 for t in tags
                        if self._tag_state_vars.get(t, tk.IntVar()).get() == 2)
            parts = []
            if n_inc: parts.append(f"{n_inc} incl")
            if n_exc: parts.append(f"{n_exc} excl")
            count_var.set(" / ".join(parts))

        def _toggle(_=None):
            if collapsed.get():
                body.pack(fill="x", padx=8, pady=(4, 6))
                arrow_lbl.configure(text="▼")
                collapsed.set(False)
            else:
                body.pack_forget()
                arrow_lbl.configure(text="▶")
                collapsed.set(True)

        hdr.bind("<Button-1>", _toggle)
        for child in hdr.winfo_children():
            child.bind("<Button-1>", _toggle)

        cols = 4
        for idx, tag in enumerate(tags):
            var = tk.IntVar(value=0)
            self._tag_state_vars[tag] = var
            btn_ref: list = []   # mutable cell for the button reference

            def _cycle(t=tag, v=var, br=btn_ref, rf=_refresh_count):
                new_state = (v.get() + 1) % 3
                v.set(new_state)
                # Update include / exclude sets
                self.active_tag_filters.discard(t)
                self.excluded_tag_filters.discard(t)
                if new_state == 1:
                    self.active_tag_filters.add(t)
                elif new_state == 2:
                    self.excluded_tag_filters.add(t)
                # Repaint the button
                if br:
                    br[0].configure(
                        text=STATE_PFX[new_state] + t,
                        fg=STATE_FG[new_state],
                        bg=STATE_BG[new_state],
                    )
                rf()
                self._update_tag_summary_label()

            btn = tk.Button(
                body,
                text=STATE_PFX[0] + tag,
                command=_cycle,
                fg=STATE_FG[0], bg=STATE_BG[0],
                activeforeground=c["accent"],
                activebackground=c["sel"],
                relief="flat", bd=0,
                font=("Georgia", 8),
                anchor="w", padx=2,
            )
            btn_ref.append(btn)
            btn.grid(row=idx // cols, column=idx % cols, sticky="w", padx=2, pady=1)

    def _update_tag_summary_label(self):
        """Refresh the global 'N incl / N excl' label above the tag panels."""
        if not hasattr(self, "tag_active_label"):
            return
        n_inc = len(self.active_tag_filters)
        n_exc = len(self.excluded_tag_filters)
        parts = []
        if n_inc: parts.append(f"{n_inc} included")
        if n_exc: parts.append(f"{n_exc} excluded")
        self.tag_active_label.configure(text=" / ".join(parts))

    def _clear_tag_filters(self):
        self.active_tag_filters.clear()
        self.excluded_tag_filters.clear()
        c = self.colors
        for tag, var in self._tag_state_vars.items():
            var.set(0)
        # Repaint all buttons back to neutral — walk every tag section body
        self._repaint_all_tag_buttons()
        if hasattr(self, "tag_active_label"):
            self.tag_active_label.configure(text="")

    def _select_all_tag_filters(self):
        """Set every tag to include state."""
        self.active_tag_filters.clear()
        self.excluded_tag_filters.clear()
        c = self.colors
        for tag, var in self._tag_state_vars.items():
            var.set(1)
            self.active_tag_filters.add(tag)
        self._repaint_all_tag_buttons()
        self._update_tag_summary_label()

    def _repaint_all_tag_buttons(self):
        """Walk the widget tree and repaint any tag cycle-buttons to match state."""
        STATE_FG  = {0: self.colors["fg"], 1: "#1eff00", 2: "#ff4444"}
        STATE_BG  = {0: self.colors["bg"], 1: "#0d1f0d", 2: "#1f0d0d"}
        STATE_PFX = {0: "  ",             1: "✓ ",      2: "✗ "}
        for tag, var in self._tag_state_vars.items():
            s = var.get()
            # Buttons store their tag name inside the text — find by matching
            for widget in self._iter_tag_buttons():
                txt = widget.cget("text")
                # strip prefix (2 chars) to get bare tag name
                if len(txt) >= 2 and txt[2:] == tag:
                    widget.configure(
                        text=STATE_PFX[s] + tag,
                        fg=STATE_FG[s],
                        bg=STATE_BG[s],
                    )
                    break

    def _iter_tag_buttons(self):
        """Yield all tk.Button widgets that live inside tag section bodies."""
        def _recurse(w):
            if isinstance(w, tk.Button):
                yield w
            for child in w.winfo_children():
                yield from _recurse(child)
        if hasattr(self, "tab_settings"):
            yield from _recurse(self.tab_settings)

    def _on_slider(self, rarity: str, value: str):
        """Move one slider; if total would exceed 100%, clamp it and reduce
        other sliders proportionally to keep the sum at exactly 100."""
        new_val = int(float(value))
        self.rarity_sliders[rarity].set(new_val)

        others   = [r for r in self.rarity_sliders if r != rarity]
        others_sum = sum(self.rarity_sliders[r].get() for r in others)
        total      = new_val + others_sum

        if total > 100:
            excess = total - 100
            # Distribute the excess reduction across the other sliders,
            # proportionally — but never drop one below 0.
            reducible = [(r, self.rarity_sliders[r].get()) for r in others
                         if self.rarity_sliders[r].get() > 0]
            reducible_sum = sum(v for _, v in reducible)

            if reducible_sum > 0:
                for r, v in reducible:
                    cut = min(v, round(excess * v / reducible_sum))
                    self.rarity_sliders[r].set(max(0, v - cut))
                # Fix any rounding leftover by adjusting the largest reducible
                remaining = sum(self.rarity_sliders[r].get()
                                for r in others) + new_val - 100
                if remaining > 0:
                    for r, v in sorted(reducible, key=lambda x: -x[1]):
                        cur = self.rarity_sliders[r].get()
                        if cur > 0:
                            self.rarity_sliders[r].set(max(0, cur - remaining))
                            break
            else:
                # No other slider has room — clamp this one
                self.rarity_sliders[rarity].set(100 - others_sum)

        # Refresh all labels + total
        for r, var in self.rarity_sliders.items():
            self.slider_labels[r].set(f"{var.get():>3}%")
        total = sum(v.get() for v in self.rarity_sliders.values())
        color = self.colors["accent"] if total == 100 else "#ff4444"
        self.total_pct_var.set(f"Total: {total}%")

    def _on_wealth_change(self):
        wealth   = self.wealth_var.get()
        defaults = WEALTH_DEFAULTS.get(wealth, {})
        for rarity, var in self.rarity_sliders.items():
            val = defaults.get(rarity, 0)
            var.set(val)
            self.slider_labels[rarity].set(f"{val:>3}%")
        total = sum(v.get() for v in self.rarity_sliders.values())
        color = self.colors["accent"] if total == 100 else "#ff4444"
        self.total_pct_var.set(f"Total: {total}%")

    def _reset_distribution(self):
        """Reset sliders to the currently selected wealth preset."""
        self._on_wealth_change()

    # ── Price modifier ────────────────────────────────────────────────────────
    def _on_price_modifier(self, _=None):
        mod = int(float(self.price_modifier.get()))
        self.price_modifier.set(mod)
        self.price_mod_label.configure(text=f"{mod}%")
        # Highlight label when not at 100%
        color = "#ff9900" if mod != 100 else self.colors["accent"]
        self.price_mod_label.configure(fg=color)
        self._populate_table(self.current_items)

    def _reset_price_modifier(self):
        self.price_modifier.set(100)
        self.price_mod_label.configure(text="100%", fg=self.colors["accent"])
        self._populate_table(self.current_items)

    # ── Save Tab ──────────────────────────────────────────────────────────────
    def _build_save_tab(self):
        c = self.colors
        f = self.tab_save

        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        tk.Label(left, text="📚  Saved Campaigns",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w")

        tree_f = ttk.Frame(left)
        tree_f.pack(fill="both", expand=True, pady=4)

        self.save_tree = ttk.Treeview(tree_f, show="tree headings",
                                       columns=("info",), selectmode="browse")
        self.save_tree.heading("#0",   text="Campaign / Town / Shop")
        self.save_tree.heading("info", text="Details")
        self.save_tree.column("#0",   width=240)
        self.save_tree.column("info", width=200)
        vsb2 = ttk.Scrollbar(tree_f, orient="vertical", command=self.save_tree.yview)
        self.save_tree.configure(yscrollcommand=vsb2.set)
        self.save_tree.pack(side="left", fill="both", expand=True)
        vsb2.pack(side="right", fill="y")

        btn_f = tk.Frame(left, bg=c["bg"])
        btn_f.pack(fill="x", pady=4)
        ttk.Button(btn_f, text="📂 Load Shop",
                   command=self._load_selected_shop).pack(side="left", padx=4)
        ttk.Button(btn_f, text="🗑 Delete",
                   style="Danger.TButton",
                   command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(btn_f, text="📤 Export JSON",
                   command=self._export_json).pack(side="left", padx=4)
        ttk.Button(btn_f, text="📥 Import JSON",
                   command=self._import_json).pack(side="left", padx=4)

        # Save form
        right = ttk.Frame(f)
        right.pack(side="right", fill="y", padx=8, pady=8)

        tk.Label(right, text="💾  Save Current Shop",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(anchor="w", pady=(0, 8))

        self.save_campaign_var = tk.StringVar()
        self.save_town_var     = tk.StringVar()

        for label, var in [("Campaign Name:", self.save_campaign_var),
                            ("Town/Location:", self.save_town_var)]:
            tk.Label(right, text=label, bg=c["bg"], fg=c["fg"],
                     font=("Georgia", 9)).pack(anchor="w")
            tk.Entry(right, textvariable=var, width=30,
                     bg=c["sel"], fg=c["fg"],
                     insertbackground=c["fg"], relief="flat").pack(anchor="w", pady=(0, 8))

        ttk.Button(right, text="💾  Save Shop",
                   command=self._save_shop).pack(anchor="w", pady=4)
        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        tk.Label(right, text="📝  Shop Notes:",
                 bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 9)).pack(anchor="w")
        notes_frame = tk.Frame(right, bg=c["bg"])
        notes_frame.pack(fill="both", expand=True, pady=(0, 8))
        self.shop_notes_widget = tk.Text(
            notes_frame, width=30, height=7,
            bg=c["sel"], fg=c["fg"],
            insertbackground=c["fg"],
            relief="flat", font=("Georgia", 9),
            wrap="word",
        )
        notes_vsb = ttk.Scrollbar(notes_frame, orient="vertical",
                                   command=self.shop_notes_widget.yview)
        self.shop_notes_widget.configure(yscrollcommand=notes_vsb.set)
        notes_vsb.pack(side="right", fill="y")
        self.shop_notes_widget.pack(side="left", fill="both", expand=True)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.save_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.save_status_var,
                 bg=c["bg"], fg=c["accent"],
                 font=("Georgia", 9, "italic"),
                 wraplength=220).pack(anchor="w")

    # ── Core actions ──────────────────────────────────────────────────────────
    def _get_rarity_weights(self) -> dict[str, int]:
        return {r: v.get() for r, v in self.rarity_sliders.items()}

    def _get_item_count(self) -> int:
        lo, hi = CITY_SIZE_RANGES.get(self.city_size_var.get(), (15, 25))
        return random.randint(lo, hi)

    def _reroll_single_item(self, item: dict):
        """Replace one unlocked item in the shop with a fresh pick of the same rarity."""
        if item.get("locked"):
            return
        shop_type = self.current_shop_type.get()
        if shop_type not in ALL_ITEMS or not ALL_ITEMS[shop_type]:
            return

        target_rarity = normalize_rarity(item.get("rarity", ""))
        existing_names = {i["name"] for i in self.current_items if i["name"] != item["name"]}
        excl    = self.excluded_tag_filters or set()
        incl    = self.active_tag_filters   or set()
        culture = self.culture_var.get()
        culture = culture if culture and culture != "(None)" else None

        def _pool_filter(x: dict) -> bool:
            if x["Name"] in existing_names:
                return False
            item_tags = {t.strip() for t in x.get("Tags", "").split(",") if t.strip()}
            if excl and (item_tags & excl):
                return False
            if incl and not (item_tags & incl):
                return False
            if not culture_match(x, culture):
                return False
            return True

        # Build a pool of same-rarity candidates not already in the shop
        pool = [x for x in ALL_ITEMS[shop_type]
                if normalize_rarity(x.get("Rarity", "")) == target_rarity
                and _pool_filter(x)]
        # Fallback: any rarity if pool is empty
        if not pool:
            pool = [x for x in ALL_ITEMS[shop_type] if _pool_filter(x)]
        if not pool:
            return

        chosen = random.choice(pool)
        new_item = {
            "item_id":    chosen.get("Item ID", ""),
            "name":       chosen.get("Name", ""),
            "rarity":     chosen.get("Rarity", ""),
            "item_type":  chosen.get("Type", ""),
            "source":     chosen.get("Source", ""),
            "page":       chosen.get("Page", ""),
            "cost_given": chosen.get("Value", ""),
            "quantity":   str(generate_item_quantity(
                              chosen,
                              self.city_size_var.get(),
                              self.wealth_var.get())),
            "locked":     False,
            "attunement": chosen.get("Attunement", ""),
            "damage":     chosen.get("Damage", ""),
            "properties": chosen.get("Properties", ""),
            "mastery":    chosen.get("Mastery", ""),
            "weight":     chosen.get("Weight", ""),
            "tags":       chosen.get("Tags", ""),
            "description": chosen.get("Text", ""),
        }

        # Swap in place to preserve list order
        for idx, i in enumerate(self.current_items):
            if i["name"] == item["name"]:
                self.current_items[idx] = new_item
                break

        self._populate_table(self.current_items)
        self.selected_row = new_item
        self._show_inspect(new_item)
        # Reselect the new row in the tree
        try:
            self.tree.selection_set(new_item["name"])
            self.tree.see(new_item["name"])
        except Exception:
            pass
        self.status_var.set(f"🎲  Rerolled '{item['name']}' → '{new_item['name']}'")

    def _run_generate(self):
        shop_type = self.current_shop_type.get()
        if not shop_type:
            messagebox.showerror("Error", "Please select a shop type.")
            return
        count   = self._get_item_count()
        weights = self._get_rarity_weights()
        if sum(weights.values()) == 0:
            messagebox.showwarning("Warning", "All weights are 0 — using Average defaults.")
            weights = WEALTH_DEFAULTS["Average"]
        culture = self.culture_var.get()
        culture = culture if culture and culture != "(None)" else None
        self.current_items = generate_shop_items(
            shop_type, count, weights,
            tag_filters=self.active_tag_filters   if self.active_tag_filters   else None,
            tag_excludes=self.excluded_tag_filters if self.excluded_tag_filters else None,
            city_size=self.city_size_var.get(),
            wealth=self.wealth_var.get(),
            culture=culture)
        self._populate_table(self.current_items)
        culture_label = f" / {culture}" if culture else ""
        self.status_var.set(
            f"✅  Generated {len(self.current_items)} items for {shop_type}  "
            f"({self.city_size_var.get()} / {self.wealth_var.get()}{culture_label})"
        )

    def _reroll(self):
        if not self.current_items:
            messagebox.showinfo("Info", "Generate a shop first.")
            return
        pct       = random.randint(10, 30) / 100
        shop_type = self.current_shop_type.get()
        locked    = [i for i in self.current_items if i.get("locked")]
        unlocked  = [i for i in self.current_items if not i.get("locked")]
        n_reroll  = max(1, int(len(unlocked) * pct))
        keep      = random.sample(unlocked, max(0, len(unlocked) - n_reroll))
        weights   = self._get_rarity_weights()
        culture   = self.culture_var.get()
        culture   = culture if culture and culture != "(None)" else None
        new_items = generate_shop_items(
            shop_type, len(self.current_items), weights, locked + keep,
            tag_filters=self.active_tag_filters   if self.active_tag_filters   else None,
            tag_excludes=self.excluded_tag_filters if self.excluded_tag_filters else None,
            city_size=self.city_size_var.get(),
            wealth=self.wealth_var.get(),
            culture=culture)
        self.current_items = new_items
        self._populate_table(self.current_items)
        self.status_var.set(
            f"🔄  Rerolled ~{int(pct*100)}% of unlocked items ({n_reroll} swapped)"
        )

    def _clear(self):
        if messagebox.askyesno("Clear Shop", "Clear all items?"):
            self.current_items = []
            self._populate_table([])
            self._clear_inspect()
            self.status_var.set("🗑  Shop cleared.")

    def _random_name(self):
        self.shop_name_var.set(generate_shop_name(self.current_shop_type.get()))

    def _on_shop_type_change(self, _=None):
        self._random_name()

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid  = sel[0]
        item = next((i for i in self.current_items if i["name"] == iid), None)
        if item:
            self.selected_row = item
            self._show_inspect(item)

    def _on_double_click(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        for item in self.current_items:
            if item["name"] == iid:
                item["locked"] = not item.get("locked", False)
                break
        self._populate_table(self.current_items)

    # ── Save / Load ───────────────────────────────────────────────────────────
    def _save_shop(self):
        campaign  = self.save_campaign_var.get().strip()
        town      = self.save_town_var.get().strip()
        shop_name = self.shop_name_var.get().strip() or f"{self.current_shop_type.get()} Shop"
        notes     = self.shop_notes_widget.get("1.0", "end").strip() if self.shop_notes_widget else ""

        if not campaign:
            messagebox.showerror("Error", "Enter a campaign name."); return
        if not town:
            messagebox.showerror("Error", "Enter a town/location name."); return
        if not self.current_items:
            messagebox.showerror("Error", "Generate a shop first."); return

        con = sqlite3.connect(DB_PATH)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO campaigns (name) VALUES (?)", (campaign,))
            cur.execute("SELECT id FROM campaigns WHERE name=?", (campaign,))
            camp_id = cur.fetchone()[0]

            # Reuse existing town with the same name in this campaign
            existing_town = cur.execute(
                "SELECT id FROM towns WHERE campaign_id=? AND name=?",
                (camp_id, town)).fetchone()
            if existing_town:
                town_id = existing_town[0]
                cur.execute("UPDATE towns SET city_size=? WHERE id=?",
                            (self.city_size_var.get(), town_id))
            else:
                cur.execute("INSERT INTO towns (campaign_id, name, city_size) VALUES (?,?,?)",
                            (camp_id, town, self.city_size_var.get()))
                town_id = cur.lastrowid

            cur.execute(
                "INSERT INTO shops (town_id, name, shop_type, wealth, last_restocked, notes) "
                "VALUES (?,?,?,?,?,?)",
                (town_id, shop_name, self.current_shop_type.get(),
                 self.wealth_var.get(), datetime.now().isoformat(), notes))
            shop_id = cur.lastrowid

            for item in self.current_items:
                cur.execute("""INSERT INTO shop_items
                    (shop_id,item_id,name,rarity,item_type,source,page,
                     cost_given,quantity,locked,
                     attunement,damage,properties,mastery,weight,tags,description)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (shop_id, item.get("item_id",""), item["name"],
                     item.get("rarity",""), item.get("item_type",""),
                     item.get("source",""), item.get("page",""),
                     item.get("cost_given",""), item.get("quantity","1"),
                     int(item.get("locked",False)),
                     item.get("attunement",""), item.get("damage",""),
                     item.get("properties",""), item.get("mastery",""),
                     item.get("weight",""), item.get("tags",""),
                     item.get("description","")))
            con.commit()
            self.save_status_var.set(f"✅ Saved '{shop_name}' → {campaign} / {town}")
            self._refresh_campaign_list()
        except Exception as e:
            con.rollback()
            messagebox.showerror("Save Failed", f"Could not save shop:\n{e}")
            self.save_status_var.set("❌ Save failed — no changes written.")
        finally:
            con.close()

    def _refresh_campaign_list(self):
        self.save_tree.delete(*self.save_tree.get_children())
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        for (cid, cname) in cur.execute(
                "SELECT id, name FROM campaigns ORDER BY name"):
            cn = self.save_tree.insert("", "end", iid=f"c{cid}",
                                       text=f"📚 {cname}", values=("",))
            for (tid, tname, tsize) in cur.execute(
                    "SELECT id, name, city_size FROM towns "
                    "WHERE campaign_id=? ORDER BY name", (cid,)):
                tn = self.save_tree.insert(cn, "end", iid=f"t{tid}",
                                           text=f"🏙 {tname}",
                                           values=(tsize or "",))
                for (sid, sname, stype, swealth) in cur.execute(
                        "SELECT id, name, shop_type, wealth FROM shops "
                        "WHERE town_id=? ORDER BY name", (tid,)):
                    self.save_tree.insert(tn, "end", iid=f"s{sid}",
                                          text=f"🏪 {sname}",
                                          values=(f"{stype} / {swealth}",))
        con.close()

    def _load_selected_shop(self):
        sel = self.save_tree.selection()
        if not sel or not sel[0].startswith("s"):
            messagebox.showinfo("Info", "Select a shop (🏪) to load.")
            return
        shop_id = int(sel[0][1:])
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        row = cur.execute(
            "SELECT name, shop_type, wealth, notes FROM shops WHERE id=?",
            (shop_id,)).fetchone()
        if not row:
            con.close(); return
        shop_name, shop_type, wealth, notes = row[0], row[1], row[2], (row[3] or "")

        # Resolve campaign and town names so the Save form is pre-filled
        town_row = cur.execute(
            "SELECT t.name, t.city_size, c.name FROM towns t "
            "JOIN campaigns c ON c.id = t.campaign_id "
            "WHERE t.id = (SELECT town_id FROM shops WHERE id=?)",
            (shop_id,)).fetchone()
        town_name, city_size, camp_name = town_row if town_row else ("", "", "")

        items_raw = cur.execute("""
            SELECT item_id,name,rarity,item_type,source,page,
                   cost_given,quantity,locked,
                   attunement,damage,properties,mastery,weight,tags,description
            FROM shop_items WHERE shop_id=?""", (shop_id,)).fetchall()
        con.close()

        self.current_items = [{
            "item_id": r[0], "name": r[1], "rarity": r[2],
            "item_type": r[3], "source": r[4], "page": r[5],
            "cost_given": r[6], "quantity": r[7], "locked": bool(r[8]),
            "attunement": r[9], "damage": r[10],
            "properties": r[11], "mastery": r[12],
            "weight": r[13], "tags": r[14], "description": r[15],
        } for r in items_raw]

        self.shop_name_var.set(shop_name)
        self.current_shop_type.set(shop_type)
        self.wealth_var.set(wealth)
        if city_size:
            self.city_size_var.set(city_size)
        # Pre-fill save form fields so re-saving is seamless
        self.save_campaign_var.set(camp_name)
        self.save_town_var.set(town_name)
        # Restore notes into the text widget
        if self.shop_notes_widget:
            self.shop_notes_widget.delete("1.0", "end")
            self.shop_notes_widget.insert("1.0", notes)
        self._on_wealth_change()
        self._populate_table(self.current_items)
        self.status_var.set(
            f"📂  Loaded '{shop_name}' ({len(self.current_items)} items)"
        )

    def _delete_selected(self):
        sel = self.save_tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Select an item to delete.")
            return
        if not messagebox.askyesno("Delete", "Delete selected? This cannot be undone."):
            return
        iid = sel[0]
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        if iid.startswith("c"):
            cur.execute("DELETE FROM campaigns WHERE id=?", (int(iid[1:]),))
        elif iid.startswith("t"):
            cur.execute("DELETE FROM towns WHERE id=?", (int(iid[1:]),))
        elif iid.startswith("s"):
            cur.execute("DELETE FROM shops WHERE id=?", (int(iid[1:]),))
        con.commit()
        con.close()
        self._refresh_campaign_list()

    def _export_json(self):
        sel = self.save_tree.selection()
        if not sel or not sel[0].startswith("s"):
            messagebox.showinfo("Info", "Select a shop (🏪) to export.")
            return
        shop_id = int(sel[0][1:])
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        shop  = cur.execute("SELECT * FROM shops WHERE id=?",
                            (shop_id,)).fetchone()
        items = cur.execute("SELECT * FROM shop_items WHERE shop_id=?",
                            (shop_id,)).fetchall()
        con.close()

        data = {
            "shop": dict(zip(
                ["id","town_id","name","shop_type","wealth",
                 "last_restocked","created_at","notes"], shop)),
            "items": [dict(zip(
                ["id","shop_id","item_id","name","rarity","item_type",
                 "source","page","cost_given","quantity",
                 "locked","attunement","damage",
                 "properties","mastery","weight","tags","description"], i))
                for i in items],
        }
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile=re.sub(r'[\\/:*?"<>|]', "_",
                               data["shop"]["name"]).replace(" ", "_") + ".json")
        if path:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Exported", f"Shop saved to:\n{path}")

    def _import_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            messagebox.showerror("Import Error", f"Could not read JSON file:\n{e}")
            return
        self.current_items = []
        for i in data.get("items", []):
            self.current_items.append({
                "item_id":    i.get("item_id",""),
                "name":       i.get("name",""),
                "rarity":     i.get("rarity",""),
                "item_type":  i.get("item_type",""),
                "source":     i.get("source",""),
                "page":       i.get("page",""),
                "cost_given": i.get("cost_given",""),
                "quantity":   i.get("quantity","1"),
                "locked":     bool(i.get("locked",False)),
                "attunement": i.get("attunement",""),
                "damage":     i.get("damage",""),
                "properties": i.get("properties",""),
                "mastery":    i.get("mastery",""),
                "weight":     i.get("weight",""),
                "tags":       i.get("tags",""),
                "description": i.get("description",""),
            })
        sdata = data.get("shop", {})
        self.shop_name_var.set(sdata.get("name", "Imported Shop"))
        self.current_shop_type.set(sdata.get("shop_type", "Magic"))
        # Restore wealth level and sync sliders — was missing, leaving UI out of sync
        wealth = sdata.get("wealth", "Average")
        if wealth in WEALTH_DEFAULTS:
            self.wealth_var.set(wealth)
            self._on_wealth_change()
        # Restore notes
        if self.shop_notes_widget:
            self.shop_notes_widget.delete("1.0", "end")
            self.shop_notes_widget.insert("1.0", sdata.get("notes", ""))
        self._populate_table(self.current_items)
        self.status_var.set(
            f"📥  Imported {len(self.current_items)} items from JSON."
        )


    # ══════════════════════════════════════════════════════════════════════════
    #  Item Gallery Tab
    # ══════════════════════════════════════════════════════════════════════════

    def _build_gallery_tab(self):
        c = self.colors
        f = self.tab_gallery

        # Left pane
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True)
        self._gallery_left = left

        # Search / filter bar
        bar = tk.Frame(left, bg=c["hdr"], pady=6)
        bar.pack(fill="x")

        tk.Label(bar, text="\U0001f4da  Item Gallery",
                 font=("Georgia", 13, "bold"),
                 bg=c["hdr"], fg=c["accent"]).pack(side="left", padx=(10, 16))

        tk.Label(bar, text="\U0001f50d", bg=c["hdr"], fg=c["fg"]).pack(side="left")
        self.gallery_search_var = tk.StringVar()
        self.gallery_search_var.trace_add("write", lambda *_: self._gallery_refresh())
        tk.Entry(bar, textvariable=self.gallery_search_var, width=30,
                 bg=c["sel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Consolas", 9)).pack(side="left", padx=(4, 12))

        tk.Label(bar, text="Rarity:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9)).pack(side="left")
        self.gallery_rarity_var = tk.StringVar(value="All")
        rarity_opts = ["All", "Mundane", "Common", "Uncommon", "Rare",
                       "Very Rare", "Legendary", "Artifact"]
        ttk.Combobox(bar, textvariable=self.gallery_rarity_var,
                     values=rarity_opts, width=12,
                     state="readonly").pack(side="left", padx=(4, 12))
        self.gallery_rarity_var.trace_add("write", lambda *_: self._gallery_refresh())

        tk.Label(bar, text="Source:", bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9)).pack(side="left")
        self.gallery_source_var = tk.StringVar()
        self.gallery_source_var.trace_add("write", lambda *_: self._gallery_refresh())
        tk.Entry(bar, textvariable=self.gallery_source_var, width=10,
                 bg=c["sel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Consolas", 9)).pack(side="left", padx=(4, 12))

        self.gallery_count_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.gallery_count_var,
                 bg=c["hdr"], fg=c["accent"],
                 font=("Georgia", 9, "italic")).pack(side="left")

        # Tag filter state (independent from stock-settings filters)
        # gallery_tag_filters  — include set
        # gallery_tag_excludes — exclude set
        # _gallery_tag_state_vars — IntVar per tag: 0=neutral, 1=include, 2=exclude
        self.gallery_tag_filters:  set[str] = set()
        self.gallery_tag_excludes: set[str] = set()
        self._gallery_tag_state_vars: dict[str, tk.IntVar] = {}

        # Tag filter header row
        tag_hdr = tk.Frame(left, bg=c["bg"])
        tag_hdr.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(tag_hdr, text="\U0001f3f7\ufe0f  Item Tag Filters",
                 font=("Georgia", 11, "bold"),
                 bg=c["bg"], fg=c["accent"]).pack(side="left")
        tk.Label(tag_hdr,
                 text="Click once to include (✓), again to exclude (✗), again to clear.",
                 bg=c["bg"], fg=c["fg"],
                 font=("Georgia", 8, "italic")).pack(side="left", padx=(10, 0))
        ttk.Button(tag_hdr, text="\u2715 Clear All",
                   command=self._gallery_clear_tags).pack(side="right")
        ttk.Button(tag_hdr, text="\u2611 Include All",
                   command=self._gallery_select_all_tags).pack(side="right", padx=(0, 4))
        self.gallery_tag_active_lbl = tk.Label(tag_hdr, text="",
                                                bg=c["bg"], fg="#ff9900",
                                                font=("Georgia", 8, "bold"))
        self.gallery_tag_active_lbl.pack(side="right", padx=6)

        # Scrollable canvas for collapsible sections
        gtag_canvas_frame = tk.Frame(left, bg=c["bg"], height=180)
        gtag_canvas_frame.pack(fill="x", padx=6, pady=(0, 4))
        gtag_canvas_frame.pack_propagate(False)

        gtag_canvas = tk.Canvas(gtag_canvas_frame, bg=c["bg"], highlightthickness=0)
        gtag_vsb    = ttk.Scrollbar(gtag_canvas_frame, orient="vertical",
                                    command=gtag_canvas.yview)
        gtag_canvas.configure(yscrollcommand=gtag_vsb.set)
        gtag_vsb.pack(side="right", fill="y")
        gtag_canvas.pack(side="left", fill="both", expand=True)

        gtag_inner = tk.Frame(gtag_canvas, bg=c["bg"])
        gtag_win   = gtag_canvas.create_window((0, 0), window=gtag_inner, anchor="nw")

        def _gtag_inner_configure(e):
            gtag_canvas.configure(scrollregion=gtag_canvas.bbox("all"))
        def _gtag_canvas_configure(e):
            gtag_canvas.itemconfig(gtag_win, width=e.width)
        gtag_inner.bind("<Configure>", _gtag_inner_configure)
        gtag_canvas.bind("<Configure>", _gtag_canvas_configure)
        gtag_canvas.bind("<MouseWheel>",
                         lambda e: gtag_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        gtag_inner.bind("<MouseWheel>",
                        lambda e: gtag_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        GTAG_RARITY_COLORS = {
            "none": "#c8c8c8", "mundane": "#c8c8c8", "common": "#c8c8c8",
            "uncommon": "#1eff00", "rare": "#0070dd",
            "very rare": "#a335ee", "legendary": "#ff8000", "artifact": "#d4af37",
        }
        for cat_name, tags in TAG_CATEGORIES.items():
            self._build_gallery_tag_section(gtag_inner, cat_name, tags, c, GTAG_RARITY_COLORS)

        # Results treeview
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        GCOLS   = ("name", "rarity", "type", "source", "value")
        GHDRS   = ("Name", "Rarity", "Type", "Source", "Value")
        GWIDTHS = (290, 95, 200, 80, 100)

        self.gallery_tree = ttk.Treeview(tree_frame, columns=GCOLS,
                                          show="headings", selectmode="browse")
        for col, hdr, w in zip(GCOLS, GHDRS, GWIDTHS):
            self.gallery_tree.heading(col, text=hdr,
                                      command=lambda c=col: self._gallery_sort(c))
            self.gallery_tree.column(col, width=w,
                                     anchor="w" if col in ("name","type") else "center")

        self.gallery_tree.tag_configure("odd",  background=ROW_ODD)
        self.gallery_tree.tag_configure("even", background=ROW_EVEN)
        for rarity, color in GTAG_RARITY_COLORS.items():
            self.gallery_tree.tag_configure(
                rarity.replace(" ", "_"), foreground=color)

        gvsb = ttk.Scrollbar(tree_frame, orient="vertical",
                              command=self.gallery_tree.yview)
        self.gallery_tree.configure(yscrollcommand=gvsb.set)
        self.gallery_tree.pack(side="left", fill="both", expand=True)
        gvsb.pack(side="right", fill="y")
        self.gallery_tree.bind("<<TreeviewSelect>>", self._gallery_on_select)

        self._gallery_sort_col = "name"
        self._gallery_sort_asc = True
        self._gallery_results:  list[dict] = []

        # Right pane: place()-based inspector (same pattern as action tab)
        self._gallery_inspect_expanded       = False
        self._gallery_inspect_width_collapsed = 310
        self._gallery_inspect_width_expanded  = None

        self.gallery_inspect_panel = tk.Frame(f, bg=c["hdr"])
        self.gallery_inspect_panel.place(relx=1.0, rely=0.0, anchor="ne",
                                          width=self._gallery_inspect_width_collapsed,
                                          relheight=1.0)
        left.pack_configure(padx=(0, self._gallery_inspect_width_collapsed + 6))

        ginsp_hdr = tk.Frame(self.gallery_inspect_panel, bg=c["hdr"])
        ginsp_hdr.pack(fill="x", padx=8, pady=(10, 0))

        tk.Label(ginsp_hdr, text="\U0001f4d6  Item Inspector",
                 font=("Georgia", 11, "bold"),
                 bg=c["hdr"], fg=c["accent"]).pack(side="left")

        self.gallery_expand_btn = tk.Label(
            ginsp_hdr, text="\u2922", font=("Georgia", 13),
            bg=c["hdr"], fg=c["fg"], cursor="hand2", padx=4)
        self.gallery_expand_btn.pack(side="right")
        self.gallery_expand_btn.bind("<Button-1>",
                                     lambda e: self._toggle_gallery_inspect_expand())
        self.gallery_expand_btn.bind("<Enter>",
            lambda e: self.gallery_expand_btn.configure(fg=c["accent"]))
        self.gallery_expand_btn.bind("<Leave>",
            lambda e: self.gallery_expand_btn.configure(fg=c["fg"]))

        ttk.Separator(self.gallery_inspect_panel, orient="horizontal").pack(
            fill="x", padx=8, pady=(4, 0))

        ginsp_canvas = tk.Canvas(self.gallery_inspect_panel, bg=c["hdr"],
                                  highlightthickness=0)
        ginsp_vsb    = ttk.Scrollbar(self.gallery_inspect_panel, orient="vertical",
                                     command=ginsp_canvas.yview)
        ginsp_canvas.configure(yscrollcommand=ginsp_vsb.set)
        ginsp_vsb.pack(side="right", fill="y")
        ginsp_canvas.pack(side="left", fill="both", expand=True)

        self.gallery_inspect_frame = tk.Frame(ginsp_canvas, bg=c["hdr"])
        self._ginsp_win = ginsp_canvas.create_window(
            (0, 0), window=self.gallery_inspect_frame, anchor="nw")

        def _ginsp_configure(e):
            ginsp_canvas.configure(scrollregion=ginsp_canvas.bbox("all"))
        def _ginsp_canvas_configure(e):
            ginsp_canvas.itemconfig(self._ginsp_win, width=e.width)
        self.gallery_inspect_frame.bind("<Configure>", _ginsp_configure)
        ginsp_canvas.bind("<Configure>", _ginsp_canvas_configure)
        ginsp_canvas.bind("<MouseWheel>",
                          lambda e: ginsp_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        self.gallery_inspect_frame.bind("<MouseWheel>",
                          lambda e: ginsp_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        tk.Label(self.gallery_inspect_frame,
                 text="Search and select an item to inspect.",
                 bg=c["hdr"], fg=c["fg"],
                 font=("Georgia", 9, "italic")).pack(pady=20, padx=10)

        self._gallery_refresh()

    # ── Gallery expand / collapse ─────────────────────────────────────────────
    def _toggle_gallery_inspect_expand(self):
        expanding = not self._gallery_inspect_expanded
        if self._gallery_inspect_width_expanded is None or expanding:
            self.update_idletasks()
            win_w = self.winfo_width()
            self._gallery_inspect_width_expanded = max(600, int(win_w * 0.56))
        w = (self._gallery_inspect_width_expanded if expanding
             else self._gallery_inspect_width_collapsed)
        self.gallery_inspect_panel.place_configure(width=w)
        self._gallery_left.pack_configure(padx=(0, w + 6))
        self._gallery_inspect_expanded = expanding
        self.gallery_expand_btn.configure(text="\u2921" if expanding else "\u2922")

    # ── Gallery tag sections (3-state: neutral / include / exclude) ─────────────
    def _build_gallery_tag_section(self, parent, cat_name: str, tags: list[str],
                                    c: dict, rarity_colors: dict):
        section = tk.Frame(parent, bg=c["bg"],
                           highlightbackground=c["sel"], highlightthickness=1)
        section.pack(fill="x", padx=4, pady=3)
        collapsed = tk.BooleanVar(value=True)

        hdr = tk.Frame(section, bg=c["sel"], cursor="hand2")
        hdr.pack(fill="x")
        arrow_lbl = tk.Label(hdr, text="\u25b6", font=("Consolas", 8),
                              bg=c["sel"], fg=c["accent"], width=2)
        arrow_lbl.pack(side="left", padx=(6, 2))
        tk.Label(hdr, text=cat_name, font=("Georgia", 9, "bold"),
                 bg=c["sel"], fg=c["fg"]).pack(side="left", pady=4)
        count_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=count_var, bg=c["sel"], fg="#ff9900",
                 font=("Consolas", 8)).pack(side="right", padx=8)
        body = tk.Frame(section, bg=c["bg"])

        STATE_FG  = {0: c["fg"],    1: "#1eff00", 2: "#ff4444"}
        STATE_BG  = {0: c["bg"],    1: "#0d1f0d", 2: "#1f0d0d"}
        STATE_PFX = {0: "  ",       1: "✓ ",      2: "✗ "}

        def _refresh_count():
            n_inc = sum(1 for t in tags
                        if self._gallery_tag_state_vars.get(t, tk.IntVar()).get() == 1)
            n_exc = sum(1 for t in tags
                        if self._gallery_tag_state_vars.get(t, tk.IntVar()).get() == 2)
            parts = []
            if n_inc: parts.append(f"{n_inc} incl")
            if n_exc: parts.append(f"{n_exc} excl")
            count_var.set(" / ".join(parts))

        def _toggle_section(_=None):
            if collapsed.get():
                body.pack(fill="x", padx=8, pady=(4, 6))
                arrow_lbl.configure(text="\u25bc")
                collapsed.set(False)
            else:
                body.pack_forget()
                arrow_lbl.configure(text="\u25b6")
                collapsed.set(True)

        hdr.bind("<Button-1>", _toggle_section)
        for child in hdr.winfo_children():
            child.bind("<Button-1>", _toggle_section)

        cols = 4
        for idx, tag in enumerate(tags):
            var = tk.IntVar(value=0)
            self._gallery_tag_state_vars[tag] = var
            btn_ref: list = []

            def _cycle(t=tag, v=var, br=btn_ref, rf=_refresh_count):
                new_state = (v.get() + 1) % 3
                v.set(new_state)
                self.gallery_tag_filters.discard(t)
                self.gallery_tag_excludes.discard(t)
                if new_state == 1:
                    self.gallery_tag_filters.add(t)
                elif new_state == 2:
                    self.gallery_tag_excludes.add(t)
                if br:
                    br[0].configure(
                        text=STATE_PFX[new_state] + t,
                        fg=STATE_FG[new_state],
                        bg=STATE_BG[new_state],
                    )
                rf()
                self._update_gallery_tag_summary()
                self._gallery_refresh()

            btn = tk.Button(
                body,
                text=STATE_PFX[0] + tag,
                command=_cycle,
                fg=STATE_FG[0], bg=STATE_BG[0],
                activeforeground=c["accent"],
                activebackground=c["sel"],
                relief="flat", bd=0,
                font=("Georgia", 8),
                anchor="w", padx=2,
            )
            btn_ref.append(btn)
            btn.grid(row=idx // cols, column=idx % cols, sticky="w", padx=2, pady=1)

    def _update_gallery_tag_summary(self):
        if not hasattr(self, "gallery_tag_active_lbl"):
            return
        n_inc = len(self.gallery_tag_filters)
        n_exc = len(self.gallery_tag_excludes)
        parts = []
        if n_inc: parts.append(f"{n_inc} included")
        if n_exc: parts.append(f"{n_exc} excluded")
        self.gallery_tag_active_lbl.configure(text=" / ".join(parts))

    def _gallery_tag_toggle(self, tag: str, var: tk.BooleanVar, refresh_count_fn=None):
        # Legacy stub — no longer called; kept to avoid AttributeError if referenced elsewhere
        pass

    def _gallery_clear_tags(self):
        self.gallery_tag_filters.clear()
        self.gallery_tag_excludes.clear()
        for var in self._gallery_tag_state_vars.values():
            var.set(0)
        self._repaint_gallery_tag_buttons()
        self.gallery_tag_active_lbl.configure(text="")
        self._gallery_refresh()

    def _gallery_select_all_tags(self):
        self.gallery_tag_filters.clear()
        self.gallery_tag_excludes.clear()
        for tag, var in self._gallery_tag_state_vars.items():
            var.set(1)
            self.gallery_tag_filters.add(tag)
        self._repaint_gallery_tag_buttons()
        self._update_gallery_tag_summary()
        self._gallery_refresh()

    def _repaint_gallery_tag_buttons(self):
        """Repaint all gallery tag buttons to match their current IntVar state."""
        STATE_FG  = {0: self.colors["fg"], 1: "#1eff00", 2: "#ff4444"}
        STATE_BG  = {0: self.colors["bg"], 1: "#0d1f0d", 2: "#1f0d0d"}
        STATE_PFX = {0: "  ",             1: "✓ ",      2: "✗ "}
        for tag, var in self._gallery_tag_state_vars.items():
            s = var.get()
            for widget in self._iter_gallery_tab_buttons():
                txt = widget.cget("text")
                if len(txt) >= 2 and txt[2:] == tag:
                    widget.configure(
                        text=STATE_PFX[s] + tag,
                        fg=STATE_FG[s],
                        bg=STATE_BG[s],
                    )
                    break

    def _iter_gallery_tab_buttons(self):
        """Yield tk.Button widgets inside the gallery tag canvas."""
        def _recurse(w):
            if isinstance(w, tk.Button):
                yield w
            for child in w.winfo_children():
                yield from _recurse(child)
        if hasattr(self, "tab_gallery"):
            yield from _recurse(self.tab_gallery)

    def _gallery_refresh(self):
        q       = self.gallery_search_var.get().strip().lower()
        rfilter = self.gallery_rarity_var.get()
        sfilter = self.gallery_source_var.get().strip().lower()

        results = []
        for item in ALL_ITEMS_FLAT:
            # Name search
            if q and q not in item.get("Name", "").lower():
                continue
            # Rarity filter
            if rfilter != "All":
                if normalize_rarity(item.get("Rarity", "")) != rfilter.lower():
                    continue
            # Source filter
            if sfilter and sfilter not in item.get("Source", "").lower():
                continue
            # Tag filter — exclude beats include
            if self.gallery_tag_excludes or self.gallery_tag_filters:
                item_tags = {t.strip() for t in item.get("Tags", "").split(",") if t.strip()}
                if self.gallery_tag_excludes and (item_tags & self.gallery_tag_excludes):
                    continue
                if self.gallery_tag_filters and not (item_tags & self.gallery_tag_filters):
                    continue
            results.append(item)

        # Sort
        col = self._gallery_sort_col
        rev = not self._gallery_sort_asc
        if col == "name":
            results.sort(key=lambda x: x.get("Name", "").lower(), reverse=rev)
        elif col == "rarity":
            results.sort(key=lambda x: (rarity_rank(x.get("Rarity", "")),
                                         x.get("Name", "").lower()), reverse=rev)
        elif col == "source":
            results.sort(key=lambda x: x.get("Source", "").lower(), reverse=rev)
        elif col == "value":
            results.sort(key=lambda x: parse_given_cost(x.get("Value", "")) or 0, reverse=rev)
        else:
            results.sort(key=lambda x: x.get("Name", "").lower(), reverse=rev)

        # Cap display at 500 for performance
        capped = results[:500]
        self._gallery_results = capped

        self.gallery_count_var.set(
            f"{len(results):,} items" + (" (showing 500)" if len(results) > 500 else ""))

        self.gallery_tree.delete(*self.gallery_tree.get_children())
        RARITY_COLORS = {
            "mundane": "#c8c8c8", "none": "#c8c8c8", "common": "#c8c8c8",
            "uncommon": "#1eff00", "rare": "#0070dd", "very rare": "#a335ee",
            "legendary": "#ff8000", "artifact": "#d4af37",
        }
        for idx, item in enumerate(capped):
            rnorm  = normalize_rarity(item.get("Rarity", ""))
            r_tag  = rnorm.replace(" ", "_")
            parity = "odd" if idx % 2 == 0 else "even"
            self.gallery_tree.insert("", "end",
                iid=f"g_{idx}",
                values=(
                    item.get("Name", ""),
                    item.get("Rarity", "—"),
                    item.get("Type", "—"),
                    item.get("Source", "—"),
                    item.get("Value", "—") or "—",
                ),
                tags=(parity, r_tag),
            )

    def _gallery_sort(self, col: str):
        if self._gallery_sort_col == col:
            self._gallery_sort_asc = not self._gallery_sort_asc
        else:
            self._gallery_sort_col = col
            self._gallery_sort_asc = True
        self._gallery_refresh()

    def _gallery_on_select(self, _=None):
        sel = self.gallery_tree.selection()
        if not sel:
            return
        iid = sel[0]
        try:
            idx = int(iid.split("_")[1])
            raw = self._gallery_results[idx]
        except (IndexError, ValueError):
            return

        # Convert raw CSV dict to the inspector's expected shape, flag as gallery
        item = {
            "name":        raw.get("Name", ""),
            "rarity":      raw.get("Rarity", ""),
            "item_type":   raw.get("Type", ""),
            "source":      raw.get("Source", ""),
            "page":        raw.get("Page", ""),
            "item_id":     raw.get("Item ID", ""),
            "cost_given":  raw.get("Value", ""),
            "attunement":  raw.get("Attunement", ""),
            "damage":      raw.get("Damage", ""),
            "properties":  raw.get("Properties", ""),
            "mastery":     raw.get("Mastery", ""),
            "weight":      raw.get("Weight", ""),
            "tags":        raw.get("Tags", ""),
            "description": raw.get("Text", ""),
            "quantity":    "",
            "locked":      False,
            "_gallery":    True,   # suppresses the Reroll button
        }

        for w in self.gallery_inspect_frame.winfo_children():
            w.destroy()

        # Re-use the collapsed inspector renderer pointed at gallery_inspect_frame
        _real_frame = self.inspect_frame
        self.inspect_frame = self.gallery_inspect_frame
        self._render_inspect_collapsed(item)
        self.inspect_frame = _real_frame


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = ShopApp()
    app.mainloop()

if __name__ == "__main__":
    main()
