import random
import re

ITEM_TYPES = [
    (["пуховик", "puffer"], "puffer jacket"),
    (["куртка", "ветровка", "бомбер", "jacket"], "jacket"),
    (["худи", "толстовка", "hoodie"], "hoodie"),
    (["свитшот", "свитер", "джемпер", "зуди", "sweatshirt"], "sweatshirt"),
    (["лонгслив", "longsleeve"], "longsleeve top"),
    (["футболка", "поло", "майка", "tshirt", "t-shirt"], "t-shirt"),
    (["рубашка", "shirt"], "shirt"),
    (["штаны", "джинсы", "брюки", "pants", "jeans"], "trousers"),
    (["шорты", "shorts"], "shorts"),
    (["костюм", "suit"], "tracksuit set"),
    (["кроссовки", "sneakers"], "sneakers"),
    (["ботинки", "boots"], "boots"),
    (["слайды", "тапки", "slides"], "slides"),
    (["шапка", "кепка", "панама", "hat", "cap"], "hat"),
    (["рюкзак", "backpack"], "backpack"),
    (["сумка", "bag"], "bag"),
    (["кошелек", "кошелёк", "wallet"], "wallet"),
    (["ремень", "belt"], "belt"),
    (["шарф", "scarf"], "scarf"),
    (["перчатки", "gloves"], "gloves"),
    (["часы", "watch"], "watch"),
    (["очки", "glasses"], "sunglasses"),
]

MATERIAL_HINTS = [
    (["замша", "suede"], "suede-finish"),
    (["кожа", "кожан", "leather"], "leather-style"),
    (["шерсть", "wool"], "wool-blend"),
    (["велюр", "velour"], "velour"),
    (["флис", "fleece"], "fleece-lined"),
]

ADJ = [
    "Cozy", "Classic", "Oversized", "Relaxed", "Minimal", "Bold",
    "Chic", "Everyday", "Elevated", "Warm", "Clean", "Vintage-style",
    "Streetwear", "Effortless", "Timeless", "Easy", "Statement",
]

VIBE = [
    "streetwear essential", "winter must-have", "layering piece",
    "casual staple", "y2k outfit piece", "old money look",
    "street style favorite", "everyday wardrobe piece",
    "cold-weather essential", "aesthetic outfit idea",
]

DESC_OPENERS = [
    "Soft {mat}texture with a {fit} fit.",
    "Clean silhouette, easy to style for {occasion}.",
    "{Mat}design built for {occasion}.",
    "Relaxed cut with a comfortable, everyday feel.",
    "A versatile piece that works for {occasion}.",
    "Simple, well-made basic for daily rotation.",
]

DESC_CLOSERS = [
    "Pairs easily with jeans, joggers, or layered fits.",
    "Perfect for building a clean streetwear rotation.",
    "Great for layering in cooler weather.",
    "An easy addition to any casual wardrobe.",
    "Works for both daily wear and elevated street looks.",
    "A reliable basic for everyday outfits.",
]

OCCASIONS = [
    "everyday street looks", "casual outfits", "cool-weather styling",
    "layered fits", "a relaxed daily rotation", "street style basics",
]

FITS = ["relaxed", "oversized", "clean", "comfortable", "easy"]

KEYWORD_POOL = [
    "streetwear", "y2k", "old money", "aesthetic", "unisex fashion",
    "casual outfit", "street style", "oversized", "cozy fit",
    "minimal outfit", "everyday style", "winter fashion", "layering",
    "clean fit", "vintage inspired", "warm outfit",
]


def detect_item(name: str, material: str = "") -> str:
    text = f"{name} {material}".lower()
    for triggers, label in ITEM_TYPES:
        if any(t in text for t in triggers):
            return label
    return "streetwear piece"


def detect_material_hint(name: str, material: str = "") -> str:
    text = f"{name} {material}".lower()
    for triggers, hint in MATERIAL_HINTS:
        if any(t in text for t in triggers):
            return hint
    return ""


def generate_title(name: str, material: str = "", used_titles: set = None) -> str:
    item = detect_item(name, material)
    mat_hint = detect_material_hint(name, material)
    used_titles = used_titles or set()

    for _ in range(20):
        adj = random.choice(ADJ)
        vibe = random.choice(VIBE)
        if mat_hint and random.random() < 0.5:
            title = f"{adj} {mat_hint} {item}, {vibe}"
        else:
            title = f"{adj} {item}, {vibe}"
        title = title[0].upper() + title[1:]
        if title not in used_titles:
            return title
    return title


def _fix_article(text: str) -> str:
    return re.sub(r'\ba (?=[aeiouAEIOU])', 'an ', text)


def generate_description(name: str, material: str = "") -> str:
    mat_hint = detect_material_hint(name, material)
    mat_prefix = f"{mat_hint} " if mat_hint else ""
    mat_prefix_cap = mat_prefix[0].upper() + mat_prefix[1:] if mat_prefix else ""

    opener = random.choice(DESC_OPENERS).format(
        mat=mat_prefix, Mat=mat_prefix_cap or "Clean ",
        fit=random.choice(FITS), occasion=random.choice(OCCASIONS)
    )
    closer = random.choice(DESC_CLOSERS)
    return _fix_article(f"{opener} {closer}")


def generate_keywords(name: str, material: str = "") -> str:
    base = ["streetwear", "aesthetic"]
    extra = random.sample(KEYWORD_POOL, k=6)
    all_kw = list(dict.fromkeys(base + extra))
    return ", ".join(all_kw[:8])