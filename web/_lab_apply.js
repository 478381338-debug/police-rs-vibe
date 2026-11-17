/* 版面选型用的临时注入脚本：只用来看真实页面换版后的效果，不参与产品路由。 */
(function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (s) => document.querySelector(s);

  window.__labRun = async function (cfg) {
    const payload = await (await fetch('/static/_lab_demo.json')).json();
    if (!window.__labPatched) {
      const orig = window.fetch;
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input.url;
        if (url.includes('/api/sixg/demo')) {
          let name = 'normal';
          try { name = JSON.parse((init && init.body) || '{}').scenario || 'normal'; } catch (e) {}
          return new Response(JSON.stringify(payload[name] || payload.normal), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return orig(input, init);
      };
      window.__labPatched = true;
    }

    $('#run-demo').click();
    await sleep(cfg.wait || 9000);

    const main = $('.ts-main');
    main.querySelectorAll('.lab-rail,.lab-dock,.lab-band').forEach((n) => n.remove());

    if (cfg.layout === 'lab-l1') {
      const L = document.createElement('div'); L.className = 'lab-rail left';
      const R = document.createElement('div'); R.className = 'lab-rail right';
      const D = document.createElement('div'); D.className = 'lab-dock';
      L.append($('.scenario-panel'), $('.net-panel'));
      R.append($('.timeline-panel'), $('.case-panel'));
      D.append($('.vision-panel'), $('.patch-panel'));
      main.append(L, R, D);
    }

    let link = document.getElementById('__lab_skin');
    if (!link) {
      link = document.createElement('link');
      link.id = '__lab_skin';
      link.rel = 'stylesheet';
      document.head.append(link);
    }
    link.href = '/static/' + cfg.css + '?v=' + Date.now();
    document.body.className = document.body.className.replace(/\blab-l\d\b/g, '').trim();
    document.body.classList.add(cfg.layout);

    await sleep(700);
    window.dispatchEvent(new Event('resize'));
    await sleep(900);
    window.dispatchEvent(new Event('resize'));
    return { layout: cfg.layout, kpis: document.querySelectorAll('#net-kpis .kpi').length };
  };
})();