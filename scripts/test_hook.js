'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const hook = fs.readFileSync(path.join(__dirname, '../patch/hook.js'), 'utf8');
const API = 'https://cpes.legym.cn/education/app/activity/';
const SITE = 'https://example.invalid/lejian';
let count = 0;
function test(name, fn) { fn(); count++; console.log('PASS', name); }
function harness(config = {}) {
  const store = config.store || {}, requests = [], modals = [], timers = [], intervals = [], delayed = [];
  const control = {offline: false, activationStatus: 200, deferredDetails: false, watches:[], watchLimit:false};
  let nextResponse, networkListener;
  const context = {
    __LEJIAN_PUBLIC_BASE__: SITE,
    __LEJIAN_BOOTSTRAP__: config.bootstrap === undefined ? 'test-one-use-grant' : config.bootstrap,
    crypto: config.noCrypto ? undefined : {getRandomValues: a => { a.set(crypto.randomBytes(a.length)); return a; }},
    setTimeout: (f, ms) => timers.push({f, ms}), setInterval: f => intervals.push(f),
    uni: {getStorageSync: k => store[k], setStorageSync: (k, v) => { store[k] = JSON.parse(JSON.stringify(v)); },
      showModal: value => modals.push(value), showToast: value => modals.push(value), onNetworkStatusChange: cb => { networkListener = cb; },
      request: function (options) {
        requests.push(options);
        let response;
        if (options.url === SITE + '/activate') response = {statusCode: control.activationStatus, data: {ok: control.activationStatus === 200, bound: true}};
        else if(options.url === SITE+'/watch-state') response={statusCode:200,data:{ok:true,watches:control.watches}};
        else if (options.url === SITE + '/hook') {
          if (control.offline) { if (options.fail) options.fail({errMsg:'offline'}); return {abort(){}}; }
          if(options.data.event==='watch_registration'&&!control.watchLimit)control.watches=[{activity_id:options.data.activityId,opening:options.data.activity.registerStartTime}];
          if(options.data.event==='cancel_registration_watch')control.watches=[];
          response = control.watchLimit?{statusCode:409,data:{ok:false,error:'watch_limit'}}:{statusCode: 200, data: {ok:true}};
        } else if (options.url === API + 'getStudentActivityDetail' && !nextResponse) {
          response = {statusCode: 200, data: {code:0,data:{id: options.data.activityId,title:'健身跑',startTime:'1793000000000',address:'操场'}}};
          if (control.deferredDetails) { delayed.push(() => options.success(response)); return {abort(){}}; }
        } else { response = nextResponse || {statusCode:200,data:{code:0,data:{}}}; nextResponse = null; }
        if (options.success) options.success.call({receiver:'original'}, response);
        return {abort(){}, marker:'request-task'};
      }}
  };
  if (config.native) context.plus = {android: {
    newObject: name => ({className:name}), invoke: (value, method, radix) => { assert.equal(method,'toString'); assert.equal(radix,16); return 'a1'; }
  }};
  vm.runInNewContext(hook, context, {filename:'hook.js'});
  const client = {store,requests,modals,control,delayed,
    watch: (value,cancel) => context.uni.__ljWatchRegistration(value,cancel),
    setupWatch: (component,value) => context.uni.__ljSetupWatchButton(component,value),
    hooks: () => requests.filter(r => r.url === SITE + '/hook'),
    activate: () => timers.find(t => t.ms === 6000).f(),
    retry: () => intervals[0](), reconnect: () => networkListener({isConnected:true}),
    request: (action, data, body, statusCode = 200, extra = {}) => {
      nextResponse = {statusCode,data:body};
      return context.uni.request(Object.assign({url: action.startsWith('http') ? action : API + action,method:'POST',data,
        header:{Authorization:'official-test-secret',Organization:'private-school'}},extra));
    }, cache: (id='A100', title='健身跑') => client.request('getActivityList',{}, {code:0,data:{items:[{id,title,startTime:'1793000000000',endTime:'1793003600000',address:'操场',mobile:'do-not-collect'}]}})
  };
  return client;
}
test('actual bundle uses expected student activity APIs and response checks', () => {
  const source=path.join(__dirname,'../work/www/app-service.js');
  if (!fs.existsSync(source)) { console.log('SKIP actual upstream bundle check; run scripts/test_build.py with private inputs'); return; }
  const original=fs.readFileSync(source,'utf8');
  for (const piece of ['a="education/app"','/activity/signUp','/activity/cancelSignUp','n.data.success?','0===n.code&&n.data&&']) assert(original.includes(piece),piece);
});
for (const title of ['健身跑-周二-九里','体育超市-羽毛球']) test(title + ' list registration captures allowed fields only', () => {
  const h=harness(); h.cache('A100',title); h.request('signUp',{activityId:'A100'},{code:0,data:{success:true}});
  assert.equal(h.hooks().length,1); const e=h.hooks()[0]; assert.equal(e.data.event,'signup'); assert.equal(e.data.activity.title,title);
  assert(!JSON.stringify(e).includes('official-test-secret')); assert(!JSON.stringify(e).includes('private-school')); assert(!JSON.stringify(e).includes('do-not-collect'));
  for(const field of ['token','user','org','device','qq']) assert(!(field in e.data));
  assert.match(e.header.Authorization,/^Bearer [0-9a-f]{64}$/); assert.match(e.data.event_id,/^[0-9a-f]{64}$/);
});
test('detail-page registration and callback RequestTask preserved', () => {
  const h=harness(); h.request('getStudentActivityDetail',{activityId:'A1'},{code:0,data:{id:'A1',title:'详情课',startTime:'1793000000000'}});
  let called=0; const callback=function(res){assert.equal(this.receiver,'original');assert.equal(res.data.code,0);called++;};
  const task=h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}},200,{success:callback});
  assert.equal(called,1);assert.equal(task.marker,'request-task');assert.equal(h.hooks()[0].data.activity.title,'详情课');
});
for (const response of [{code:0,data:false},{code:0,data:{success:false}},{code:1,data:true},{code:0,data:null},{}]) test('cancel failure ignored ' + JSON.stringify(response), () => {
  const h=harness(); h.request('cancelSignUp',{activityId:'A1'},response);assert.equal(h.hooks().length,0);
});
for (const response of [{code:0,data:true},{code:0,data:{success:true}}]) test('confirmed cancel reported ' + JSON.stringify(response), () => {
  const h=harness(); h.request('cancelSignUp',{activityId:'A1'},response);assert.equal(h.hooks().length,1);assert.equal(h.hooks()[0].data.event,'cancel');
});
for (const response of [{code:0,data:false},{code:0,data:{}},{code:0,data:{success:false}},{code:1,data:{success:true}},{},'invalid']) test('failed/unknown signup ignored ' + JSON.stringify(response), () => {
  const h=harness(); h.request('signUp',{activityId:'A1'},response);assert.equal(h.hooks().length,0);
});
for(const code of [401,403,500]) test('HTTP ' + code + ' ignored even success-looking payload', () => {
  const h=harness(); h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}},code);assert.equal(h.hooks().length,0);
});
for(const endpoint of ['signUpStatistics','signUp/extra','https://cpes.legym.cn/education/app/course/signUp','https://other.example/education/app/activity/signUp']) test('unrelated endpoint excluded ' + endpoint, () => {
  const h=harness();h.request(endpoint,{activityId:'A1'},{code:0,data:{success:true}});assert.equal(h.hooks().length,0);
});
test('missing id and GET cannot mutate reminder', () => {
  const h=harness();h.request('signUp',{}, {code:0,data:{success:true}});h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}},200,{method:'GET'});assert.equal(h.hooks().length,0);
});
test('missing cached details read from official API using only original official credentials', () => {
  const h=harness(); h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}});
  const read=h.requests.find(r=>r.url===API+'getStudentActivityDetail');assert.equal(read.header.Authorization,'official-test-secret');assert.equal(h.hooks()[0].data.activity.title,'健身跑');
});
test('offline cancel replaces pending signup and retries stable event id', () => {
  const h=harness();h.control.offline=true;h.cache();h.request('signUp',{activityId:'A100'},{code:0,data:{success:true}});
  h.request('cancelSignUp',{activityId:'A100'},{code:0,data:true});
  const queue=h.store.__lj_outbox_v2;assert.equal(queue.length,1);assert.equal(queue[0].event,'cancel');const id=queue[0].event_id;
  h.control.offline=false;h.reconnect();assert.equal(h.hooks().at(-1).data.event_id,id);assert.equal(h.store.__lj_outbox_v2.length,0);
});
test('offline events resume after restart without manual binding', () => {
  const h=harness();h.control.offline=true;h.request('cancelSignUp',{activityId:'A1'},{code:0,data:true});const storedSecret=h.store.__lj_site_binding_v2.device_secret;
  const next=harness({store:h.store});next.activate();assert.equal(next.hooks().length,1);assert.equal(next.hooks()[0].header.Authorization,'Bearer '+storedSecret);
});
test('personalized APK upgrade retains physical device but reauthorizes and clears pending events', () => {
  const h=harness();h.control.offline=true;h.request('cancelSignUp',{activityId:'A1'},{code:0,data:true});const old=h.store.__lj_site_binding_v2.device_secret;
  const oldDev=h.store.__lj_site_binding_v2.dev;
  const next=harness({store:h.store,bootstrap:'another-account-package'});next.activate();assert.equal(next.store.__lj_site_binding_v2.device_secret,old);assert.equal(next.store.__lj_site_binding_v2.dev,oldDev);assert.equal(next.hooks().length,0);assert.equal(next.store.__lj_outbox_v2.length,0);
  assert.equal(next.requests[0].url,SITE+'/activate');assert.equal(next.requests[0].data.bootstrap,'another-account-package');
});
test('changed grant cannot use old device credential before successful reauthorization', () => {
  const h=harness();h.activate();const next=harness({store:h.store,bootstrap:'rejected-upgrade-grant'});next.control.activationStatus=403;
  next.request('cancelSignUp',{activityId:'A1'},{code:0,data:true});assert.equal(next.hooks().length,0);assert.equal(next.store.__lj_site_binding_v2.bound,false);
});
test('late detail lookup carries older timestamp than newer cancellation', () => {
  const h=harness();h.control.deferredDetails=true;h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}});h.request('cancelSignUp',{activityId:'A1'},{code:0,data:true});h.delayed[0]();
  const events=h.hooks().map(r=>r.data);assert.equal(events[0].event,'cancel');assert.equal(events[1].event,'signup');assert(events[0].at>events[1].at);
});
test('secure native RNG fallback uses 256-bit Java BigInteger, no Math.random fallback', () => {
  const h=harness({noCrypto:true,native:true});h.activate();assert.match(h.store.__lj_site_binding_v2.device_secret,/^[0-9a-f]{64}$/);assert(!hook.includes('Math.random'));
});
test('no secure RNG fails closed without disturbing official callback', () => {
  const h=harness({noCrypto:true});let called=false;h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}},200,{success:()=>{called=true;}});assert(called);assert.equal(h.hooks().length,0);
});
test('generic package cannot reuse previous account secret', () => {
  const h=harness();h.activate();const next=harness({store:h.store,bootstrap:''});next.activate();assert.equal(next.requests.length,0);assert(next.modals.length);
});
test('activation failure does not transmit unauthenticated events or block signup', () => {
  const h=harness();h.control.activationStatus=403;h.request('cancelSignUp',{activityId:'A1'},{code:0,data:true});assert.equal(h.hooks().length,0);assert.equal(h.store.__lj_outbox_v2.length,1);assert(h.modals.length);
});
test('list and detail registration timestamps normalize without calling school signup', () => {
  for (const key of ['startRegisterTime','registerStartTime']) {
    const h=harness(), opening=Date.now()+3600000;
    h.watch({id:'A1',title:'健身跑',startTime:Date.now()+86400000,[key]:opening});
    assert.equal(h.hooks().length,1);
    assert.equal(h.hooks()[0].data.event,'watch_registration');
    assert.equal(h.hooks()[0].data.activity.registerStartTime,opening);
    assert.equal(h.requests.filter(r=>r.url.startsWith(API)).length,0);
  }
});
test('offline watch and actual signup queues stay independent', () => {
  const h=harness();h.control.offline=true;
  h.watch({id:'A1',title:'健身跑',startTime:Date.now()+86400000,startRegisterTime:Date.now()+3600000});
  h.request('signUp',{activityId:'A1'},{code:0,data:{success:true}});
  assert.equal(h.store.__lj_outbox_v2.length,2);
  h.control.offline=false;h.retry();assert.equal(h.store.__lj_outbox_v2.length,0);
});
test('missing or opened registration cannot schedule a watch', () => {
  const h=harness();
  h.watch({id:'A1',title:'健身跑',registerStartTime:Date.now()-1});
  h.watch({id:'A2',title:'健身跑'});
  assert.equal(h.hooks().length,0);
});
test('server state switches both buttons and cancellation clears state',()=>{
 const h=harness(),a={id:'A1',title:'test',startTime:Date.now()+86400000,startRegisterTime:Date.now()+3600000};
 const list={},detail={btnStyle:{}};h.setupWatch(list,a);h.setupWatch(detail,a);
 assert.equal(list.btnName,'提醒报名');h.watch(a);
 assert.equal(list.btnName,'取消提醒');assert.equal(detail.btnName,'取消提醒');assert.equal(detail.btnStyle.backgroundColor,'#F5F6FA');
 h.watch(a,true);assert.equal(list.btnName,'提醒报名');assert.equal(detail.btnName,'提醒报名');
});
test('account limit is terminal and never displays a false active watch',()=>{
 const h=harness(),a={id:'A1',title:'test',startTime:Date.now()+86400000,startRegisterTime:Date.now()+3600000},component={};
 h.setupWatch(component,a);h.control.watchLimit=true;h.watch(a);
 assert.equal(component.btnName,'提醒报名');assert.equal(h.store.__lj_outbox_v2.length,0);
 assert(h.modals.some(m=>m.title==='提醒数量已满'));
});
console.log('HOOK TESTS PASSED:',count);
