const {test}=require('node:test');
const assert=require('node:assert/strict');
const signals=require('../wwwroot/js/gate-signals.js');

function state(gate,occupied=true) {
  return {signalPolicyVersion:1,occupied,vehicle:occupied?{entry_camera:gate}:null,
    nextVehicle:occupied?null:{entry_camera:gate},greenGate:gate,
    signals:{A:gate==='A'?'GREEN':'RED',B:gate==='B'?'GREEN':'RED'}};
}

test('first gate A and first gate B each select exactly one green',()=>{
  for (const gate of ['A','B']) assert.equal(signals.select(state(gate)).greenGate,gate);
});

test('a waiting gate receives priority while D is free',()=>{
  assert.deepEqual(signals.select(state('B',false)),{A:'RED',B:'GREEN',greenGate:'B'});
});

test('unavailable, older server, invalid double green and mismatched priority fail to red',()=>{
  for (const payload of [null,{}, {...state('A'),issue:'conflict'},
    {...state('A'),signals:{A:'GREEN',B:'GREEN'}},
    {...state('A'),greenGate:'B'}, {...state('A'),vehicle:{entry_camera:'C'}}]) {
    assert.deepEqual(signals.select(payload),{A:'RED',B:'RED',greenGate:null});
  }
});

function lights(initial) {
  const classes={AR:'lamp-red',AG:'lamp-off',BR:'lamp-red',BG:'lamp-off'};
  if (initial) {classes[initial+'R']='lamp-off'; classes[initial+'G']='lamp-green';}
  const nodes={};
  for (const gate of ['A','B']) nodes[gate]={};
  for (const gate of ['A','B']) for (const [kind,suffix] of [['red','R'],['green','G']])
    nodes[gate][kind]={setAttribute(name,value){
      assert.equal(name,'class'); classes[gate+suffix]=value;
      // Kiểm tra SAU TỪNG thay thuộc tính, không chỉ trạng thái cuối.
      assert.ok(!(classes.AG==='lamp-green' && classes.BG==='lamp-green'));
    }};
  return {nodes,classes};
}

test('switching B to A and A to B never briefly enables two green lamps',()=>{
  for (const [previous,next] of [['B','A'],['A','B']]) {
    const view=lights(previous);
    signals.render(state(next),view.nodes);
    assert.equal(view.classes[next+'G'],'lamp-green');
    assert.equal(view.classes[previous+'R'],'lamp-red');
  }
});

test('connection loss switches the currently green lamp off',()=>{
  const view=lights('A'); signals.render(null,view.nodes);
  assert.deepEqual(view.classes,{AR:'lamp-red',AG:'lamp-off',BR:'lamp-red',BG:'lamp-off'});
});
