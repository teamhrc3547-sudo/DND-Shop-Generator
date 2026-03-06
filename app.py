import os
import glob
import json
import io
import zipfile
from datetime import datetime
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from shopgen import (
    TownProfile,
    build_nonmagic_shop_inventory,
    build_magic_shop_inventory,
    reroll_slots,
    save_shop_instance,
    load_shop_instance,
    normalize_rarity,
)

# Optional: better row-click selection (click any cell) via AgGrid
HAS_AGGRID = False
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
    from st_aggrid.shared import JsCode
    HAS_AGGRID = True
except Exception:
    HAS_AGGRID = False


DATA_DIR = "data"
SAVE_DIR = "saves"


@st.cache_data
def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "id" not in df.columns and "name" in df.columns:
        df["id"] = df["name"].astype(str)
    return df


def default_shop_settings_for_settlement(settlement: str) -> Dict:
    """
    Defaults:
    - Non-magic shops: optional cross-shop variety mix (0-50%)
    - Magic shop: selective + varied (quota driven)
    """
    s = (settlement or "").lower()
    if s == "village":
        return {
            "variety_pct": 0,
            "magic_quotas": {"common": 3, "uncommon": 1, "rare": 0, "very rare": 0, "legendary": 0},
        }
    if s == "town":
        return {
            "variety_pct": 0,
            "magic_quotas": {"common": 5, "uncommon": 3, "rare": 1, "very rare": 0, "legendary": 0},
        }
    if s == "city":
        return {
            "variety_pct": 0,
            "magic_quotas": {"common": 6, "uncommon": 4, "rare": 2, "very rare": 1, "legendary": 0},
        }
    return {
        "variety_pct": 0,
        "magic_quotas": {"common": 7, "uncommon": 5, "rare": 3, "very rare": 1, "legendary": 1},
    }


def gp_to_denom(gp: float) -> str:
    """Format gp into gp/sp/cp without decimals (10 sp = 1 gp, 10 cp = 1 sp). Includes commas."""
    if gp is None or (isinstance(gp, float) and np.isnan(gp)):
        return "-"

    cp_total = int(round(float(gp) * 100))  # 1 gp = 100 cp
    sign = ""
    if cp_total < 0:
        sign = "-"
        cp_total = abs(cp_total)

    gp_i = cp_total // 100
    rem = cp_total % 100
    sp_i = rem // 10
    cp_i = rem % 10

    parts = []
    if gp_i:
        parts.append(f"{gp_i:,} gp")
    if sp_i:
        parts.append(f"{sp_i} sp")
    if cp_i:
        parts.append(f"{cp_i} cp")
    if not parts:
        parts.append("0 gp")
    return sign + " ".join(parts)


RARITY_TEXT_COLOR = {
    "common": "#D0D0D0",
    "mundane": "#A9A9A9",
    "uncommon": "#1DB954",   # green
    "rare": "#2F80ED",       # blue
    "very rare": "#9B51E0",  # purple
    "legendary": "#F2C94C",  # orange/yellow
    "artifact": "#EB5757",   # red
}


def rarity_text_color(rarity: str) -> str:
    r = normalize_rarity(rarity)
    return RARITY_TEXT_COLOR.get(r, "#3CCFCF")  # teal fallback


def style_rarity(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Color the Rarity *text* (not cell background). (Used for non-AgGrid fallback.)"""
    if "Rarity" not in df.columns:
        return df.style

    def _rarity_css(v: str) -> str:
        color = rarity_text_color(v)
        shadow = "text-shadow: 0 0 1px #000, 0 0 2px #000;" if color.upper() == "#FFFFFF" else ""
        return f"color: {color}; font-weight: 650; {shadow}"

    return df.style.applymap(_rarity_css, subset=["Rarity"])




def format_rarity(rarity: str) -> str:
    r = normalize_rarity(rarity)
    mapping = {
        "": "None",
        "none": "None",
        "mundane": "Mundane",
        "common": "Common",
        "uncommon": "Uncommon",
        "rare": "Rare",
        "very rare": "Very Rare",
        "legendary": "Legendary",
        "artifact": "Artifact",
    }
    return mapping.get(r, (str(rarity).strip().title() if str(rarity).strip() else "None"))


def rarity_sort_rank(rarity: str) -> int:
    r = normalize_rarity(rarity)
    order = {
        "": 0,
        "none": 0,
        "mundane": 0,
        "common": 1,
        "uncommon": 2,
        "rare": 3,
        "very rare": 4,
        "legendary": 5,
        "artifact": 6,
    }
    return order.get(r, 7)

def reroll_random_percent_slots(inv: dict, pct_low: float, pct_high: float, rng: np.random.Generator) -> Tuple[list, float]:
    slots = [sid for sid, e in inv.items() if not e.get("locked", False)]
    if not slots:
        return [], 0.0

    pct = float(rng.uniform(pct_low, pct_high))
    k = max(1, int(round(len(inv) * pct)))
    k = min(k, len(slots))
    chosen = rng.choice(np.array(slots), size=k, replace=False).tolist()
    return chosen, pct


def current_shop_payload(shop_name: str) -> dict:
    return {
        "id": None,
        "name": shop_name,
        "cityName": st.session_state.save_city_name,
        "shopType": st.session_state.shop_category,
        "town": {
            "settlement": st.session_state.town.settlement,
            "wealth": st.session_state.town.wealth,
        },
        "inventory": st.session_state.inventory,
        "magicQuotas": st.session_state.magic_quotas,
        "varietyPct": st.session_state.variety_pct,
        "discountMult": st.session_state.discount_mult,
    }


def make_backup_filename(prefix: str, suffix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = "_".join(str(prefix or "shop_backup").strip().split())
    return f"{safe_prefix}_{stamp}.{suffix}"


def build_all_saves_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = sorted(glob.glob(os.path.join(SAVE_DIR, "*.json")))
        for fp in files:
            if os.path.isfile(fp):
                zf.write(fp, arcname=os.path.basename(fp))
    buf.seek(0)
    return buf.getvalue()


st.set_page_config(page_title="D&D Shop Generator", layout="wide")
st.title("🛒 D&D Shop Generator")

# Sticky inspect panel CSS
st.markdown(
    """
<style>
.sticky-inspect {
    position: sticky;
    top: 1rem;
}

.inspect-card {
    border: 1px solid rgba(0, 0, 0, 0.10);
    border-radius: 14px;
    padding: 1rem 1.1rem;
    box-shadow: 0 6px 18px rgba(0,0,0,0.08);
    max-height: calc(100vh - 2rem);
    overflow-y: auto;
    background: rgba(255, 255, 255, 0.72);
    backdrop-filter: blur(4px);
}

.inspect-label {
    font-size: 0.88rem;
    font-weight: 700;
    margin-top: 0.8rem;
    margin-bottom: 0.15rem;
}

.inspect-value {
    margin-bottom: 0.35rem;
}

.inspect-desc {
    line-height: 1.45;
    white-space: pre-wrap;
}
</style>
""",
    unsafe_allow_html=True,
)

# Load datasets (each shop has its own table now)
magic_path = os.path.join(DATA_DIR, "magic_items.csv")
general_path = os.path.join(DATA_DIR, "general_store_items.csv")
blacksmith_path = os.path.join(DATA_DIR, "blacksmith_shop.csv")
armory_path = os.path.join(DATA_DIR, "amorer_store.csv")   # keeping your filename as-is
alchemy_path = os.path.join(DATA_DIR, "alchemy_store.csv")

missing = [p for p in [magic_path, general_path, blacksmith_path, armory_path, alchemy_path] if not os.path.exists(p)]
if missing:
    st.error(
        "Missing data files. Expected these CSVs in your `data/` folder:\n\n"
        + "\n".join([f"- {p}" for p in missing])
    )
    st.stop()

magic_df = load_csv(magic_path)
general_df = load_csv(general_path)
blacksmith_df = load_csv(blacksmith_path)
armory_df = load_csv(armory_path)
alchemy_df = load_csv(alchemy_path)

SHOP_TYPES = ["magic", "alchemy", "general", "blacksmith", "armory"]

# Session state
st.session_state.setdefault("inventory", {})
st.session_state.setdefault("shop_category", "magic")
st.session_state.setdefault("town", TownProfile(settlement="town", wealth="average"))
st.session_state.setdefault("magic_quotas", {"common": 5, "uncommon": 3, "rare": 1, "very rare": 0, "legendary": 0})
st.session_state.setdefault("discount_mult", 1.0)
st.session_state.setdefault("variety_pct", 0)
st.session_state.setdefault("save_city_name", "")
st.session_state.setdefault("selected_slot", None)


###############################################################################
# Sidebar
###############################################################################
with st.sidebar:
    st.header("⚙️ Controls")

    st.subheader("🛒 Shop")
    shop_category = st.selectbox(
        "Shop category",
        SHOP_TYPES,
        index=SHOP_TYPES.index(st.session_state.shop_category) if st.session_state.shop_category in SHOP_TYPES else 0,
        format_func=lambda s: s.title(),
    )
    st.session_state.shop_category = shop_category

    with st.expander("💰 Economy", expanded=True):
        settlement_options = ["village", "town", "city", "metropolis"]
        current_settlement = str(getattr(st.session_state.town, "settlement", "town"))
        current_settlement_key = current_settlement.strip().lower()
        settlement = st.selectbox(
            "Settlement",
            settlement_options,
            index=settlement_options.index(current_settlement_key) if current_settlement_key in settlement_options else 1,
            format_func=lambda s: s.title(),
        )

        wealth_options = ["poor", "average", "rich"]
        current_wealth = str(getattr(st.session_state.town, "wealth", "average"))
        current_wealth_key = current_wealth.strip().lower()
        wealth = st.selectbox(
            "Wealth",
            wealth_options,
            index=wealth_options.index(current_wealth_key) if current_wealth_key in wealth_options else 1,
            format_func=lambda s: s.title(),
        )

        discount_pct = st.slider(
            "Discount / markup",
            min_value=50,
            max_value=125,
            value=int(round(st.session_state.discount_mult * 100)),
            step=5,
            help="50% = half price. 125% = 25% markup. (5% increments)",
        )
        st.session_state.discount_mult = float(discount_pct) / 100.0

        prev_settlement = str(getattr(st.session_state.town, "settlement", "town")).strip().lower()

        # TownProfile is frozen -> rebuild it rather than mutating fields
        st.session_state.town = TownProfile(
            settlement=str(settlement).strip().lower(),
            wealth=str(wealth).strip().lower(),
        )

        if str(settlement).strip().lower() != prev_settlement:
            defaults = default_shop_settings_for_settlement(settlement)
            st.session_state.variety_pct = defaults["variety_pct"]
            st.session_state.magic_quotas = defaults["magic_quotas"]

    with st.expander("📦 Stock settings", expanded=True):
        if shop_category == "magic":
            q = dict(st.session_state.magic_quotas)
            for r in ["common", "uncommon", "rare", "very rare", "legendary"]:
                q[r] = st.slider(f"{format_rarity(r)} items", 0, 20, int(q.get(r, 0)))
            st.session_state.magic_quotas = q

        st.session_state.variety_pct = st.slider(
            "Variety",
            0,
            50,
            int(st.session_state.variety_pct),
            step=5,
            help="Adjusts the variety of the items in the pool, allowing for more than just the current shops' items to end up in the output table.",
        )

    with st.expander("🎲 Actions", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            gen = st.button("🎲 Generate", use_container_width=True)
        with col2:
            clear = st.button("🧹 Clear", use_container_width=True)

        reroll = st.button("♻️ Reroll 10–30%", use_container_width=True)

    with st.expander("💾 Save / Load", expanded=False):
        city_name = st.text_input("City / Location name", value=st.session_state.save_city_name)
        st.session_state.save_city_name = city_name

        shop_name = st.text_input("Shop name", value="My Shop")
        save_now = st.button("Save current shop", use_container_width=True)

        current_backup_data = json.dumps(current_shop_payload(shop_name), indent=2, ensure_ascii=False).encode("utf-8")
        st.download_button(
            "Download backup of current shop",
            data=current_backup_data,
            file_name=make_backup_filename(shop_name or "shop", "json"),
            mime="application/json",
            use_container_width=True,
            help="Download a JSON backup of the shop you currently have loaded.",
        )

        all_saves_exist = bool(glob.glob(os.path.join(SAVE_DIR, "*.json")))
        st.download_button(
            "Download backup of all saved shops",
            data=build_all_saves_zip_bytes() if all_saves_exist else b"",
            file_name=make_backup_filename("all_shop_backups", "zip"),
            mime="application/zip",
            use_container_width=True,
            disabled=not all_saves_exist,
            help="Download a ZIP containing every shop saved by this app instance.",
        )

###############################################################################
# Actions
###############################################################################
if clear:
    st.session_state.inventory = {}
    st.session_state.selected_slot = None

if gen:
    rng = np.random.default_rng()
    if shop_category == "magic":
        st.session_state.inventory = build_magic_shop_inventory(
            magic_df=magic_df,
            town=st.session_state.town,
            rng=rng,
            quotas=st.session_state.magic_quotas,
        )
    else:
        df_map = {
            "general": general_df,
            "blacksmith": blacksmith_df,
            "armory": armory_df,
            "alchemy": alchemy_df,
        }
        st.session_state.inventory = build_nonmagic_shop_inventory(
            shop_df=df_map.get(shop_category, general_df),
            town=st.session_state.town,
            rng=rng,
            base_rotating_slots=0,
            shop_label=shop_category,
            variety_pct=st.session_state.variety_pct,
            source_pools={"general": general_df, "blacksmith": blacksmith_df, "armory": armory_df, "alchemy": alchemy_df, "magic": magic_df},
        )

if reroll:
    if st.session_state.inventory:
        rng = np.random.default_rng()
        slots, pct = reroll_random_percent_slots(st.session_state.inventory, 0.10, 0.30, rng)

        st.session_state.inventory = reroll_slots(
            current_inventory=st.session_state.inventory,
            slots_to_reroll=slots,
            general_df=general_df,
            blacksmith_df=blacksmith_df,
            armory_df=armory_df,
            alchemy_df=alchemy_df,
            magic_df=magic_df,
            town=st.session_state.town,
            rng=rng,
            shop_type=st.session_state.shop_category,
            quotas=st.session_state.magic_quotas,
            variety_pct=st.session_state.variety_pct,
        )
        st.success(f"Rerolled {pct*100:.1f}% of the shop ({len(slots)} items) ✅")
    else:
        st.warning("Generate a shop first.")

if "save_now" in locals() and save_now:
    payload = current_shop_payload(shop_name)
    fp = save_shop_instance(SAVE_DIR, payload)
    st.success(f"Saved ✅ {fp}")


###############################################################################
# Load panel (grouped by city)
###############################################################################
def _list_saves() -> Dict[str, list]:
    files = sorted(glob.glob(os.path.join(SAVE_DIR, "*.json")))
    by_city: Dict[str, list] = {}
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                p = json.load(f)
            city = (p.get("cityName") or "(no city)").strip() or "(no city)"
            by_city.setdefault(city, []).append((fp, p.get("name") or os.path.basename(fp)))
        except Exception:
            by_city.setdefault("(unreadable)", []).append((fp, os.path.basename(fp)))
    return by_city


with st.sidebar:
    saves_by_city = _list_saves()
    if saves_by_city:
        st.divider()
        st.subheader("📂 Load")
        city_pick = st.selectbox("Load by city", list(saves_by_city.keys()))
        shop_pick = st.selectbox(
            "Saved shops",
            saves_by_city[city_pick],
            format_func=lambda t: t[1],
        )
        if st.button("Load selected", use_container_width=True):
            fp = shop_pick[0]
            payload = load_shop_instance(fp)

            st.session_state.shop_category = payload.get("shopType", "magic")
            st.session_state.save_city_name = payload.get("cityName", "")
            t = payload.get("town", {})
            st.session_state.town = TownProfile(
                settlement=t.get("settlement", "town"),
                wealth=t.get("wealth", "average"),
            )
            st.session_state.inventory = payload.get("inventory", {})
            st.session_state.magic_quotas = payload.get("magicQuotas", st.session_state.magic_quotas)
            st.session_state.variety_pct = int(payload.get("varietyPct", payload.get("nonmagicRotatingSlots", st.session_state.variety_pct)))
            st.session_state.discount_mult = float(payload.get("discountMult", st.session_state.discount_mult))
            st.session_state.selected_slot = None
            st.success("Loaded ✅")
    else:
        st.caption("No saves yet.")


###############################################################################
# Main
###############################################################################
inv = st.session_state.inventory
if not inv:
    st.info("Use **Generate** in the sidebar to create inventory.")
    st.stop()

st.subheader("📦 Inventory")

# Build display dataframe (slotId is kept internal only)
rows = []
for sid, e in inv.items():
    rows.append(
        {
            "slotId": sid,
            "Name": e.get("itemName", ""),
            "Rarity": format_rarity(e.get("rarity", "")),
            "raritySortRank": rarity_sort_rank(e.get("rarity", "")),
            "Cost (given)": e.get("csvValueGp", None),
            "Cost (DM's guide)": e.get("dmGuideGp", None),
            "Calculated price": e.get("calcGp", e.get("priceGp", None)),
            "Locked": bool(e.get("locked", False)),
        }
    )
df = pd.DataFrame(rows)

mult = float(st.session_state.discount_mult)
for c in ["Cost (given)", "Cost (DM's guide)", "Calculated price"]:
    df[c] = pd.to_numeric(df[c], errors="coerce") * mult

df_display = df.set_index("slotId")[["Name", "Rarity", "raritySortRank", "Cost (given)", "Cost (DM's guide)", "Calculated price", "Locked"]].copy()

# Pretty currency formatting (keep None -> '-')
for c in ["Cost (given)", "Cost (DM's guide)", "Calculated price"]:
    df_display[c] = df_display[c].apply(lambda x: gp_to_denom(x) if pd.notna(x) else "-")

# Layout: table on the left, inspect on the right
left, right = st.columns([1.8, 1.2], gap="large")

with left:
    st.caption(f"Prices shown include **{int(round(mult*100))}%** multiplier.")

    # Height to fit items (no vertical scroll area inside the table)
    row_h = 35
    height = int((len(df_display) + 1) * row_h + 3)

    selected_slot = st.session_state.get("selected_slot")

    if HAS_AGGRID:
        # Click-any-cell row selection ✅
        df_grid = df_display.reset_index()  # includes slotId column (we'll hide it)

        gb = GridOptionsBuilder.from_dataframe(df_grid)
        gb.configure_default_column(resizable=True, sortable=True, filter=False)

        # Hide internal id
        gb.configure_column("slotId", header_name="slotId", hide=True)
        gb.configure_column("raritySortRank", header_name="raritySortRank", hide=True)

        # Rarity text coloring
        rarity_js = JsCode(
            """
            function(params) {
              const v = (params.value || "").toString().trim().toLowerCase();
              const map = {
                "common": "#D0D0D0",
                "mundane": "#A9A9A9",
                "uncommon": "#1DB954",
                "rare": "#2F80ED",
                "very rare": "#9B51E0",
                "legendary": "#F2C94C",
                "artifact": "#EB5757"
              };
              const color = map[v] || "#3CCFCF";
              const shadow = (color.toUpperCase() === "#FFFFFF") ? "0 0 1px #000, 0 0 2px #000" : "none";
              return { color: color, fontWeight: 650, textShadow: shadow };
            }
            """
        )
        rarity_comparator_js = JsCode(
            """
            function(valueA, valueB, nodeA, nodeB) {
              const a = Number(nodeA && nodeA.data ? nodeA.data.raritySortRank : 999);
              const b = Number(nodeB && nodeB.data ? nodeB.data.raritySortRank : 999);
              return a - b;
            }
            """
        )
        gb.configure_column("Rarity", cellStyle=rarity_js, comparator=rarity_comparator_js)

        # Selection: single row, no checkbox column
        gb.configure_selection(selection_mode="single", use_checkbox=False)

        grid_options = gb.build()

        grid = AgGrid(
            df_grid,
            gridOptions=grid_options,
            height=height,
            fit_columns_on_grid_load=True,
            update_mode=GridUpdateMode.SELECTION_CHANGED,
            allow_unsafe_jscode=True,
        )

        selected_rows = grid.get("selected_rows")
        if selected_rows is None:
            selected_rows = []
        elif isinstance(selected_rows, pd.DataFrame):
            selected_rows = selected_rows.to_dict("records")
        if len(selected_rows) > 0:
            selected_slot = selected_rows[0].get("slotId")
    else:
        st.warning("Tip: install `streamlit-aggrid` to select an item by clicking **any cell** (better UX).")
        event = None
        try:
            event = st.dataframe(
                style_rarity(df_display),
                use_container_width=True,
                height=height,
                on_select="rerun",
                selection_mode="single-row",
            )
            if event is not None and getattr(event, "selection", None) is not None and event.selection.rows:
                sel = event.selection.rows[0]
                if isinstance(sel, (int, np.integer)):
                    idx = int(sel)
                    if 0 <= idx < len(df_display.index):
                        selected_slot = df_display.index[idx]
                else:
                    selected_slot = str(sel)
        except TypeError:
            st.dataframe(style_rarity(df_display), use_container_width=True, height=height)

    # Keep selection valid and default to first row if nothing is selected
    options = list(df_display.index)
    if options:
        if selected_slot not in inv:
            selected_slot = options[0]
        st.session_state.selected_slot = selected_slot
    else:
        st.session_state.selected_slot = None

with right:
    st.markdown("<div class='sticky-inspect'><div class='inspect-card'>", unsafe_allow_html=True)

    st.subheader("🔎 Inspect")
    sid = st.session_state.get("selected_slot")
    e = inv.get(sid, {}) if sid else {}

    if not e:
        st.info("Select an item to inspect.")
    else:
        details = e.get("details", {}) or {}

        name = e.get("itemName", "") or "Unnamed item"
        rarity_raw = e.get("rarity", "")
        rarity = format_rarity(rarity_raw) if rarity_raw not in {None, ""} else "-"
        item_type = details.get("type") or e.get("type") or "-"
        armor_class = details.get("armor_class") or "-"
        damage = details.get("damage") or "-"
        properties = details.get("properties") or "-"
        mastery = details.get("mastery") or "-"
        weight = details.get("weight") or "-"
        attunement = details.get("attunement") or "-"
        source = details.get("source") or "-"
        page = details.get("page")
        desc = details.get("description") or "(No description in the CSV)"

        st.markdown(f"## {name}")

        rcol = rarity_text_color(rarity)
        shadow = "text-shadow: 0 0 1px #000, 0 0 2px #000;" if rcol.upper() == "#FFFFFF" else ""
        st.markdown(
            f"<div style='font-weight:700; color:{rcol}; {shadow}; margin-bottom:0.5rem;'>Rarity: {rarity}</div>",
            unsafe_allow_html=True,
        )

        locked_now = bool(e.get("locked", False))
        new_locked = st.checkbox("🔒 Lock this item", value=locked_now)
        if new_locked != locked_now:
            inv[sid]["locked"] = bool(new_locked)
            st.session_state.inventory = inv
            st.rerun()

        csv_gp = e.get("csvValueGp", None)
        dmg_gp = e.get("dmGuideGp", None)
        calc_gp = e.get("calcGp", e.get("priceGp", None))

        def _fmt(v):
            try:
                return gp_to_denom(float(v) * mult) if v is not None else "-"
            except Exception:
                return "-"

        st.markdown("<div class='inspect-label'>Price (given)</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{_fmt(csv_gp)}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Price (DM's guide)</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{_fmt(dmg_gp)}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Calculated price</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{_fmt(calc_gp)}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Type</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{item_type}</div>", unsafe_allow_html=True)

        if armor_class != "-":
            st.markdown("<div class='inspect-label'>Armor Class</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='inspect-value'>{armor_class}</div>", unsafe_allow_html=True)

        if damage != "-":
            st.markdown("<div class='inspect-label'>Damage</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='inspect-value'>{damage}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Properties</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{properties}</div>", unsafe_allow_html=True)

        if mastery != "-":
            st.markdown("<div class='inspect-label'>Mastery</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='inspect-value'>{mastery}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Weight</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{weight}</div>", unsafe_allow_html=True)

        if attunement != "-":
            st.markdown("<div class='inspect-label'>Attunement</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='inspect-value'>{attunement}</div>", unsafe_allow_html=True)

        src_text = source if page is None else f"{source}, p. {page}"
        st.markdown("<div class='inspect-label'>Source</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-value'>{src_text}</div>", unsafe_allow_html=True)

        st.markdown("<div class='inspect-label'>Description</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='inspect-desc'>{desc}</div>", unsafe_allow_html=True)

    st.markdown("</div></div>", unsafe_allow_html=True)
