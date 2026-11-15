"""Таблица клавиш: имя -> (виртуальный код, скан-код, расширенная клавиша).

Имена клавиш пишутся заглавными латинскими буквами. Сочетания задаются через
«+»: ``CTRL+1``, ``SHIFT+F2``, ``ALT+Q``.

Игры на DirectX часто читают клавиатуру через DirectInput / Raw Input и
игнорируют «виртуальные» нажатия, поэтому по умолчанию ввод отправляется
скан-кодами (как от физической клавиатуры).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class KeyInfo:
    name: str
    vk: int
    scan: int
    extended: bool = False


_KEYS: Dict[str, KeyInfo] = {}


def _add(name: str, vk: int, scan: int, extended: bool = False) -> None:
    _KEYS[name] = KeyInfo(name, vk, scan, extended)


# Буквы
_LETTER_SCANS = {
    "Q": 0x10, "W": 0x11, "E": 0x12, "R": 0x13, "T": 0x14, "Y": 0x15, "U": 0x16,
    "I": 0x17, "O": 0x18, "P": 0x19, "A": 0x1E, "S": 0x1F, "D": 0x20, "F": 0x21,
    "G": 0x22, "H": 0x23, "J": 0x24, "K": 0x25, "L": 0x26, "Z": 0x2C, "X": 0x2D,
    "C": 0x2E, "V": 0x2F, "B": 0x30, "N": 0x31, "M": 0x32,
}
for _ch, _sc in _LETTER_SCANS.items():
    _add(_ch, ord(_ch), _sc)

# Цифры верхнего ряда
for _i, _ch in enumerate("1234567890"):
    _add(_ch, ord(_ch), 0x02 + _i)

# Функциональные клавиши
for _i in range(1, 11):
    _add(f"F{_i}", 0x6F + _i, 0x3A + _i)
_add("F11", 0x7A, 0x57)
_add("F12", 0x7B, 0x58)

# Служебные
_add("ESC", 0x1B, 0x01)
_add("TAB", 0x09, 0x0F)
_add("SPACE", 0x20, 0x39)
_add("ENTER", 0x0D, 0x1C)
_add("BACKSPACE", 0x08, 0x0E)
_add("CAPSLOCK", 0x14, 0x3A)

# Модификаторы (для SendInput используем общие VK: SHIFT/CTRL/ALT)
_add("SHIFT", 0x10, 0x2A)
_add("CTRL", 0x11, 0x1D)
_add("ALT", 0x12, 0x38)
_add("RSHIFT", 0xA1, 0x36)
_add("RCTRL", 0xA3, 0x1D, True)
_add("RALT", 0xA5, 0x38, True)

# Знаки
_add("MINUS", 0xBD, 0x0C)
_add("EQUALS", 0xBB, 0x0D)
_add("LBRACKET", 0xDB, 0x1A)
_add("RBRACKET", 0xDD, 0x1B)
_add("SEMICOLON", 0xBA, 0x27)
_add("QUOTE", 0xDE, 0x28)
_add("BACKQUOTE", 0xC0, 0x29)
_add("BACKSLASH", 0xDC, 0x2B)
_add("COMMA", 0xBC, 0x33)
_add("PERIOD", 0xBE, 0x34)
_add("SLASH", 0xBF, 0x35)

# Навигация (расширенные)
_add("UP", 0x26, 0x48, True)
_add("DOWN", 0x28, 0x50, True)
_add("LEFT", 0x25, 0x4B, True)
_add("RIGHT", 0x27, 0x4D, True)
_add("INSERT", 0x2D, 0x52, True)
_add("DELETE", 0x2E, 0x53, True)
_add("HOME", 0x24, 0x47, True)
_add("END", 0x23, 0x4F, True)
_add("PAGEUP", 0x21, 0x49, True)
_add("PAGEDOWN", 0x22, 0x51, True)

# Цифровой блок
_NUM_SCANS = [0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49]
for _i, _sc in enumerate(_NUM_SCANS):
    _add(f"NUM{_i}", 0x60 + _i, _sc)
_add("NUMMULT", 0x6A, 0x37)
_add("NUMPLUS", 0x6B, 0x4E)
_add("NUMMINUS", 0x6D, 0x4A)
_add("NUMDOT", 0x6E, 0x53)
_add("NUMDIV", 0x6F, 0x35, True)

# Кнопки мыши (только для горячих клавиш программы, не для отправки)
_add("MOUSE4", 0x05, 0)
_add("MOUSE5", 0x06, 0)

MODIFIERS = ("CTRL", "SHIFT", "ALT")

ALIASES = {
    "ESCAPE": "ESC", "RETURN": "ENTER", "CONTROL": "CTRL", "LCTRL": "CTRL",
    "LSHIFT": "SHIFT", "LALT": "ALT", "MENU": "ALT", "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN", "DEL": "DELETE", "INS": "INSERT", "-": "MINUS",
    "=": "EQUALS", "[": "LBRACKET", "]": "RBRACKET", ";": "SEMICOLON",
    "'": "QUOTE", "`": "BACKQUOTE", "~": "BACKQUOTE", "\\": "BACKSLASH",
    ",": "COMMA", ".": "PERIOD", "/": "SLASH", "ПРОБЕЛ": "SPACE",
    "TILDE": "BACKQUOTE", "Ё": "BACKQUOTE",
}

# Русская раскладка ЙЦУКЕН → физические клавиши (в игру уходит скан-код клавиши)
_RU_LAYOUT = {
    "Й": "Q", "Ц": "W", "У": "E", "К": "R", "Е": "T", "Н": "Y", "Г": "U", "Ш": "I", "Щ": "O",
    "З": "P", "Х": "LBRACKET", "Ъ": "RBRACKET", "Ф": "A", "Ы": "S", "В": "D", "А": "F", "П": "G",
    "Р": "H", "О": "J", "Л": "K", "Д": "L", "Ж": "SEMICOLON", "Э": "QUOTE", "Я": "Z", "Ч": "X",
    "С": "C", "М": "V", "И": "B", "Т": "N", "Ь": "M", "Б": "COMMA", "Ю": "PERIOD",
    "ТАБ": "TAB", "ЕСК": "ESC",
}
ALIASES.update(_RU_LAYOUT)

_VK_TO_NAME: Dict[int, str] = {}
for _name, _info in _KEYS.items():
    _VK_TO_NAME.setdefault(_info.vk, _name)
# Левые/правые модификаторы из нативных событий
_VK_TO_NAME.update({0xA0: "SHIFT", 0xA1: "SHIFT", 0xA2: "CTRL", 0xA3: "CTRL",
                    0xA4: "ALT", 0xA5: "ALT"})


def all_key_names() -> List[str]:
    return list(_KEYS.keys())


def key_info(name: str) -> Optional[KeyInfo]:
    return _KEYS.get(normalize_name(name))


def normalize_name(name: str) -> str:
    n = name.strip().upper()
    return ALIASES.get(n, n)


@dataclass(frozen=True)
class KeyCombo:
    modifiers: Tuple[str, ...]
    key: str

    def __str__(self) -> str:
        return "+".join(list(self.modifiers) + [self.key])

    @property
    def info(self) -> KeyInfo:
        return _KEYS[self.key]


class KeyParseError(ValueError):
    pass


def parse_combo(text: str) -> KeyCombo:
    """Разобрать строку вида ``CTRL+1`` в сочетание клавиш."""
    if text is None or not str(text).strip():
        raise KeyParseError("пустая клавиша")
    raw = str(text).strip()
    # «+» как отдельная клавиша — только через NUMPLUS
    parts = raw.replace(" ", "").split("+")
    if not all(parts):
        raise KeyParseError(f"не удалось разобрать «{raw}»")
    names = [normalize_name(p) for p in parts]
    for n in names:
        if n not in _KEYS:
            raise KeyParseError(f"неизвестная клавиша «{n}»")
    *mods, main = names
    if mods and main in MODIFIERS:
        raise KeyParseError("после модификаторов нужна основная клавиша")
    for m in mods:
        if m not in MODIFIERS:
            raise KeyParseError(f"«{m}» не является модификатором (CTRL/SHIFT/ALT)")
    # Упорядочим модификаторы и уберём повторы
    mods_sorted = tuple(m for m in MODIFIERS if m in mods)
    return KeyCombo(mods_sorted, main)


def is_valid_combo(text: str) -> bool:
    try:
        parse_combo(text)
        return True
    except KeyParseError:
        return False


def combo_from_vk(vk: int, ctrl: bool = False, shift: bool = False, alt: bool = False) -> Optional[str]:
    """Имя сочетания по нативному виртуальному коду (для поля ввода клавиши)."""
    name = _VK_TO_NAME.get(vk)
    if name is None or name in MODIFIERS:
        return None
    mods = [m for m, on in (("CTRL", ctrl), ("SHIFT", shift), ("ALT", alt)) if on]
    return "+".join(mods + [name])
