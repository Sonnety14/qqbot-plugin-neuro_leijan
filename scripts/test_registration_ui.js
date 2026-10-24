'use strict';
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict'), path=require('path');
const sandbox={webpackJsonp:[],uni:{__ljWatchRegistration:(value,cancel)=>({value,cancel}),
 __ljSetupWatchButton:(vm)=>Object.assign(vm,{btnName:'提醒报名',btnState:88,disabled:false})}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../work/registration-preview.js'),'utf8'),sandbox);
const modules=sandbox.webpackJsonp[0][1];
let count=0;
for(const factory of Object.values(modules)) {
  const source=factory.toString();
  if(!source.includes('initBtns:function')&&!source.includes('initBtn:function(t)'))continue;
  const exports={};factory({},exports,()=>({}));
  const methods=exports.default.methods;
  const activity={id:'test',state:0,startRegisterTime:Date.now()+3600000,registerStartTime:Date.now()+3600000};
  const detail=!!methods.initBtn;
  const component=detail?{detailData:activity}:{activitieItem:activity};
  (methods.initBtn||methods.initBtns).call(component,activity);
  assert.equal(component.btnName,'提醒报名');assert.equal(component.disabled,false);assert.equal(component.btnState,88);
  assert.equal(methods.handelClick.call(component).value,activity);
  component.btnState=89;component.btnStyle={};
  (methods.btnStyleFn||methods.setBtnStyle).call(component);
  assert.equal(component.btnStyle.backgroundColor||component.bgColor,'#F5F6FA');
  assert.equal(methods.handelClick.call(component).cancel,true);
  count++;
}
assert.equal(count,2);
console.log('PASS actual list and detail component methods expose reminder button and route click without signup');
