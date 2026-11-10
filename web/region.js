(function () {
  const embed = document.getElementById('zq-embed');
  const frame = document.getElementById('zq-frame');
  const tabs = Array.from(document.querySelectorAll('.region-tab'));
  if (!embed || !frame || !tabs.length) return;

  const subtitle = document.querySelector('.brand-text p');
  const originalSubtitle = subtitle ? subtitle.innerHTML : '';

  function setRegion(region, updateUrl) {
    const zhangqiu = region === 'zhangqiu';
    document.body.classList.toggle('zq-mode', zhangqiu);
    embed.classList.toggle('active', zhangqiu);
    embed.setAttribute('aria-hidden', zhangqiu ? 'false' : 'true');
    tabs.forEach((tab) => {
      const active = tab.dataset.region === region;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-pressed', active ? 'true' : 'false');
    });

    if (zhangqiu) {
      if (!frame.dataset.loaded) {
        frame.src = '/zhangqiu?embed=1&v=20261107region1';
        frame.dataset.loaded = '1';
      }
      document.title = '警务遥感 · 章丘专区';
      if (subtitle) subtitle.textContent = '济南市章丘区 · 公开自然与人文数据 · 风险筛查';
      if (updateUrl) history.replaceState(null, '', '?region=zhangqiu');
    } else {
      document.title = '警务遥感 · 生态安全与灾害风险遥感地图';
      if (subtitle) subtitle.innerHTML = originalSubtitle;
      if (updateUrl) history.replaceState(null, '', location.pathname);
      // 主地图在 iframe 关闭后再校正一次尺寸
      setTimeout(() => {
        if (window.state && window.state.map && window.state.map.invalidateSize) {
          window.state.map.invalidateSize();
        }
      }, 80);
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => setRegion(tab.dataset.region, true));
  });

  const initial = new URLSearchParams(location.search).get('region');
  if (initial === 'zhangqiu') setRegion('zhangqiu', false);
})();
