(() => {
  'use strict';
  const root = document.getElementById('lejian');
  const button = document.getElementById('lejian-prepare');
  const progress = document.getElementById('lejian-progress');
  async function api(path, data) {
    const response = await fetch('/tools/lejian/' + path, {
      method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.timeout(data === undefined ? 15000 : 180000),
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      body: data === undefined ? undefined : JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '请求失败，请重试');
    return result;
  }
  button.onclick = async () => {
    button.disabled = true;
    document.getElementById('lejian-ready').hidden = true;
    progress.textContent = '正在为你的账号生成安装包，请稍候…';
    try {
      const result = await api('downloads', {});
      const download = document.getElementById('lejian-download');
      download.href = result.url;
      download.download = 'neuro-lejian-3.10.3.apk';
      document.getElementById('lejian-ready').hidden = false;
      progress.textContent = '安装包已生成，正在开始下载。若未自动下载，请点击下方链接。';
      download.click();
    } catch (error) {
      progress.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  api('status').then(status => {
    document.getElementById('lejian-account').textContent = status.bound ? '已绑定 QQ：' + status.qq : '尚未绑定 QQ';
    document.getElementById('lejian-bind').hidden = status.bound;
    button.disabled = !status.bound;
  }).catch(error => { progress.textContent = error.message; });
})();
