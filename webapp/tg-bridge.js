/* Міст між формою сайту і Telegram-ботом.
   Працює лише всередині Telegram (є initData). Саму форму не змінює:
   додає збереження прогресу на сервері та відправку заявки в бот. */
(function () {
  var TG = window.Telegram && window.Telegram.WebApp;
  var INIT = (TG && TG.initData) || window.__UE_INIT || '';
  if (!INIT) return;
  var KIND = window.UE_KIND, CFG = window.UE_CFG || {};
  try { TG.ready(); TG.expand(); TG.setHeaderColor && TG.setHeaderColor('#0B0E1A'); TG.setBackgroundColor && TG.setBackgroundColor('#0B0E1A'); } catch (e) {}

  var css = document.createElement('style');
  css.textContent =
    'html,body{background:#0B0E1A!important}.ue-botlink{display:none!important}' +
    '.brandbar{display:block!important;background:linear-gradient(160deg,#12163A,#0B0E1A)!important}' +
    '.brandbar::after{background:linear-gradient(100deg,#5B84FF,#9A6BFF 52%,#E0408A)!important;box-shadow:none!important}' +
    '.brandin img{box-shadow:0 0 0 2px rgba(157,182,255,.6),0 6px 18px rgba(0,0,0,.6)!important}' +
    '.bn{color:#fff!important;text-shadow:none!important}.bn i,.bn .grad{background:linear-gradient(90deg,#A9C0FF,#C9AEFF 50%,#FF94C2);-webkit-background-clip:text;background-clip:text;color:transparent!important}' +
    '.bs{color:#8F9AC6!important}' +
    '.ue-tg{display:flex;flex-direction:column;gap:10px;margin-top:8px}' +
    '.ue-tg .sendbtn{width:100%;min-height:56px;font-size:17px!important;flex:none}' +
    '.ue-note{color:#8F9AC6;font-size:14px;line-height:1.45}' +
    '.ue-st{font-size:15px;line-height:1.45;color:#8FE6B2;min-height:20px}.ue-st.bad{color:#FFA566}' +
    '.ue-contact{width:100%;margin:0 0 10px;background:rgba(107,151,255,.14);border:1.5px solid #8FB0FF;color:#fff;border-radius:14px;padding:13px 14px;font-size:16px}';
  document.head.appendChild(css);

  function api(path, body) {
    return fetch('/api/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-TG-Init': INIT },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().then(function (d) { d.__ok = r.ok; return d; }); });
  }
  function haptic(t) { try { TG.HapticFeedback.notificationOccurred(t); } catch (e) {} }
  function closeSoon(ms) { setTimeout(function () { try { TG.close(); } catch (e) {} }, ms || 1800); }

  /* ---------------- АНКЕТА ---------------- */
  if (KIND === 'anketa') {
    var lastSent = '';
    var DEF = function () { return { step: 0, path: null, items: [], monthly: {} }; };

    function fromCalc(c) {
      if (!c) return;
      var on = c.on || [], has = function (k) { return on.indexOf(k) !== -1; };
      S.path = c.path === 'scale' ? 'scale' : 'start';
      if (c.region) S.region = c.region;
      if (c.sector) S.sector = c.sector;
      if (has('age')) S.age = '18–25 років';
      if (has('vet')) S.status = ['Ветеран або учасник бойових дій'];
      if (has('jobs2')) S.jobs = '2 робочі місця';
      var b = [];
      if (has('pat')) b.push('Маю патент на винахід або корисну модель за темою бізнесу');
      if (has('vpo')) b.push('Я ВПО і вже мав бізнес понад 6 місяців');
      if (has('pdv')) b.push('Я платник ПДВ');
      if (has('rep')) b.push('Куплю обладнання замість пошкодженого війною');
      var sec = SECTORS.find(function (v) { return v.n === c.sector; });
      if (sec && sec.p) b.push('Моя галузь пріоритетна — так, за Вашою галуззю');
      if (b.length) S.bonus = b;
    }

    var baseDraw = window.draw;
    window.draw = function () {
      baseDraw.apply(this, arguments);
      try { afterDraw(); } catch (e) {}
    };
    function afterDraw() {
      var st = (typeof cur === 'function') ? cur() : null;
      if (st && st.id === 'owner' && TG && TG.requestContact && !(S.phone && V.phone.t(S.phone))) {
        var inp = document.querySelector('input[data-k="phone"]');
        if (inp && !document.querySelector('.ue-contact')) {
          var b = document.createElement('button');
          b.type = 'button'; b.className = 'ue-contact'; b.textContent = '📱 Підставити номер із Telegram';
          b.onclick = function () {
            TG.requestContact(function (ok, resp) {
              var ph = resp && resp.responseUnsafe && resp.responseUnsafe.contact && resp.responseUnsafe.contact.phone_number;
              if (ok && ph) { S.phone = V.phone.c(String(ph)); S.phone_verified = true; window.draw(); }
            });
          };
          inp.parentNode.insertBefore(b, inp);
        }
      }
    }

    var baseResult = window.drawResult;
    window.drawResult = function () {
      baseResult.apply(this, arguments);
      var box = document.querySelector('.sendbox');
      if (!box) return;
      document.querySelectorAll('.msg.info.no-print').forEach(function (x) { x.style.display = 'none'; });
      box.className = 'ue-tg no-print';
      box.innerHTML =
        '<button class="sendbtn go" id="ueSend">Надіслати анкету Uncle Evan</button>' +
        '<div class="ue-st" id="ueSt"></div>' +
        '<div class="ue-note">Анкета одразу надійде Uncle Evan, а її копія — Вам у цей чат. Далі Uncle Evan напише Вам особисто.</div>';
      document.getElementById('ueSend').onclick = submit;
    };

    var sending = false;
    function submit() {
      if (sending) return;
      var btn = document.getElementById('ueSend'), st = document.getElementById('ueSt');
      sending = true; btn.disabled = true; btn.textContent = 'Надсилаю…'; st.textContent = ''; st.className = 'ue-st';
      api('submit', { state: S, text: buildTxt(), calc: calc() }).then(function (d) {
        sending = false;
        if (d.__ok && d.ok) {
          btn.textContent = 'Надіслано ✓'; btn.classList.add('done');
          st.textContent = 'Анкету отримано. Копія — у чаті з ботом.';
          S._sent = true; saveS(); lastSent = JSON.stringify(S); haptic('success'); closeSoon(2200);
        } else { throw new Error(d.error || 'fail'); }
      }).catch(function () {
        sending = false; btn.disabled = false; btn.textContent = 'Спробувати ще раз';
        st.className = 'ue-st bad';
        st.textContent = 'Не вдалося надіслати. Перевірте інтернет і натисніть ще раз — відповіді збережено.';
        haptic('error');
      });
    }

    function sync() {
      if (sending || S._sent) return;
      if (!S.path && !S.step) return;
      var j = JSON.stringify(S);
      if (j === lastSent) return;
      lastSent = j;
      api('progress', { state: S }).catch(function () { lastSent = ''; });
    }

    api('state', { kind: 'anketa' }).then(function (d) {
      if (S._sent) {
        var keep = { pib: S.pib, phone: S.phone, email: S.email, tg: S.tg };
        S = DEF(); Object.keys(keep).forEach(function (k) { if (keep[k]) S[k] = keep[k]; });
      }
      if (d.progress && typeof d.progress === 'object') {
        S = Object.assign(DEF(), d.progress); delete S._sent;
      } else if (d.calc) {
        fromCalc(d.calc);
      }
      var u = TG && TG.initDataUnsafe && TG.initDataUnsafe.user;
      if (!S.tg && u && u.username) S.tg = '@' + u.username;
      lastSent = JSON.stringify(S);
      window.draw();
    }).catch(function () {}).then(function () {
      setInterval(sync, 4000);
      window.addEventListener('pagehide', sync);
    });
  }

  /* ---------------- КАЛЬКУЛЯТОР ---------------- */
  if (KIND === 'kalk') {
    var t = 0, lastCalc = '';
    function payload() {
      var g = calc();
      return { state: S, result: { sum: g.sum, pct: g.pct, base: g.base, cap: g.cap, start: g.start, bonuses: g.B.filter(function (b) { return b.on; }).map(function (b) { return { l: b.l, p: b.p }; }) } };
    }
    function push() {
      var p = payload(), j = JSON.stringify(p);
      if (j === lastCalc) return Promise.resolve({ __ok: true, same: true });
      lastCalc = j;
      return api('calc', p);
    }
    function decorate() {
      var sb = document.querySelector('.sendbox');
      if (sb) {
        var sect = sb.previousElementSibling;
        if (sect && sect.classList.contains('sect')) sect.style.display = 'none';
        sb.className = 'ue-tg';
        sb.innerHTML =
          '<button class="sendbtn go" id="ueOrder">Замовити бізнес-план під цю суму →</button>' +
          '<button class="sendbtn copy" id="ueChat">Надіслати розрахунок мені в чат</button>' +
          '<div class="ue-st" id="ueSt"></div>' +
          '<div class="ue-note">Бізнес-план за формою Дії: ' + (CFG.price || '7 000') + ' грн, 2–3 дні, без передоплати.</div>';
        document.getElementById('ueOrder').onclick = function () {
          lastCalc = '';
          api('calc', Object.assign(payload(), { order: true })).then(function (d) {
            var link = 'https://t.me/' + CFG.orderBot + '?start=c' + (d.id || 0);
            try { TG.openTelegramLink(link); } catch (e) { location.href = link; }
            closeSoon(600);
          }).catch(function () {
            var st = document.getElementById('ueSt'); st.className = 'ue-st bad';
            st.textContent = 'Не вдалося зберегти розрахунок. Перевірте інтернет і спробуйте ще раз.';
          });
        };
        document.getElementById('ueChat').onclick = function () {
          lastCalc = '';
          api('calc', Object.assign(payload(), { send: true })).then(function () {
            var st = document.getElementById('ueSt'); st.className = 'ue-st';
            st.textContent = 'Розрахунок надіслано в чат.'; haptic('success'); closeSoon(1200);
          }).catch(function () {});
        };
      }
      var nx = document.querySelector('a.bigbtn.next');
      if (nx) nx.style.display = 'none';
    }
    var baseBuild = window.build;
    window.build = function () { baseBuild.apply(this, arguments); decorate(); };
    var baseUpd = window.upd;
    window.upd = function () {
      baseUpd.apply(this, arguments);
      clearTimeout(t); t = setTimeout(function () { push().catch(function () { lastCalc = ''; }); }, 1500);
    };
    decorate();
    api('state', { kind: 'kalk' }).then(function (d) {
      if (d.calc && typeof d.calc === 'object' && Array.isArray(d.calc.on)) {
        S = Object.assign({ path: 'start', region: '', sector: '', on: [] }, d.calc);
        lastCalc = JSON.stringify(payload());
        window.build();
      }
    }).catch(function () {});
  }
})();
