"""Параметри гранту «Власна справа 2.0» і розрахунок суми.

Логіка один в один із формами сайту (анкета v10, калькулятор v9).
Змінилась постанова — правимо тут і у формах.
"""
BASE = {"start": 300_000, "scale": 1_500_000}
CAP = {"start": 500_000, "scale": 2_500_000}

FRONTLINE = {
    "Дніпропетровська область", "Донецька область", "Запорізька область",
    "Київська область — Славутицька громада", "Миколаївська область", "Одеська область",
    "Сумська область", "Харківська область", "Херсонська область", "Чернігівська область",
}
PRIORITY = {
    "Оборонно-промисловий комплекс", "IT та кібербезпека", "Переробна промисловість, виробництво",
    "Агропромисловий сектор і переробка", "Енергетика та енергоефективність",
    "Логістика, транспорт, склад", "Будівництво та відновлення", "Освіта, навчання, курси",
    "Догляд, реабілітація, медицина", "Креативні індустрії, дизайн, медіа",
}
CHIP_LABELS = {
    "age": ("Вік 18–25 років", 5),
    "vet": ("Ветеранське підприємництво", 20),
    "pdv": ("Платник ПДВ", 20),
    "pat": ("Патент на винахід або корисну модель", 20),
    "vpo": ("ВПО з досвідом 6+ міс. або переїзд бізнесу", 20),
    "jobs2": ("Два і більше нових робочих місця", 20),
    "rep": ("Відновлення пошкодженого війною", 30),
}


def _has(arr, sub: str) -> bool:
    return any(sub in str(v) for v in (arr or []))


def _int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except ValueError:
        return 0


def _lead_int(v) -> int:
    """Як parseInt у JS: число на початку рядка ('2 робочі місця' → 2)."""
    digits = ""
    for ch in str(v or "").strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def _finish(path: str, bonuses: list[tuple[str, int, bool]]) -> dict:
    start = path != "scale"
    base = BASE["start" if start else "scale"]
    cap = CAP["start" if start else "scale"]
    pct = sum(p for _, p, on in bonuses if on)
    raw = round(base * (1 + pct / 100))
    return {
        "start": start, "base": base, "cap": cap, "pct": pct, "raw": raw,
        "grant": min(raw, cap),
        "bonuses": [{"l": l, "p": p} for l, p, on in bonuses if on],
    }


def calc_kalk(s: dict) -> dict:
    on = s.get("on") or []
    b = [
        (*CHIP_LABELS["age"], "age" in on), (*CHIP_LABELS["vet"], "vet" in on),
        (*CHIP_LABELS["pdv"], "pdv" in on), (*CHIP_LABELS["pat"], "pat" in on),
        (*CHIP_LABELS["vpo"], "vpo" in on), (*CHIP_LABELS["jobs2"], "jobs2" in on),
        ("Прифронтовий регіон", 30, s.get("region") in FRONTLINE),
        (*CHIP_LABELS["rep"], "rep" in on),
        ("Пріоритетна галузь", 50, s.get("sector") in PRIORITY),
    ]
    return _finish("scale" if s.get("path") == "scale" else "start", b)


def items_total(s: dict) -> int:
    return sum(_int(i.get("qty")) * _int(i.get("price")) for i in (s.get("items") or []) if isinstance(i, dict))


def calc_anketa(s: dict) -> dict:
    st, bn = s.get("status") or [], s.get("bonus") or []
    jobs = _lead_int(s.get("jobs"))
    b = [
        ("Вік 18–25 років", 5, s.get("age") == "18–25 років"),
        ("Ветеранське підприємництво", 20, _has(st, "Ветеран або учасник") or _has(st, "ветеранського підприємництва")),
        ("Платник ПДВ", 20, _has(bn, "платник ПДВ") or s.get("tax") == "ФОП 3 група — 3% з ПДВ"),
        ("Патент на винахід або корисну модель", 20, _has(bn, "патент")),
        ("ВПО з досвідом 6+ міс. або переїзд бізнесу", 20, _has(bn, "ВПО і вже мав") or _has(bn, "переїхав")),
        ("Два і більше нових робочих місця", 20, jobs >= 2),
        ("Прифронтовий регіон", 30, s.get("region") in FRONTLINE),
        ("Відновлення пошкодженого війною", 30, _has(bn, "замість пошкодженого")),
        ("Пріоритетна галузь", 50, _has(bn, "галузь пріоритетна")),
    ]
    r = _finish("start" if s.get("path") == "start" else "scale", b)
    r["items_total"] = items_total(s)
    r["jobs"] = jobs
    return r


def money(n) -> str:
    return f"{int(round(n or 0)):,}".replace(",", " ")
