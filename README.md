# D&D Shop Generator

---
#### `>>>` teamHRC3547-sudo
---

## File Structure

```
Folder Name
├── DND_ShopGen... .py         # Python App
└── Items_Beta.csv             # Item Database

```
---

## Generating a Shop

1. Select a **Shop Type** from the dropdown (Alchemy, Armory, Magic, etc.)
2. Click **Name** to generate a random shop name, or type your own
3. Set **City Size** and **Wealth** in the Stock Settings tab
4. Include or exclude any particular tags based on the desired shop
5. Click **Generate Shop**

---

## Features

### Shop Types
Alchemy · Armory · Blacksmith · Fletcher & Bowyer · General Store · Jeweler & Curiosities · Magic · Scribe & Scroll · Stables & Outfitter · Tavern & Inn

### City Size
Controls how many items appear and quantity of each:
| Size | Item Count |
|---|---|
| Village | 10–15 |
| Town | 15–25 |
| City | 25–35 |
| Metropolis | 35–60 |

### Wealth-Rarity Disribution
| Wealth | Common | Uncommon | Rare | Very Rare | Legendary | Artifact |
|---|---|---|---|---|---|---|
| Poor | 55% | 30% | 15% | 0% | 0% | 0% |
| Average | 40% | 30% | 24% | 5% | 1% | 0% |
| Rich | 30% | 25% | 20% | 15% | 10% | 0% |

---

### Item Tags
All shops can be customized to include, or exclude certain items based on its tags. All tags are listed below

##### **Race/Culture:** 
###### Drow, Draconic, Dwarven, Elven, Fey, Fiendish, Giant

##### **Element/Damage Type:** 
###### Acid, Fire, Force, Ice/Cold, Lightning, Necrotic, Poison, Psychic, Radiant, Thunder, Slashing, Piercing, Bludgeoning 

##### **Type(Taken from 5e.tools):** 
###### Adventuring Gear, Ammunition, Artisans, Tools, Amulet/Necklace, Belt, Book/Tome, Boots/Footwear, Card/Deck, Cloak, Dust/Powder, Figurine, Food/Drink, Gloves/Bracers, Headwear, Instrument, Potion, Ring, Rod, Scroll, Staff, Tattoo, Tools, Wand, Other, Trade Good, Spellcasting Focus, Wonderous

##### **Weapon & Armor:** 
###### Armor, Finesse, Generic Variant, Heavy Armor, Heavy Weapon, Light Armor, Light Weapon, Medium Armor, Melee, Ranged Weapon, Shield, Thrown, Two-Handed, Versatile, Weapon

##### **Rarity:** 
###### Artifact, Common, Legendary, Mundane, Rare, Uncommon, Very Rare

---

### Quantity System
Each item gets a quantity based on `ceiling(size_mod × weight + 1)`:
- `size_mod` is a random float drawn from a city + rarity range table below
- `weight` (0–3) is inferred from the item — consumables stack most, legendary items are always singular


Size Mod Table

| Rarity | Village | Town | City | Metropolis |
|---|---|---|---|---|
| Mundane | 0 – 5 | 0 – 10 | 2 – 15 | 5 – 30 |
| Common | 0 – 2 | 0 – 4 | 1 – 5 | 3 – 15 |
| Uncommon | 0 | 0 – 2 | 1 – 4 | 2 – 6 |
| Rare | 0 | 0 | 0 – 3 | 0 – 5 |
| Very Rare | 0 | 0 | 0 – 1 | 0 – 1 |
| Legendary | 0 | 0 | 0 | 0 |

---

### Price Modifier
Slider in the Action tab adjusts all displayed prices from 50% to 125% of list price. Useful for haggling, special sales, or greedy shopkeepers.

### Item Locking
Double-click any item to lock it. Locked items survive rerolls and regeneration in case you want to save it for your party later.

### Reroll Button
Replaces 10–30% of unlocked items with fresh picks, keeping the shop feeling dynamic between visits.

### Sell Item Tab
Look up any item for a player to sell it back to the shopkeeper. Default buy-back percentage is 80%.

### Campaigns & Saves
Save shops to a local SQLite database organized by Campaign -> Town-> Shop. Load, export to JSON, or import from JSON for sharing or backups.

### Item Gallery
Browse the full item database with search, rarity filter, source filter, and tag filters.

---

## Item Sources
A big thanks to [5e.tools](https://5e.tools/), all data was taken from their items database, using all default sourcebooks as well as a popular homebrew source, *The Griffons Saddlebag* Books 1-5 (TGS1-5).

---
