const {test}=require('node:test');
const assert=require('node:assert/strict');
const Connection=require('../wwwroot/js/camera-connection-state.js');

test('a configured source locks its URL and connect button until confirmed disconnected',()=>{
  const state=new Connection();
  state.update({configured:true,streamAvailable:true});
  assert.equal(state.inputDisabled,true);assert.equal(state.connectDisabled,true);assert.equal(state.disconnectDisabled,false);
  state.update({configured:false,streamAvailable:false});
  assert.equal(state.inputDisabled,false);assert.equal(state.connectDisabled,false);assert.equal(state.disconnectDisabled,true);
});
test('temporary loss of pictures or service never unlocks an existing camera',()=>{
  const state=new Connection();state.update({configured:true,streamAvailable:true});
  state.update({configured:true,streamAvailable:false});assert.equal(state.locked,true);
  state.unavailable();assert.equal(state.locked,true);assert.equal(state.disconnectDisabled,false);
  state.update({configured:true,streamAvailable:true});assert.equal(state.locked,true);
});
test('configuration in flight locks input and all connection operations',()=>{
  const state=new Connection();state.busy=true;
  assert.equal(state.inputDisabled,true);assert.equal(state.connectDisabled,true);assert.equal(state.disconnectDisabled,true);
  state.busy=false;assert.equal(state.inputDisabled,false);
});
test('camera locks are independent; a new page restores state from the service',()=>{
  const a=new Connection(),b=new Connection();a.update({configured:true,streamAvailable:true});b.update({configured:false});
  assert.equal(a.locked,true);assert.equal(b.locked,false);
  const refreshed=new Connection();refreshed.update({configured:true,streamAvailable:true});assert.equal(refreshed.locked,true);
});
