"""Генерує банери для ботів у стилі сайту (assets/*.jpg).

Запуск:  python tools/make_banners.py     (потрібен playwright + chromium)
Фото й логотип — із сайту (assets/src). Плашки ціни беруться з PRICE.
"""
import asyncio
import base64
import os
import pathlib

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICE = f"{int(os.getenv('PRICE', '7000')):,}".replace(",", " ")
PLAQUES = [f"{PRICE} грн", "2–3 дні", "без передоплати"]

BANNERS = {
    "order": ("Грант «Власна справа 2.0»", "Бізнес-план<br>під ключ", PLAQUES),
    "resume": ("Анкета збережена", "Продовжимо<br>з того ж кроку", PLAQUES),
    "done": ("Анкету отримано", "Далі напишу<br>Вам особисто", ["звіт за 2–3 дні", "оплата після вичитки"]),
    "menu": ("Довідник і калькулятор", "Власна<br>справа 2.0", ["до 500 000 грн", "до 2 500 000 грн", "бонуси до +50 %"]),
    "about": ("Власна справа 2.0", "Головне<br>за 30 секунд", ["безповоротний грант", "подача через Дію"]),
    "tracks": ("Власна справа 2.0", "Старт чи<br>Масштабування", ["до 500 000 грн", "до 2 500 000 грн"]),
    "bonus": ("Власна справа 2.0", "Бонуси<br>до суми гранту", ["+5 %", "+20 %", "+30 %", "+50 %"]),
    "spend": ("Власна справа 2.0", "На що можна<br>витратити", ["обладнання до 100 %", "реклама до 10 %"]),
    "duties": ("Власна справа 2.0", "Обов’язки<br>після гранту", ["податки ≥ суми гранту", "кошти за 3 місяці"]),
    "apply": ("Власна справа 2.0", "Як подати<br>заявку в Дії", ["6 кроків", "бізнес-план обов’язковий"]),
    "refuse": ("Власна справа 2.0", "Чому<br>відмовляють", ["ліміти кошторису", "цифри не сходяться"]),
    "service": ("Бізнес-план під ключ", "Що входить<br>у пакет", PLAQUES),
    "calc": ("Калькулятор гранту", "Ваша сума<br>з бонусами", ["область", "галузь", "статус"]),
}

HTML = """<!doctype html><html lang="uk"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=PT+Sans:wght@400;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0}
body{width:1280px;height:640px;overflow:hidden;font-family:'PT Sans',system-ui,sans-serif;color:#EEF1FF;
background:radial-gradient(700px 520px at 78% 40%,rgba(120,84,220,.55),transparent 62%),
radial-gradient(600px 420px at 0% 100%,rgba(224,64,138,.22),transparent 60%),
repeating-linear-gradient(90deg,rgba(150,160,255,.05) 0 1px,transparent 1px 46px),
repeating-linear-gradient(0deg,rgba(150,160,255,.05) 0 1px,transparent 1px 46px),#0B0E1A}
.ph{position:absolute;right:0;top:0;height:640px;width:560px;background:url(__PHOTO__) right top/auto 640px no-repeat;
-webkit-mask-image:linear-gradient(90deg,transparent 0,#000 26%);mask-image:linear-gradient(90deg,transparent 0,#000 26%)}
.in{position:absolute;left:64px;top:52px;width:760px}
.top{display:flex;align-items:center;gap:16px}
.top img{width:70px;height:70px;border-radius:50%;box-shadow:0 0 0 2px rgba(157,182,255,.6),0 0 28px rgba(155,108,255,.6)}
.bn{font-family:'Oswald',sans-serif;font-weight:700;font-size:26px;letter-spacing:.06em;color:#fff}
.bs{font-size:14px;letter-spacing:.2em;text-transform:uppercase;color:#8F9AC6;margin-top:2px}
.k{display:inline-block;margin-top:44px;padding:8px 18px;border-radius:999px;border:1px solid rgba(143,176,255,.5);
background:rgba(107,151,255,.16);color:#C4D3FF;font-size:19px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
h1{font-family:'Oswald',sans-serif;font-weight:700;font-size:92px;line-height:1.04;margin-top:18px;text-transform:uppercase;
background:linear-gradient(90deg,#A9C0FF,#C9AEFF 50%,#FF94C2);-webkit-background-clip:text;background-clip:text;color:transparent}
.pl{position:absolute;left:64px;bottom:54px;display:flex;gap:12px;flex-wrap:wrap;width:800px}
.pl span{padding:12px 22px;border-radius:16px;font-size:25px;font-weight:700;color:#fff;
background:linear-gradient(160deg,rgba(107,151,255,.30),rgba(155,108,255,.28) 50%,rgba(224,64,138,.30));
border:1px solid rgba(200,180,255,.5)}
</style></head><body><div class="ph"></div><div class="in"><div class="top"><img src="__LOGO__">
<div><div class="bn">UNCLE EVAN</div><div class="bs">Економічний архітектор майбутнього</div></div></div>
<span class="k">__KICK__</span><h1>__TITLE__</h1></div><div class="pl">__PL__</div></body></html>"""


def data_uri(p: pathlib.Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


async def main():
    photo = data_uri(ROOT / "assets/src/uehome-og.jpg")
    logo = data_uri(ROOT / "assets/src/uncle-evan-logo-2026.jpg")
    async with async_playwright() as p:
        br = await p.chromium.launch()
        pg = await br.new_page(viewport={"width": 1280, "height": 640})
        for name, (kick, title, pl) in BANNERS.items():
            html = (HTML.replace("__PHOTO__", photo).replace("__LOGO__", logo).replace("__KICK__", kick)
                    .replace("__TITLE__", title).replace("__PL__", "".join(f"<span>{x}</span>" for x in pl)))
            await pg.set_content(html, wait_until="networkidle")
            await pg.evaluate("document.fonts.ready")
            await pg.screenshot(path=str(ROOT / "assets" / f"{name}.jpg"), type="jpeg", quality=88)
            print("ok", name)
        await br.close()


if __name__ == "__main__":
    asyncio.run(main())
