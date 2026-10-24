;(function () {
  'use strict';
  // Personalized APKs prepend a one-use website grant. Official Legym
  // credentials are never sent to the reminder service.
  var CFG = { base: (typeof __LEJIAN_PUBLIC_BASE__ === 'string' ? __LEJIAN_PUBLIC_BASE__ : ''), app: '3.10.3' };
  if (!/^https:\/\//.test(CFG.base)) return;
  var STATE = '__lj_site_binding_v2', QUEUE = '__lj_outbox_v2';
  var detail = Object.create(null), detailIds = [], binding, queue = [];
  var originalRequest, activating = false, sending = false, notices = {};
  var bootstrap = typeof __LEJIAN_BOOTSTRAP__ !== 'undefined' ? __LEJIAN_BOOTSTRAP__ : '';
  var watchRows = null, watchViews = [], watchReading = false, watchReadAt = 0, watchReadAgain = false;
  function watchEvent(e) { return e.event === 'watch_registration' || e.event === 'cancel_registration_watch'; }
  function watchKey(a) { return String(a.id) + ':' + String(a.registerStartTime || a.startRegisterTime); }
  function watchActive(a) {
    return !!watchRows && watchRows.some(function (r) { return String(r.activity_id) + ':' + String(r.opening) === watchKey(a); });
  }
  function paintWatches() {
    watchViews = watchViews.filter(function (v) { return !v.vm._isDestroyed; }).slice(-200);
    watchViews.forEach(function (v) {
      var vm=v.vm, a=v.activity;
      if (Number(a.registerStartTime || a.startRegisterTime) <= nowMs()) return;
      var pending=queue.some(function (e) { return watchEvent(e) && e.activityId===String(a.id); });
      var active=watchActive(a);
      vm.btnState=active?89:88;vm.disabled=watchRows===null||pending;
      vm.btnName=pending?'同步中…':watchRows===null?'读取提醒…':active?'取消提醒':'提醒报名';
      vm.bgColor=active?'#F5F6FA':'rgba(252,108,55,1)';vm.color=active?'#222':'#fff';
      if(vm.btnStyle){vm.btnStyle.backgroundColor=vm.bgColor;vm.btnStyle.color=vm.color;}
      if(vm.$forceUpdate)vm.$forceUpdate();
    });
  }
  function refreshWatches(force) {
    if(watchReading){if(force)watchReadAgain=true;return;}
    if(!binding||!binding.bound||(!force&&nowMs()-watchReadAt<2000))return;
    watchReading=true;watchReadAt=nowMs();
    post('/watch-state',{dev:binding.dev},true,function(status,body){
      watchReading=false;
      if(status===200&&body&&body.ok&&Array.isArray(body.watches)){watchRows=body.watches;paintWatches();}
      if(watchReadAgain){watchReadAgain=false;refreshWatches(true);}
    });
  }
  function safe(fn, fallback) { try { return fn(); } catch (e) { return fallback; } }
  function nowMs() { return Date.now(); }
  function read(key, fallback) { return safe(function () { return uni.getStorageSync(key) || fallback; }, fallback); }
  function write(key, value) { uni.setStorageSync(key, value); }
  function notice(key, text) {
    if (notices[key]) return;
    notices[key] = true;
    safe(function () { uni.showModal({title: 'QQ 活动提醒', content: text, showCancel: false, confirmText: '知道了'}); });
  }
  function randomHex() {
    if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
      var bytes = new Uint8Array(32), result = '';
      crypto.getRandomValues(bytes);
      for (var i = 0; i < bytes.length; i++) result += ('0' + bytes[i].toString(16)).slice(-2);
      return result;
    }
    if (typeof plus !== 'undefined' && plus.android) {
      var rng = plus.android.newObject('java.security.SecureRandom');
      var value = plus.android.newObject('java.math.BigInteger', 256, rng);
      var hex = plus.android.invoke(value, 'toString', 16);
      if (typeof hex === 'string' && /^[0-9a-f]{1,64}$/.test(hex)) return ('0000000000000000000000000000000000000000000000000000000000000000' + hex).slice(-64);
    }
    throw new Error('Secure random generator unavailable');
  }
  function prepare() {
    if (typeof bootstrap !== 'string' || !bootstrap) {
      notice('package', '请先在 提醒服务网站 绑定 QQ，再从网站下载专属安装包。此安装包没有账户授权。');
      return false;
    }
    if (binding) return true;
    var saved = read(STATE, null);
    var validDevice = saved && /^[A-Za-z0-9_-]{8,64}$/.test(saved.dev || '') && /^[0-9a-f]{64}$/.test(saved.device_secret || '');
    if (!validDevice || saved.bootstrap !== bootstrap) {
      // Keep the physical device identity across upgrades so a cancellation in
      // an updated APK still cancels this device's earlier reminder. The new
      // grant MUST be activated before that credential can send any new event.
      saved = {bootstrap: bootstrap, dev: validDevice ? saved.dev : 'd' + randomHex().slice(0, 48),
        device_secret: validDevice ? saved.device_secret : randomHex(), bound: false,
        last_at: validDevice ? Number(saved.last_at) || 0 : 0};
      // Never replay another website account's buffered registrations.
      write(QUEUE, []);
      write(STATE, saved);
    }
    binding = saved;
    var stored = read(QUEUE, []);
    queue = Array.isArray(stored) ? stored.filter(function (e) { return e && e.dev === binding.dev; }).slice(-200) : [];
    return true;
  }
  function post(path, data, authenticated, callback) {
    var headers = {'Content-Type': 'application/json'};
    if (authenticated) headers.Authorization = 'Bearer ' + binding.device_secret;
    try {
      originalRequest.call(uni, {url: CFG.base + path, method: 'POST', data: data, timeout: 8000, header: headers,
        success: function (res) { callback(res && res.statusCode || 0, res && res.data); },
        fail: function () { callback(0, null); }});
    } catch (e) { callback(0, null); }
  }
  function activate() {
    if (activating || !safe(prepare, false)) return;
    if (binding.bound) { flush(); refreshWatches(false); return; }
    activating = true;
    post('/activate', {bootstrap: bootstrap, dev: binding.dev, device_secret: binding.device_secret}, false, function (status, body) {
      activating = false;
      if (status === 200 && body && body.ok === true && body.bound === true) {
        binding.bound = true;
        safe(function () { write(STATE, binding); });
        flush();
        refreshWatches(true);
      } else if (status >= 400 && status < 500 && status !== 429) {
        notice('binding', '网站账户授权已失效或已在其他设备使用，请在 提醒服务网站 重新下载专属安装包。乐健本身的报名功能不受影响。');
      } else {
        notice('offline', '暂时无法连接 QQ 提醒服务，将自动重试。请保持应用联网；乐健的报名和取消仍正常进行。');
      }
    });
  }
  function flush() {
    if (sending || !binding || !binding.bound || !queue.length) return;
    sending = true;
    var event = queue[0];
    post('/hook', event, true, function (status, body) {
      sending = false;
      if ((status === 200 && body && body.ok === true) || status === 400 || status === 422 || status === 409) {
        queue = queue.filter(function (item) { return item.event_id !== event.event_id; });
        safe(function () { write(QUEUE, queue); });
        if (status === 409 && body && body.error === 'watch_limit') {
          safe(function(){uni.showModal({title:'提醒数量已满',content:'每个账户最多设置 4 个有效报名提醒，请先取消一个再添加。',showCancel:false});});
        } else if (status !== 200) notice('details', '这次活动信息不完整或已开放报名，未能设置提醒，请刷新活动后重试。');
        if (watchEvent(event)) refreshWatches(true);
        flush();
      } else if (status === 401 || status === 403) {
        notice('binding', 'QQ 提醒授权已失效，请在 提醒服务网站 重新下载专属安装包。乐健本身的报名功能不受影响。');
      } else {
        notice('offline', 'QQ 提醒暂未同步，已保存并会在应用联网时重试。请保持应用联网，尤其是取消报名后。');
      }
    });
  }
  function activity(value) {
    if (!value || typeof value !== 'object' || value.id == null) return null;
    if (!(value.startTime || value.title || value.name)) return null;
    return {id: String(value.id), title: String(value.title || value.name || '').slice(0,120),
      startTime: value.startTime || null, endTime: value.endTime || null,
      registerStartTime: value.registerStartTime || value.startRegisterTime || null,
      address: String(value.address || '').slice(0,200)};
  }
  function cache(value, depth) {
    if (!value || typeof value !== 'object' || depth > 5) return;
    var act = activity(value);
    if (act) {
      if (!detail[act.id]) detailIds.push(act.id);
      detail[act.id] = act;
      if (detailIds.length > 400) delete detail[detailIds.shift()];
    }
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length && i < 200; i++) cache(value[i], depth + 1);
    } else {
      var keys = Object.keys(value);
      for (var j = 0; j < keys.length && j < 60; j++) cache(value[keys[j]], depth + 1);
    }
  }
  function nextEvent(kind, id) {
    if (!safe(prepare, false)) { notice('setup', 'QQ 提醒初始化未完成，请保持应用联网后重试。乐健报名功能不受影响。'); return null; }
    binding.last_at = Math.max(nowMs(), (Number(binding.last_at) || 0) + 1);
    write(STATE, binding);
    return {v: 2, dev: binding.dev, event: kind, ok: true, activityId: id,
      event_id: randomHex(), at: binding.last_at, app: CFG.app};
  }
  function enqueue(event) {
    event.activity = detail[event.activityId] || {id: event.activityId};
    // Coalesce only before delivery; the server also deduplicates and orders.
    var same = function (item) { return item.activityId === event.activityId &&
      watchEvent(item) === watchEvent(event); };
    if (queue.some(function (item) { return same(item) && item.at > event.at; })) return;
    queue = queue.filter(function (item) { return !same(item); });
    queue.push(event);
    queue.sort(function (a, b) { return a.at - b.at; });
    if (queue.length > 200) {
      queue.shift();
      notice('full', 'QQ 提醒待同步活动过多，部分旧记录无法保留。请保持应用联网后核对报名及提醒。');
    }
    write(QUEUE, queue);
    paintWatches();
    activate();
  }
  function report(kind, id, options) {
    var event = nextEvent(kind, id);
    if (!event) return;
    if (kind === 'cancel' || (detail[id] && detail[id].startTime)) { enqueue(event); return; }
    // Read missing details only from the official API. Its login header is
    // never copied into our outbox or reminder service payload.
    originalRequest.call(uni, {url: 'https://cpes.legym.cn/education/app/activity/getStudentActivityDetail',
      method: 'GET', data: {activityId: id}, header: options.header || {}, timeout: 8000,
      success: function (res) {
        if (res && res.statusCode === 200 && res.data && res.data.code === 0) safe(function () { cache(res.data.data, 0); });
        safe(function () { enqueue(event); });
      }, fail: function () { safe(function () { enqueue(event); }); }});
  }
  function wrap(options) {
    if (!options || !options.url) return options;
    var match = /^https:\/\/cpes\.legym\.cn\/education\/app\/activity\/(signUp|cancelSignUp|getStudentActivityDetail|getActivityList|my\/getActivityList)(?:\?|$)/.exec(String(options.url));
    if (!match) return options;
    var action = match[1], isMutation = action === 'signUp' || action === 'cancelSignUp';
    if (isMutation && String(options.method || 'GET').toUpperCase() !== 'POST') return options;
    var data = options.data;
    if (typeof data === 'string') data = safe(function () { return JSON.parse(data); }, null);
    var id = data && data.activityId != null ? String(data.activityId) : '';
    var copied = {};
    Object.keys(options).forEach(function (key) { copied[key] = options[key]; });
    var callback = options.success;
    copied.success = function (response) {
      safe(function () {
        var body = response && response.data;
        if (!response || response.statusCode !== 200 || !body || body.code !== 0) return;
        if (!isMutation) { cache(body.data, 0); return; }
        var ok = action === 'signUp' ? body.data && body.data.success === true :
          body.data === true || (body.data && body.data.success === true);
        if (ok && id && id.length <= 64) report(action === 'signUp' ? 'signup' : 'cancel', id, options);
      });
      if (typeof callback === 'function') return callback.apply(this, arguments);
    };
    return copied;
  }
  var tries = 0;
  function boot() {
    tries++;
    if (typeof uni === 'undefined' || typeof uni.request !== 'function') {
      if (tries < 200) setTimeout(boot, 50);
      return;
    }
    if (uni.__ljSitePatched) return;
    originalRequest = uni.request;
    uni.request = function (options) { return originalRequest.call(uni, safe(function () { return wrap(options); }, options)); };
    uni.__ljSitePatched = true;
    uni.__ljSetupWatchButton = function (vm,value) {
      watchViews=watchViews.filter(function(v){return v.vm!==vm;});
      watchViews.push({vm:vm,activity:value});paintWatches();
      if(safe(prepare,false)){activate();refreshWatches(false);}
    };
    uni.__ljWatchRegistration = function (value, cancel) {
      safe(function () {
        var act = activity(value);
        if (!act || !(Number(act.registerStartTime) > nowMs())) {
          uni.showToast({title:'报名已开放或时间不可用，请刷新活动', icon:'none'}); return;
        }
        cache(value, 0);
        var event = nextEvent(cancel ? 'cancel_registration_watch' : 'watch_registration', act.id);
        if (!event) return;
        enqueue(event);
        uni.showToast({title:cancel?'取消请求已提交，联网同步后生效':'提醒请求已提交，请以 QQ 确认消息为准', icon:'none', duration:3000});
      });
    };
    setTimeout(activate, 6000);
    setInterval(activate, 30000);
    if (typeof uni.onNetworkStatusChange === 'function') safe(function () { uni.onNetworkStatusChange(function (state) { if (state.isConnected) activate(); }); });
  }
  boot();
})();
