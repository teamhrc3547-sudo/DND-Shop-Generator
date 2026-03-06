# shopgen.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import json
import os
import uuid
from datetime import datetime
import re

import numpy as np
import pandas as pd


RARITIES = ["common", "uncommon", "rare", "very rare", "legendary"]
RARITY_INDEX = {r: i for i, r in enumerate(RARITIES)}


@dataclass(frozen=True)
class TownProfile:
    settlement: str   # village/town/city/metropolis
    wealth: str       # poor/average/rich
    party_level: Optional[int] = None  # back-compat only


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_rarity(x: str) -> str:
    """Normalize rarity strings to a consistent lowercase bucket."""
    if not isinstance(x, str):
        return ""
    x = x.strip().lower()
    x = x.replace("veryrare", "very rare")
    x = x.replace("very-rare", "very rare")
    x = x.replace(",", "")  # handle 'very rare,' etc.
    return x


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def parse_int_maybe(v) -> Optional[int]:
    """Safely parse integers from CSV fields (handles 'undefined', NaN, '', etc.)."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except Exception:
        pass
    if isinstance(v, (int, np.integer)):
        return int(v)
    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "undefined", "na", "n/a"}:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None


def parse_gp_value(v) -> Optional[float]:
    """
    Parse a GP value from a CSV 'value' field.
    Accepts:
      - numeric (already gp)
      - strings like '10 GP', '5 sp', '30 cp', '1,250 gp'
    Returns gp as float, or None if missing/unparseable.
    """
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except Exception:
        pass
    if isinstance(v, (int, np.integer, float)):
        return float(v)

    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "undefined", "na", "n/a", "-"}:
        return None

    s = s.replace(",", "")
    m = re.search(r"(-?\d+(\.\d+)?)\s*([a-zA-Z]+)?", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(3) or "gp").lower()

    # normalize pluralization / variants
    if unit in {"g", "gp", "gps"}:
        return num
    if unit in {"s", "sp", "sps"}:
        return num / 10.0
    if unit in {"c", "cp", "cps"}:
        return num / 100.0

    # If it's not a currency unit (e.g., 'lb.'), treat as missing
    return None


def clean_text(v) -> str:
    """Convert odd CSV values into readable text."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and np.isnan(v):
            return ""
    except Exception:
        pass
    s = str(v)
    if s.strip().lower() in {"nan", "none", "null", "undefined", "n/a", "na"}:
        return ""
    replacements = {
        "�": "",
        "â€”": "—",
        "â€“": "–",
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "Â": "",
    }
    for bad, good in replacements.items():
        s = s.replace(bad, good)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_optional_field(v) -> Optional[str]:
    s = clean_text(v)
    return s if s else None


def get_row_combat_value(row: pd.Series) -> tuple[Optional[str], Optional[str]]:
    raw = clean_optional_field(row.get("damage", row.get("damange", "")))
    if not raw:
        return None, None
    lower = raw.lower()
    if lower.startswith("ac") or "armor class" in lower:
        return None, raw
    return raw, None


###############################################################################
# Pricing
###############################################################################

# DMG-like price brackets (gp)
DMG_RANGES_GP = {
    "common": (50, 100),
    "uncommon": (101, 500),
    "rare": (501, 5000),
    "very rare": (5001, 50000),
    "legendary": (50001, 500000),
    "artifact": (100000, 1000000),
}


def dmg_random_price_gp(rarity: str, rng: np.random.Generator) -> int:
    """
    Sample a DMG-style price for a rarity bucket, clamped to a reasonable range.
    """
    r = normalize_rarity(rarity)
    lo, hi = DMG_RANGES_GP.get(r, (50, 500))

    mu = (lo + hi) / 2.0
    sigma = (hi - lo) / 6.0

    raw = float(rng.normal(mu, sigma))
    raw = clamp(raw, lo, hi)

    if raw < 200:
        return int(round(raw / 5.0) * 5)
    if raw < 2000:
        return int(round(raw / 10.0) * 10)
    return int(round(raw / 50.0) * 50)


# Custom potion pricing (gp) per your convention
POTION_RANGES_GP = {
    "common": (30, 50),
    "uncommon": (80, 100),
    "rare": (200, 500),
    "very rare": (2000, 4000),
    "legendary": (15000, 20000),
}


def potion_price_gp(rarity: str, rng: np.random.Generator) -> int:
    r = normalize_rarity(rarity)
    lo, hi = POTION_RANGES_GP.get(r, (30, 50))
    # uniform here feels more "shop-y" than normal sampling
    raw = float(rng.integers(int(lo), int(hi) + 1))
    # nice rounding
    if raw < 200:
        return int(round(raw / 5.0) * 5)
    if raw < 2000:
        return int(round(raw / 10.0) * 10)
    return int(round(raw / 50.0) * 50)


def wealth_multiplier(wealth: str) -> float:
    w = (wealth or "").strip().lower()
    return {"poor": 0.85, "average": 1.0, "rich": 1.15}.get(w, 1.0)


###############################################################################
# Utilities
###############################################################################

def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def pick_weighted(df: pd.DataFrame, n: int, rng: np.random.Generator, weight_col: Optional[str] = None) -> pd.DataFrame:
    if len(df) == 0 or n <= 0:
        return df.iloc[0:0]
    if weight_col and weight_col in df.columns:
        w_series = pd.to_numeric(df[weight_col], errors="coerce").fillna(1.0)
        w = w_series.astype(float).to_numpy()
        w = np.clip(w, 0.0, None)
        if w.sum() <= 0:
            w = None
    else:
        w = None

    idx = rng.choice(
        df.index.to_numpy(),
        size=min(n, len(df)),
        replace=False,
        p=(w / w.sum()) if w is not None else None,
    )
    return df.loc[idx]


###############################################################################
# Inventory builders
###############################################################################

def _pricing_for_source_row(
    row: pd.Series,
    *,
    source_label: str,
    town: TownProfile,
    rng: np.random.Generator,
) -> Tuple[str, Optional[int], Optional[int], Optional[float], int]:
    """Return rarity, DMG price, calc/list price, CSV gp, quantity for a source row."""
    source_label = (source_label or "general").strip().lower()
    csv_gp = parse_gp_value(row.get("value"))
    raw_rarity = normalize_rarity(row.get("rarity", row.get("rarity_norm", "")))

    if source_label == "magic":
        rarity = raw_rarity or "common"
        dm_gp = dmg_random_price_gp(rarity, rng)
        calc = (0.8 * float(csv_gp) + 0.2 * float(dm_gp)) if csv_gp is not None else float(dm_gp)
        calc *= wealth_multiplier(town.wealth)
        return rarity, int(dm_gp), int(round(calc)), csv_gp, 1

    if source_label == "alchemy":
        rarity = raw_rarity or "common"
        calc = potion_price_gp(rarity, rng)
        calc = int(round(calc * wealth_multiplier(town.wealth)))
        if rarity == "common":
            qty = int(rng.integers(2, 7))
        elif rarity == "uncommon":
            qty = int(rng.integers(1, 5))
        else:
            qty = 1
        return rarity, None, calc, csv_gp, qty

    if source_label in {"general", "blacksmith"}:
        rarity = "mundane" if raw_rarity in {"", "none"} else raw_rarity
        calc = int(round(csv_gp)) if csv_gp is not None else int(rng.integers(1, 101))
        qty = int(rng.integers(1, 9))
        return rarity, None, calc, csv_gp, qty

    if source_label == "armory":
        rarity = raw_rarity if raw_rarity else "none"
        if rarity in {"none", ""}:
            calc = int(round(csv_gp)) if csv_gp is not None else None
            return rarity, None, calc, csv_gp, int(rng.integers(1, 5))
        dm_gp = dmg_random_price_gp(rarity, rng)
        calc = (0.8 * float(csv_gp) + 0.2 * float(dm_gp)) if csv_gp is not None else float(dm_gp)
        calc *= wealth_multiplier(town.wealth)
        return rarity, int(dm_gp), int(round(calc)), csv_gp, 1

    rarity = raw_rarity or "mundane"
    calc = int(round(csv_gp)) if csv_gp is not None else int(rng.integers(1, 101))
    return rarity, None, calc, csv_gp, 1


def _source_mix_weights(current_label: str) -> Dict[str, float]:
    current_label = (current_label or "general").strip().lower()
    presets = {
        "general": {"blacksmith": 0.34, "armory": 0.30, "alchemy": 0.24, "magic": 0.12},
        "blacksmith": {"armory": 0.36, "general": 0.28, "alchemy": 0.22, "magic": 0.14},
        "armory": {"blacksmith": 0.32, "general": 0.24, "alchemy": 0.24, "magic": 0.20},
        "alchemy": {"magic": 0.34, "general": 0.24, "blacksmith": 0.22, "armory": 0.20},
        "magic": {"alchemy": 0.38, "armory": 0.24, "blacksmith": 0.20, "general": 0.18},
    }
    return presets.get(current_label, {"general": 0.30, "blacksmith": 0.25, "armory": 0.25, "alchemy": 0.15, "magic": 0.05})


def _add_cross_shop_variety(
    inv: Dict[str, dict],
    *,
    current_label: str,
    town: TownProfile,
    rng: np.random.Generator,
    variety_pct: int = 0,
    source_pools: Optional[Dict[str, pd.DataFrame]] = None,
    locked_slots: Optional[Dict[str, dict]] = None,
) -> Dict[str, dict]:
    """Replace some rotating slots with items sampled from other shop CSVs."""
    if not inv or not source_pools:
        return inv
    pct = max(0.0, min(0.50, float(variety_pct) / 100.0))
    if pct <= 0:
        return inv

    rotating_slots = [sid for sid in inv.keys() if sid.startswith("rotating:") and not inv[sid].get("locked")]
    if not rotating_slots:
        return inv

    replace_n = int(round(len(rotating_slots) * pct))
    replace_n = max(0, min(len(rotating_slots), replace_n))
    if replace_n <= 0:
        return inv

    slots_to_replace = list(rng.choice(np.array(rotating_slots, dtype=object), size=replace_n, replace=False))
    slot_order = {sid: i for i, sid in enumerate(slots_to_replace, start=1)}

    mix_weights = _source_mix_weights(current_label)
    source_names = []
    weights = []
    for name, wt in mix_weights.items():
        if name == current_label:
            continue
        df = source_pools.get(name)
        if df is None or len(df) == 0:
            continue
        source_names.append(name)
        weights.append(float(wt))
    if not source_names:
        return inv

    weights = np.array(weights, dtype=float)
    weights = weights / weights.sum()

    used_ids = {str(v.get("itemId", "")) for v in inv.values()}

    for sid in slots_to_replace:
        source_label = str(rng.choice(np.array(source_names, dtype=object), p=weights))
        pool = source_pools.get(source_label)
        if pool is None or len(pool) == 0:
            continue
        pool = pool.copy()
        if "id" not in pool.columns and "name" in pool.columns:
            pool["id"] = pool["name"].astype(str)
        available = pool[~pool["id"].astype(str).isin(used_ids)].copy()
        if len(available) == 0:
            available = pool.copy()

        weight_col = "weight" if "weight" in available.columns else None
        picked = pick_weighted(available, 1, rng, weight_col=weight_col)
        if len(picked) == 0:
            continue
        row = picked.iloc[0]
        rarity, dm_gp, calc_gp, csv_gp, qty = _pricing_for_source_row(row, source_label=source_label, town=town, rng=rng)

        inv[sid] = _entry_from_row(
            row,
            slot_id=sid,
            rarity=rarity,
            quantity=qty,
            csv_gp=csv_gp,
            dm_gp=dm_gp,
            calc_gp=calc_gp,
        )
        inv[sid]["slotId"] = sid
        inv[sid]["sourceShop"] = source_label
        used_ids.add(str(inv[sid].get("itemId", "")))

    return inv


def _entry_from_row(
    row: pd.Series,
    *,
    slot_id: str,
    rarity: str,
    quantity: int,
    csv_gp: Optional[float],
    dm_gp: Optional[int],
    calc_gp: Optional[int],
) -> dict:
    damage, armor_class = get_row_combat_value(row)
    return {
        "slotId": slot_id,
        "itemName": clean_text(row.get("name", "")),
        "itemId": clean_text(row.get("id", row.get("name", ""))),
        "rarity": clean_text(rarity),
        "type": clean_text(row.get("type", "Gear")),
        "quantity": int(quantity),
        "csvValueGp": float(csv_gp) if csv_gp is not None else None,
        "dmGuideGp": int(dm_gp) if dm_gp is not None else None,
        "calcGp": int(calc_gp) if calc_gp is not None else None,
        "priceGp": int(calc_gp) if calc_gp is not None else None,
        "locked": False,
        "details": {
            "description": clean_text(row.get("text", "")),
            "source": clean_text(row.get("source", "")),
            "page": parse_int_maybe(row.get("page")),
            "attunement": clean_optional_field(row.get("attunement", row.get("attunment", ""))),
            "type": clean_optional_field(row.get("type", "")),
            "damage": damage,
            "armor_class": armor_class,
            "properties": clean_optional_field(row.get("properties", "")),
            "mastery": clean_optional_field(row.get("mastery", "")),
            "weight": clean_optional_field(row.get("weight", "")),
        },
    }


def build_alchemy_shop_inventory(
    shop_df: pd.DataFrame,
    town: TownProfile,
    rng: np.random.Generator,
    *,
    locked_slots: Optional[Dict[str, dict]] = None,
) -> Dict[str, dict]:
    """
    Alchemy behavior:
    - Total stock (randomized): ~20 in village, up to ~60 in metropolis
    - Always stocks the healing potion line if present
    - Uses custom potion pricing ranges (not DMG brackets)
    """
    inv: Dict[str, dict] = {}
    locked_slots = locked_slots or {}

    df = shop_df.copy()
    if "id" not in df.columns:
        df["id"] = df["name"].astype(str)

    df["rarity_norm"] = df.get("rarity", "").apply(normalize_rarity)

    settlement = (town.settlement or "town").strip().lower()
    ranges = {
        "village": (18, 22),
        "town": (28, 40),
        "city": (45, 55),
        "metropolis": (58, 62),
    }
    lo, hi = ranges.get(settlement, (28, 40))
    target = int(rng.integers(lo, hi + 1))

    # Always include healing potions if present
    heals = df[df["name"].astype(str).str.contains("healing", case=False, na=False)].copy()
    heals = heals.sort_values("rarity_norm")  # common -> ...
    heal_rows = heals.to_dict(orient="records")

    chosen_ids = set()
    slot_i = 0

    for r in heal_rows:
        row = pd.Series(r)
        slot_i += 1
        slot_id = f"always:alchemy:heal:{row['id']}"
        chosen_ids.add(row["id"])

        if slot_id in locked_slots:
            inv[slot_id] = locked_slots[slot_id]
            continue

        rar = normalize_rarity(row.get("rarity", ""))
        qty = 1
        if rar == "common":
            qty = int(rng.integers(3, 9))
        elif rar == "uncommon":
            qty = int(rng.integers(2, 6))
        else:
            qty = 1

        price = potion_price_gp(rar or "common", rng)
        price = int(round(price * wealth_multiplier(town.wealth)))

        inv[slot_id] = _entry_from_row(
            row,
            slot_id=slot_id,
            rarity=rar if rar else "common",
            quantity=qty,
            csv_gp=parse_gp_value(row.get("value")),
            dm_gp=None,
            calc_gp=price,
        )

    remaining = max(0, target - len(inv))

    pool = df[~df["id"].isin(chosen_ids)].copy()
    # Favor actual potions (type contains 'potion') but don't hard-require
    if "type" in pool.columns:
        potion_mask = pool["type"].astype(str).str.contains("potion", case=False, na=False)
        potion_pool = pool[potion_mask]
        if len(potion_pool) >= max(10, remaining // 2):
            pool = potion_pool

    picked = pick_weighted(pool, remaining, rng, weight_col="weight" if "weight" in pool.columns else None)

    for _, row in picked.iterrows():
        slot_i += 1
        slot_id = f"rotating:alchemy:{slot_i}"
        if slot_id in locked_slots:
            inv[slot_id] = locked_slots[slot_id]
            continue

        rar = normalize_rarity(row.get("rarity", ""))
        price = potion_price_gp(rar or "common", rng)
        price = int(round(price * wealth_multiplier(town.wealth)))

        inv[slot_id] = _entry_from_row(
            row,
            slot_id=slot_id,
            rarity=rar if rar else "common",
            quantity=1,
            csv_gp=parse_gp_value(row.get("value")),
            dm_gp=None,
            calc_gp=price,
        )

    return inv


def build_nonmagic_shop_inventory(
    shop_df: pd.DataFrame,
    town: TownProfile,
    rng: np.random.Generator,
    base_rotating_slots: int,
    *,
    staple_rarities: Optional[List[str]] = None,
    rotating_rarities_by_settlement: Optional[Dict[str, List[str]]] = None,
    locked_slots: Optional[Dict[str, dict]] = None,
    qty_range: tuple[int, int] = (2, 15),
    shop_label: str = "general",
    variety_pct: int = 0,
    source_pools: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, dict]:
    """
    Non-magic shops.
    - General/Blacksmith: sample a target percentage of the CSV so stock feels broad but still unique.
    - Armory: keep the existing rarity-aware pricing behavior.
    - Alchemy: special potion logic.
    """
    label = (shop_label or "general").strip().lower()

    if label == "alchemy":
        inv = build_alchemy_shop_inventory(shop_df, town, rng, locked_slots=locked_slots)
        return _add_cross_shop_variety(inv, current_label=label, town=town, rng=rng, variety_pct=variety_pct, source_pools=source_pools, locked_slots=locked_slots)

    inv: Dict[str, dict] = {}
    locked_slots = locked_slots or {}

    df = shop_df.copy()
    if "id" not in df.columns:
        df["id"] = df["name"].astype(str)

    df["rarity_norm"] = df.get("rarity", "").apply(normalize_rarity)

    def _rarity_for_row(rnorm: str) -> str:
        rnorm = normalize_rarity(rnorm)
        if label in {"general", "blacksmith"}:
            return "mundane" if rnorm in {"", "none"} else rnorm
        if label == "armory":
            return rnorm if rnorm else "none"
        return rnorm or "mundane"

    def _pricing_for_row(rarity: str, csv_gp: Optional[float]) -> tuple[Optional[int], Optional[int]]:
        if label in {"general", "blacksmith"}:
            if csv_gp is not None:
                return None, int(round(csv_gp))
            fallback = float(rng.integers(1, 101))
            return None, int(round(fallback))

        if label == "armory":
            r = normalize_rarity(rarity)
            if r in {"none", ""}:
                if csv_gp is not None:
                    return None, int(round(csv_gp))
                return None, None
            dm = dmg_random_price_gp(r, rng)
            if csv_gp is not None:
                calc = 0.8 * float(csv_gp) + 0.2 * float(dm)
            else:
                calc = float(dm)
            calc *= wealth_multiplier(town.wealth)
            return int(dm), int(round(calc))

        if csv_gp is not None:
            return None, int(round(csv_gp))
        return None, int(round(float(rng.integers(1, 101))))

    if label in {"general", "blacksmith"}:
        settlement = (town.settlement or "town").strip().lower()
        pct_ranges = {
            "village": (0.60, 0.65),
            "town": (0.65, 0.75),
            "city": (0.75, 0.85),
            "metropolis": (0.85, 0.90),
        }
        wealth_bonus = {
            "poor": 0.00,
            "average": 0.025,
            "rich": 0.05,
        }.get((town.wealth or "average").strip().lower(), 0.025)

        lo, hi = pct_ranges.get(settlement, (0.65, 0.75))
        target_pct = min(1.0, float(rng.uniform(lo, hi)) + wealth_bonus)
        target_n = max(1, min(len(df), int(round(len(df) * target_pct))))

        picked = pick_weighted(df, target_n, rng, weight_col="weight" if "weight" in df.columns else None)

        for i, (_, row) in enumerate(picked.iterrows(), start=1):
            slot_id = f"rotating:{label}:{i}"
            if slot_id in locked_slots:
                inv[slot_id] = locked_slots[slot_id]
                continue

            csv_gp = parse_gp_value(row.get("value"))
            rarity = _rarity_for_row(row.get("rarity_norm", row.get("rarity", "")))
            dm_gp, calc_gp = _pricing_for_row(rarity, csv_gp)
            qty = int(rng.integers(qty_range[0], qty_range[1] + 1))

            inv[slot_id] = _entry_from_row(
                row,
                slot_id=slot_id,
                rarity=rarity,
                quantity=qty,
                csv_gp=csv_gp,
                dm_gp=dm_gp,
                calc_gp=calc_gp,
            )

        return _add_cross_shop_variety(inv, current_label=label, town=town, rng=rng, variety_pct=variety_pct, source_pools=source_pools, locked_slots=locked_slots)

    if staple_rarities is None:
        staple_rarities = ["common", "uncommon", "mundane", "", "none"]

    if rotating_rarities_by_settlement is None:
        rotating_rarities_by_settlement = {
            "village": ["common", "uncommon", "none", ""],
            "town": ["common", "uncommon", "none", ""],
            "city": ["common", "uncommon", "rare", "none", ""],
            "metropolis": ["common", "uncommon", "rare", "none", ""],
        }

    staples = df[df["rarity_norm"].isin(staple_rarities)].copy()

    for _, row in staples.iterrows():
        slot_id = f"always:{label}:{row['id']}"
        if slot_id in locked_slots:
            inv[slot_id] = locked_slots[slot_id]
            continue

        csv_gp = parse_gp_value(row.get("value"))
        rarity = _rarity_for_row(row.get("rarity_norm", row.get("rarity", "")))
        dm_gp, calc_gp = _pricing_for_row(rarity, csv_gp)
        qty = int(rng.integers(qty_range[0], qty_range[1] + 1))

        inv[slot_id] = _entry_from_row(
            row,
            slot_id=slot_id,
            rarity=rarity,
            quantity=qty,
            csv_gp=csv_gp,
            dm_gp=dm_gp,
            calc_gp=calc_gp,
        )

    size_bonus = {"village": 0, "town": 8, "city": 14, "metropolis": 20}.get((town.settlement or "").lower(), 8)
    k = max(0, int(base_rotating_slots) + size_bonus)

    allowed = rotating_rarities_by_settlement.get((town.settlement or "").lower(), ["common", "uncommon", "none", ""])
    pool = df[df["rarity_norm"].isin(allowed)].copy()
    picked = pick_weighted(pool, k, rng, weight_col="weight" if "weight" in pool.columns else None)

    for i, (_, row) in enumerate(picked.iterrows(), start=1):
        slot_id = f"rotating:{label}:{i}"
        if slot_id in locked_slots:
            inv[slot_id] = locked_slots[slot_id]
            continue

        csv_gp = parse_gp_value(row.get("value"))
        rarity = _rarity_for_row(row.get("rarity_norm", row.get("rarity", "")))
        dm_gp, calc_gp = _pricing_for_row(rarity, csv_gp)
        qty = int(rng.integers(1, max(2, qty_range[1] // 2) + 1))

        inv[slot_id] = _entry_from_row(
            row,
            slot_id=slot_id,
            rarity=rarity,
            quantity=qty,
            csv_gp=csv_gp,
            dm_gp=dm_gp,
            calc_gp=calc_gp,
        )
    return _add_cross_shop_variety(inv, current_label=label, town=town, rng=rng, variety_pct=variety_pct, source_pools=source_pools, locked_slots=locked_slots)


def build_magic_shop_inventory(
    magic_df: pd.DataFrame,
    town: TownProfile,
    rng: np.random.Generator,
    quotas: Dict[str, int],
    locked_slots: Optional[Dict[str, dict]] = None,
    variety_pct: int = 0,
    source_pools: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, dict]:
    """
    Magic shop behavior:
    - Selective quotas by rarity (varied each generation)
    - Price uses CSV value blended with DMG bracket sample + wealth nudge
    """
    inv: Dict[str, dict] = {}
    locked_slots = locked_slots or {}

    df = magic_df.copy()
    if "id" not in df.columns:
        df["id"] = df["name"].astype(str)

    df["rarity_norm"] = df["rarity"].apply(normalize_rarity)

    slot_counter = 0
    for rarity, count in quotas.items():
        rarity = normalize_rarity(rarity)
        if count <= 0:
            continue

        pool = df[df["rarity_norm"].eq(rarity)]
        picked = pick_weighted(pool, count, rng, weight_col="weight" if "weight" in pool.columns else None)

        for _, row in picked.iterrows():
            slot_counter += 1
            slot_id = f"rotating:magic:{rarity}:{slot_counter}"

            if slot_id in locked_slots:
                inv[slot_id] = locked_slots[slot_id]
                continue

            csv_gp = parse_gp_value(row.get("value"))
            dm_gp = dmg_random_price_gp(rarity if rarity else row.get("rarity", ""), rng)

            if csv_gp is not None:
                calc = 0.8 * float(csv_gp) + 0.2 * float(dm_gp)
            else:
                calc = float(dm_gp)

            calc *= wealth_multiplier(town.wealth)
            list_gp = int(round(calc))
            inv[slot_id] = _entry_from_row(
                row,
                slot_id=slot_id,
                rarity=rarity if rarity else normalize_rarity(row.get("rarity", "")),
                quantity=1,
                csv_gp=csv_gp,
                dm_gp=dm_gp,
                calc_gp=list_gp,
            )

    return inv


###############################################################################
# Rerolling
###############################################################################

def reroll_slots(
    current_inventory: Dict[str, dict],
    slots_to_reroll: List[str],
    *,
    general_df: Optional[pd.DataFrame],
    blacksmith_df: Optional[pd.DataFrame],
    armory_df: Optional[pd.DataFrame],
    alchemy_df: Optional[pd.DataFrame],
    magic_df: Optional[pd.DataFrame],
    town: TownProfile,
    rng: np.random.Generator,
    shop_type: str,
    quotas: Dict[str, int],
    variety_pct: int,
) -> Dict[str, dict]:
    """Reroll only specified slots (unless locked)."""
    inv = dict(current_inventory)
    locked = {sid: entry for sid, entry in inv.items() if entry.get("locked")}

    for sid in slots_to_reroll:
        if sid in locked:
            continue
        inv.pop(sid, None)

    stype = (shop_type or "").strip().lower()
    if stype == "magic":
        fresh = build_magic_shop_inventory(
            magic_df=magic_df if magic_df is not None else pd.DataFrame(),
            town=town,
            rng=rng,
            quotas=quotas,
            locked_slots=locked,
        )
    else:
        df_map = {
            "general": general_df,
            "blacksmith": blacksmith_df,
            "armory": armory_df,
            "alchemy": alchemy_df,
        }
        chosen_df = df_map.get(stype, None)
        if chosen_df is None:
            chosen_df = general_df
        if chosen_df is None:
            chosen_df = pd.DataFrame()
        fresh = build_nonmagic_shop_inventory(
            shop_df=chosen_df,
            town=town,
            rng=rng,
            base_rotating_slots=0,
            locked_slots=locked,
            shop_label=stype or "general",
            variety_pct=variety_pct,
            source_pools={"general": general_df if general_df is not None else pd.DataFrame(), "blacksmith": blacksmith_df if blacksmith_df is not None else pd.DataFrame(), "armory": armory_df if armory_df is not None else pd.DataFrame(), "alchemy": alchemy_df if alchemy_df is not None else pd.DataFrame(), "magic": magic_df if magic_df is not None else pd.DataFrame()},
        )

    for sid in slots_to_reroll:
        if sid in locked:
            inv[sid] = locked[sid]
            continue
        if sid in fresh:
            inv[sid] = fresh[sid]
        else:
            candidates = [k for k in fresh.keys() if k.startswith("rotating:") and k not in inv]
            if candidates:
                repl = candidates[0]
                inv[sid] = fresh[repl]
                inv[sid]["slotId"] = sid

    return inv


###############################################################################
# Save / load
###############################################################################

def save_shop_instance(path: str, payload: dict) -> str:
    os.makedirs(path, exist_ok=True)

    shop_id = payload.get("id") or str(uuid.uuid4())
    payload["id"] = shop_id
    payload.setdefault("savedAt", _now_iso())

    city_slug = _slug(payload.get("cityName", ""))
    shop_slug = _slug(payload.get("name", ""))
    fp = os.path.join(path, f"{city_slug}__{shop_slug}__{shop_id[:8]}.json")

    with open(fp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return fp


def load_shop_instance(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)
