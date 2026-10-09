"""Наскрізний тест Mini App у справжньому браузері: форма сайту + міст + сервер.

Проходить анкету кнопками, як людина, і перевіряє, що заявка дійшла, а сума збігається
з розрахунком форми. Окремо звіряє формулу форми (JS) з серверною (Python) на випадкових даних.
"""
import json
import random

import pytest
from aiohttp import web
from playwright.async_api import async_playwright

from app import db, tgauth
from app.grant import calc_anketa, calc_kalk
from app.web import make_app

pytestmark = pytest.mark.asyncio
ORDER, INFO = "111:AAA-order-test", "222:BBB-info-test"

FILL = """async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const type = (el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); };
  const SAMPLE = {name: 'Іваненко Іван Іванович', phone: '0957221978', email: 'ivan@gmail.com', tg: 'ivan_test',
                  code: '1234567890', my: '032024', int: '50000', txt: 'Тестове значення', txto: 'Примітка'};
  if (cur().type === 'memo') go(1);
  let guard = 0;
  while (document.getElementById('nb') && guard++ < 60) {
    const st = cur();
    if (st.type === 'path') { document.querySelector('.path').click(); }
    else if (st.type !== 'memo') {
      for (const f of vF(st)) {
        if (f.ty === 'text') { const el = document.querySelector('input[data-k="' + f.k + '"]'); type(el, SAMPLE[f.v] || 'Текст'); }
        else if (f.ty === 'single' || f.ty === 'multi') {
          const b = document.querySelector('[data-' + (f.ty === 'multi' ? 'm' : 's') + '="' + f.k + '"]');
          if (b && !b.classList.contains('selected')) b.click();
        } else if (f.ty === 'items') {
          aItem();
          type(document.querySelector('[data-i="0"][data-f="name"]'), 'Верстат токарний');
          type(document.querySelector('[data-i="0"][data-f="qty"]'), '2');
          type(document.querySelector('[data-i="0"][data-f="price"]'), '120000');
        } else if (f.ty === 'money') { type(document.querySelector('input[data-mm]'), '15000'); }
      }
    }
    await sleep(20);
    const nb = document.getElementById('nb');
    if (nb.disabled) return {stuck: st.id, state: S};
    nb.click();
    await sleep(20);
  }
  return {done: !!document.getElementById('ueSend'), grant: calc().grant, phone: S.phone, state: S,
          oldBox: !!document.getElementById('bMail')};
}"""


@pytest.fixture
async def server(env):
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", env
    await runner.cleanup()


async def _page(pw, base, kind, user, token):
    br = await pw.chromium.launch()
    ctx = await br.new_context(viewport={"width": 390, "height": 844})
    pg = await ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    await pg.route("**/telegram.org/**", lambda r: r.abort())
    await pg.route("**/fonts.g*/**", lambda r: r.abort())
    await pg.add_init_script(f"window.__UE_INIT = {json.dumps(tgauth.sign(user, token))};")
    await pg.goto(f"{base}/app/{kind}")
    await pg.wait_for_function("window.UE_KIND !== undefined")
    await pg.wait_for_timeout(400)
    return br, pg, errors


async def test_anketa_end_to_end(server):
    base, env = server
    await db.set_setting("orders_chat_id", "-100500")
    user = {"id": 9001, "first_name": "Іван", "username": "ivan_test"}
    async with async_playwright() as pw:
        br, pg, errors = await _page(pw, base, "anketa", user, ORDER)
        res = await pg.evaluate(FILL)
        assert not res.get("stuck"), f"форма зупинилась на кроці {res.get('stuck')}"
        assert res["done"] and not res["oldBox"], "у Telegram замість пошти — кнопка відправки в бот"
        assert res["phone"] == "+380 95 722 19 78", "телефон нормалізується маскою форми"
        await pg.screenshot(path="/tmp/ue-anketa-result.png", full_page=False)
        await pg.click("#ueSend")
        await pg.wait_for_function("document.getElementById('ueSt').textContent.includes('отримано')", timeout=8000)
        assert not errors, errors
        await br.close()
    app = await db.get_app(1)
    assert app and app["grant"] == res["grant"] == calc_anketa(app["state"])["grant"]
    assert "АНКЕТА ДЛЯ БІЗНЕС-ПЛАНУ" in app["text"] and "Верстат токарний" in app["text"]
    assert env["order"].sent("send_message", -100500) and env["order"].sent("send_document", 9001)


async def test_anketa_progress_survives_reopen(server):
    base, env = server
    user = {"id": 9002, "first_name": "Олена"}
    async with async_playwright() as pw:
        br, pg, errors = await _page(pw, base, "anketa", user, ORDER)
        await pg.evaluate("() => { go(1); document.querySelector('.path').click(); go(1); S.org='ФОП'; S.pib='Олена Тест'; }")
        await pg.wait_for_timeout(4600)  # автозбереження раз на 4 с
        await br.close()
        saved = await db.get_progress(9002)
        assert saved and saved["state"]["step"] == 2 and saved["state"]["pib"] == "Олена Тест"
        # інший пристрій: порожній localStorage, стан приходить із сервера
        br, pg, errors = await _page(pw, base, "anketa", user, ORDER)
        st = await pg.evaluate("() => ({step: S.step, pib: S.pib, path: S.path})")
        assert st == {"step": 2, "pib": "Олена Тест", "path": "start"}
        assert not errors, errors
        await br.close()


async def test_kalk_prefills_anketa(server):
    base, env = server
    user = {"id": 9003, "first_name": "Петро"}
    async with async_playwright() as pw:
        br, pg, errors = await _page(pw, base, "kalk", user, INFO)
        assert await pg.locator("#ueOrder").count() == 1 and await pg.locator("#bMail").count() == 0
        await pg.select_option("#selRegion", "Харківська область")
        await pg.select_option("#selSector", "IT та кібербезпека")
        await pg.click('[data-chip="vet"]')
        js_sum = await pg.evaluate("calc().sum")
        await pg.screenshot(path="/tmp/ue-kalk.png", full_page=False)
        await pg.wait_for_timeout(2000)  # відкладене збереження
        c = await db.last_calc(9003)
        assert c and c["grant"] == js_sum == 500_000 and c["state"]["on"] == ["vet"]
        await pg.click("#ueChat")
        await pg.wait_for_function("document.getElementById('ueSt').textContent.includes('надіслано')")
        assert env["info"].sent("send_photo", 9003)
        assert not errors, errors
        await br.close()
        # та сама людина відкриває анкету в боті замовлення
        br, pg, errors = await _page(pw, base, "anketa", user, ORDER)
        st = await pg.evaluate("() => ({path: S.path, region: S.region, sector: S.sector, status: S.status, bonus: S.bonus})")
        assert st["region"] == "Харківська область" and st["sector"] == "IT та кібербезпека" and st["path"] == "start"
        assert st["status"] == ["Ветеран або учасник бойових дій"]
        assert await pg.evaluate("calc().grant") == 500_000, "перенесений розрахунок дає ту саму суму"
        assert not errors, errors
        await br.close()


async def test_js_and_python_formulas_match(server):
    """200 випадкових анкет і 200 розрахунків: форма сайту й сервер рахують однаково."""
    base, env = server
    random.seed(7)
    async with async_playwright() as pw:
        br, pg, errors = await _page(pw, base, "anketa", {"id": 9004, "first_name": "T"}, ORDER)
        pairs = await pg.evaluate("""() => {
          const pick = a => a[Math.floor(Math.random() * a.length)];
          const some = a => a.filter(() => Math.random() < 0.3);
          const opts = (id, k, s) => STEPS.find(x => x.id === id).f.find(f => f.k === k).o(s).map(o => typeof o === 'string' ? o : o.t);
          const out = [];
          for (let i = 0; i < 200; i++) {
            const s = {path: pick(['start', 'scale']), items: [], monthly: {}};
            s.region = pick(REGIONS).n; s.sector = pick(SECTORS).n;
            s.age = pick(opts('status', 'age', s)); s.status = some(opts('status', 'status', s));
            s.tax = pick(opts('kved', 'tax', s)); s.jobs = pick(opts('staff', 'jobs', s));
            s.bonus = some(opts('bonus', 'bonus', s));
            for (let j = 0; j < Math.floor(Math.random() * 3); j++) s.items.push({name: 'x', qty: String(1 + j), price: String(1000 * (j + 3))});
            S = s; const g = calc();
            out.push([s, g.grant, g.pct, g.K]);
          }
          return out;
        }""")
        await br.close()
        for s, grant, pct, k in pairs:
            g = calc_anketa(s)
            assert (g["grant"], g["pct"], g["items_total"]) == (grant, pct, k), s
        br, pg, errors = await _page(pw, base, "kalk", {"id": 9005, "first_name": "T"}, INFO)
        pairs = await pg.evaluate("""() => {
          const pick = a => a[Math.floor(Math.random() * a.length)];
          const out = [];
          for (let i = 0; i < 200; i++) {
            S = {path: pick(['start', 'scale']), region: pick([''].concat(REGIONS.map(r => r.n))),
                 sector: pick([''].concat(SECTORS.map(r => r.n))), on: CHIPS.map(c => c.k).filter(() => Math.random() < 0.3)};
            const g = calc(); out.push([S, g.sum, g.pct]);
          }
          return out;
        }""")
        await br.close()
        for s, grant, pct in pairs:
            g = calc_kalk(s)
            assert (g["grant"], g["pct"]) == (grant, pct), s
