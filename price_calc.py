"""Локальный расчёт цены из закупочной стоимости в юанях.

Модуль намеренно не делает сетевых запросов: курс хранится в небольшом
локальном JSON-файле и применяется синхронно во время создания превью.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
import json
import logging
import os
import re


logger = logging.getLogger(__name__)

DEFAULT_RATE = Decimal("12")
RATE_FILE = "price_settings.json"


class PriceCalculationError(ValueError):
    """Ошибка, которую можно показать пользователю до публикации."""


@dataclass(frozen=True)
class PriceRule:
    label: str
    slope: Decimal
    base: Decimal
    rounding: int
    modifiers: dict[str, Decimal]


@dataclass(frozen=True)
class DeliveryPriceRule:
    label: str
    delivery_yuan: Decimal
    profit_rubles: Decimal
    rounding: int


@dataclass(frozen=True)
class ParsedPrice:
    value: int | Decimal | None
    mode: str | None


@dataclass(frozen=True)
class Calculation:
    price: int
    category_key: str
    category_label: str
    purchase_yuan: Decimal
    rate: Decimal
    raw_price: Decimal
    modifiers: tuple[str, ...]


def _rule(slope: str, base: str, rounding: int, label: str,
          modifiers: dict[str, str] | None = None) -> PriceRule:
    return PriceRule(
        label=label,
        slope=Decimal(slope),
        base=Decimal(base),
        rounding=rounding,
        modifiers={
            name: Decimal(value)
            for name, value in (modifiers or {}).items()
        },
    )


PRICE_RULES: dict[str, PriceRule] = {
    # Одежда
    "hoodie": _rule("8", "2090", 90, "Худи / толстовки", {"шерсть": "400"}),
    "sweatshirt": _rule("8", "1890", 90, "Свитшоты / свитера / джемперы", {"шерсть": "400"}),
    "longsleeve": _rule("8", "1490", 90, "Лонгсливы"),
    "tshirt": _rule("8", "1500", 90, "Футболки / поло", {"майка": "250"}),
    "shirt": _rule("8", "1800", 90, "Рубашки"),
    "pants": _rule("5", "3540", 90, "Штаны / джинсы"),
    "shorts": _rule("8", "1600", 90, "Шорты"),
    # Верхняя одежда
    "heavy_jacket": _rule("0.3", "14500", 90, "Куртки тяжёлые"),
    "light_winter_jacket": _rule("2", "8200", 90, "Пуховики лёгкие / куртки зимние"),
    "fall_outerwear": _rule("5", "3000", 90, "Джинсовки / бомберы / жилеты осенние"),
    "windbreaker": _rule("5", "2800", 90, "Ветровки / анораки"),
    # Обувь
    "sneakers": _rule("1.4", "4480", 90, "Кроссовки", {"люкс": "2500", "тряпка": "-400"}),
    "boots": _rule("1.4", "5080", 90, "Ботинки", {"люкс": "2500", "тряпка": "-400"}),
    "slides": _rule("1.4", "3640", 90, "Слайды / тапки", {"тряпка": "-400"}),
    # Аксессуары
    "hats": _rule("8", "600", 90, "Шапки / кепки / панамы"),
    "socks": _rule("6", "1080", 10, "Носки (комплект)"),
    "belts": _rule("6", "2700", 10, "Ремни"),
    "backpacks": _rule("6", "2340", 10, "Рюкзаки"),
    "underwear": _rule("6", "1620", 10, "Трусы (комплект)"),
    "scarves": _rule("6", "2160", 10, "Шарфы"),
    "gloves": _rule("6", "1260", 10, "Перчатки"),
    "watches": _rule("6", "2160", 10, "Часы"),
    # Сумки, украшения и особые
    "bags": _rule("4", "3500", 90, "Сумки женские / кроссбоди"),
    "wallets": _rule("0", "2520", 90, "Кошельки / картхолдеры"),
    "jewelry": _rule("0", "2160", 90, "Украшения"),
    "suits": _rule("8", "4090", 90, "Костюмы (худи + штаны)"),
    "umbrellas": _rule("6", "1500", 10, "Зонты"),
    "glasses": _rule("6", "1500", 10, "Очки"),
}

FIXED_PURCHASE_COST_YUAN = Decimal("40")

DELIVERY_PRICE_RULES: dict[str, DeliveryPriceRule] = {
    "leather_outerwear": DeliveryPriceRule(
        label="Замшевые / кожаные куртки",
        delivery_yuan=Decimal("150"),
        profit_rubles=Decimal("4250"),
        rounding=90,
    ),
    "puffer_jacket": DeliveryPriceRule(
        label="Пуховики",
        delivery_yuan=Decimal("175"),
        profit_rubles=Decimal("8000"),
        rounding=90,
    ),
}


YUAN_PATTERN = re.compile(
    r"(?ix)"
    r"(?:[¥￥]\s*([0-9]+(?:[.,][0-9]+)?)"
    r"|([0-9]+(?:[.,][0-9]+)?)\s*(?:[¥￥]|cny|юан(?:ь|я|ей|и)?))"
)
YUAN_MARKER_PATTERN = re.compile(r"(?i)[¥￥]|cny|юан")
PLAIN_NUMBER_PATTERN = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*$")


def parse_price_line(line: str) -> ParsedPrice:
    """Разбирает одну строку цены, не смешивая юани с рублями."""
    text = str(line or "").strip()
    yuan_match = YUAN_PATTERN.search(text)
    if yuan_match:
        raw = yuan_match.group(1) or yuan_match.group(2)
        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return ParsedPrice(None, "yuan")
        return ParsedPrice(value if value > 0 else None, "yuan")

    if YUAN_MARKER_PATTERN.search(text):
        # Явный знак юаня был, но число не распознано: не принимаем строку
        # за рубли и не позволяем опубликовать товар с неверной ценой.
        return ParsedPrice(None, "yuan")

    plain_match = PLAIN_NUMBER_PATTERN.fullmatch(text)
    if plain_match:
        raw = plain_match.group(1).replace(",", ".")
        integer_part = raw.split(".", 1)[0]
        digits_count = len(integer_part)
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return ParsedPrice(None, None)
        if value <= 0:
            return ParsedPrice(None, None)
        # В короткой записи знак валюты не нужен:
        # 1–3 цифры — закупка в юанях, 4–6 цифр — готовая цена в рублях.
        if digits_count <= 3:
            return ParsedPrice(value, "yuan")
        if 4 <= digits_count <= 6 and value == value.to_integral_value():
            return ParsedPrice(int(value), "rubles")
    return ParsedPrice(None, None)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").lower().replace("ё", "е")).strip()


def _has(text: str, *parts: str) -> bool:
    normalized = _normalize(text)
    return any(part in normalized for part in parts)


LEATHER_OUTERWEAR_TRIGGERS = (
    "замша", "замшевая", "замшевый",
    "дубленка",
    "кожа", "кожаная", "кожаный", "кожаное",
)


def _detect_from_text(text: str) -> str | None:
    """Определяет тип вещи по наиболее специфичным словам."""
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("heavy_jacket", (
            "canada goose", "canada", "the north face", "tnf",
            "тяжелая куртка", "тяжелая",
            *LEATHER_OUTERWEAR_TRIGGERS,
        )),
        ("light_winter_jacket", ("пуховик", "куртка зимняя", "зимняя куртка")),
        ("fall_outerwear", ("бомбер", "джинсовка", "жилет")),
        ("windbreaker", ("ветровка", "анорак")),
        ("boots", ("ботинок", "ботинки", "ботинк")),
        ("slides", ("слайды", "тапки", "тапочки", "сланцы", "шлепан")),
        ("sneakers", ("кроссов", "сникер", "кед", "обувь")),
        ("sweatshirt", ("свитшот", "свитер", "джемпер")),
        ("hoodie", ("худи", "толстовк")),
        ("longsleeve", ("лонгслив",)),
        ("shirt", ("рубашк",)),
        ("tshirt", ("футболк", "поло")),
        ("pants", ("штаны", "брюк", "джинс")),
        ("underwear", ("трус",)),
        ("shorts", ("шорт",)),
        ("scarves", ("шарф",)),
        ("hats", ("шапк", "кепк", "панам")),
        ("socks", ("носк",)),
        ("belts", ("ремн",)),
        ("backpacks", ("рюкзак",)),
        ("gloves", ("перчат",)),
        ("watches", ("часы", "часов")),
        ("umbrellas", ("зонт",)),
        ("glasses", ("очки",)),
        ("jewelry", ("цепоч", "браслет", "украш")),
        ("wallets", ("кошелек", "кошел", "картхолдер")),
        ("bags", ("сумк", "кроссбоди")),
        ("suits", ("костюм",)),
    )
    normalized = _normalize(text)
    for key, words in checks:
        if any(word in normalized for word in words):
            return key
    return None


def resolve_category(store_category: str, text: str = "") -> str | None:
    """Сопоставляет категорию кнопки бота с правилом калькулятора."""
    category = _normalize(store_category)
    detected = _detect_from_text(text)

    if "обув" in category:
        return detected if detected in {"boots", "slides", "sneakers"} else "sneakers"
    if "худи" in category or "свитшот" in category:
        return "sweatshirt" if detected == "sweatshirt" else "hoodie"
    if "футбол" in category or "рубаш" in category:
        return "shirt" if detected == "shirt" else "tshirt"
    if "штаны" in category or "джинс" in category:
        return "pants"
    if "шорт" in category or "трус" in category:
        return "underwear" if detected == "underwear" else "shorts"
    if "головн" in category or "шарф" in category:
        return "scarves" if detected == "scarves" else "hats"
    if "сумк" in category or "кошел" in category:
        return "wallets" if detected == "wallets" else "bags"
    if "куртк" in category or "ветров" in category:
        if _has(text, *LEATHER_OUTERWEAR_TRIGGERS):
            return "leather_outerwear"
        return detected if detected in {
            "heavy_jacket", "light_winter_jacket", "fall_outerwear", "windbreaker"
        } else "windbreaker"
    if "зима" in category:
        if _has(text, "пуховик"):
            return "puffer_jacket"
        return detected if detected in {"heavy_jacket", "light_winter_jacket"} else "light_winter_jacket"
    if "костюм" in category:
        return "suits"
    if "аксессуар" in category:
        return detected if detected in {
            "hats", "socks", "belts", "backpacks", "underwear", "scarves",
            "gloves", "watches", "umbrellas", "glasses", "jewelry",
        } else None
    if "old money" in category:
        return detected
    if "основн" in category:
        return detected
    return detected


def _load_rate() -> Decimal:
    try:
        with open(RATE_FILE, encoding="utf-8") as f:
            raw = json.load(f).get("rate")
        rate = Decimal(str(raw))
        if rate > 0:
            return rate
    except (FileNotFoundError, OSError, ValueError, TypeError, InvalidOperation, AttributeError):
        pass
    return DEFAULT_RATE


_current_rate = _load_rate()


def get_exchange_rate() -> Decimal:
    return _current_rate


def set_exchange_rate(value: str | int | float | Decimal) -> Decimal:
    """Устанавливает и атомарно сохраняет курс для будущих расчётов."""
    global _current_rate
    try:
        rate = Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise PriceCalculationError("Курс должен быть положительным числом, например 12.5.")
    if not rate.is_finite() or rate <= 0 or rate > Decimal("1000"):
        raise PriceCalculationError("Курс должен быть больше 0 и не превышать 1000.")

    temp_path = f"{RATE_FILE}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"rate": str(rate)}, f, ensure_ascii=False)
        os.replace(temp_path, RATE_FILE)
    except OSError as exc:
        raise PriceCalculationError(f"Не удалось сохранить курс: {exc}") from exc

    _current_rate = rate
    return rate


def format_rate(rate: Decimal | None = None) -> str:
    value = rate if rate is not None else get_exchange_rate()
    return format(value.normalize(), "f")


def _round_price(raw_price: Decimal, rounding: int) -> Decimal:
    if rounding == 90:
        rounded = (
            (raw_price - Decimal("90")) / Decimal("100")
        ).to_integral_value(rounding=ROUND_FLOOR) * Decimal("100") + Decimal("90")
        return max(Decimal("90"), rounded)
    return (raw_price / Decimal("10")).to_integral_value(rounding=ROUND_HALF_UP) * Decimal("10")


def calculate_price(
    purchase_yuan: int | float | str | Decimal,
    store_category: str,
    text: str = "",
    rate: Decimal | None = None,
) -> Calculation:
    """Рассчитывает итоговую цену в рублях по таблице."""
    try:
        purchase = Decimal(str(purchase_yuan).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PriceCalculationError("Закупочная цена в юанях указана неверно.") from exc
    if not purchase.is_finite() or purchase <= 0:
        raise PriceCalculationError("Закупочная цена в юанях должна быть больше нуля.")

    selected_rate = rate if rate is not None else get_exchange_rate()
    category_key = resolve_category(store_category, text)
    if not category_key or (
        category_key not in PRICE_RULES
        and category_key not in DELIVERY_PRICE_RULES
    ):
        raise PriceCalculationError(
            "Не понял тип товара для калькулятора. "
            "Добавь тип в название или материал: "
            "кроссовки, ботинки, худи, футболка, рубашка, "
            "ремень, рюкзак, часы и т. п."
        )

    if category_key in DELIVERY_PRICE_RULES:
        delivery_rule = DELIVERY_PRICE_RULES[category_key]
        raw_price = (
            purchase
            + FIXED_PURCHASE_COST_YUAN
            + delivery_rule.delivery_yuan
        ) * selected_rate + delivery_rule.profit_rubles
        rounded = _round_price(raw_price, delivery_rule.rounding)
        return Calculation(
            price=int(rounded),
            category_key=category_key,
            category_label=delivery_rule.label,
            purchase_yuan=purchase,
            rate=selected_rate,
            raw_price=raw_price,
            modifiers=(),
        )

    rule = PRICE_RULES[category_key]
    applied_modifiers: list[str] = []
    normalized_text = _normalize(text)
    modifier_total = Decimal("0")
    for modifier_name, modifier_value in rule.modifiers.items():
        if modifier_name in normalized_text:
            modifier_total += modifier_value
            sign = "+" if modifier_value >= 0 else ""
            applied_modifiers.append(f"{modifier_name} {sign}{format(modifier_value, 'f')} ₽")

    raw_price = purchase * (selected_rate + rule.slope) + rule.base + modifier_total
    rounded = _round_price(raw_price, rule.rounding)

    return Calculation(
        price=int(rounded),
        category_key=category_key,
        category_label=rule.label,
        purchase_yuan=purchase,
        rate=selected_rate,
        raw_price=raw_price,
        modifiers=tuple(applied_modifiers),
    )