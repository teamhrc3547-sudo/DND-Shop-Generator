# DND-Shop-Generator

`>>>` teamhrc3527-sudo

## Features:

- Generate your own shops by type:
  - Magic Shop
  - General Store
  - Blacksmith
  - Armorer
  - Alchemy Store
- Scale inventory by settlement size and wealth
- Variety slider for cross-shop inventories
- Save shops by city
- Sort items by rarity, name, and price

- Items Included come from https://5e.tools/items.html
    - Includes items from all default sources in 5e.tools and *The Griffons Saddlebag Book 1-5*

## Getting Started:

Clone Repository:
```
git clone https://github.com/your-username/dnd-shop-generator.git
cd dnd-shop-generator
```
Install Dependencies:
```
pip install -r requirements.txt
```
Run Locally:
```
streamlit run app.py
```

### File Structure:
```
dnd-shop-generator/
│
├─> app.py
├─> shopgen.py
├─> requirements.txt
├─> README.md
|─> data/
│   ├─> magic_items.csv
│   ├─> general_store_items.csv
│   ├─> blacksmith_shop.csv
│   ├─> amorer_store.csv
│   └─> alchemy_store.csv
└─> saves/
```
