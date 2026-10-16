const{test}=require('node:test'),assert=require('node:assert/strict');
const {build}=require('../wwwroot/js/map-vehicle-markers.js');
const at='2026-10-16T01:00:00Z';
test('the vehicle in D is shown by its existing D icon without duplicated gate labels',()=>{
  const marker={plate:'51K12345',cameraId:'A',detectedAt:at};
  assert.deepEqual(build({vehicle:{id:'one',plate:marker.plate,entry_camera:'A'}},[marker],new Set(['A:'+marker.plate])),[]);
});
test('waiting and exit notices stay at confirmed gates on the existing map markers',()=>{
  const state={nextVehicle:{id:'two',plate:'51F00651',entry_camera:'B',entered_at:at},lastExit:{id:'one',plate:'51K12345',entry_camera:'A',exit_camera:'C',exited_at:at}};
  const markers=build(state,[],new Set());assert.equal(markers.length,2);
  assert.equal(markers[0].cameraId,'B');assert.equal(markers[0].status,'CHỜ NGOÀI D');
  assert.equal(markers[1].cameraId,'C');assert.equal(markers[1].status,'ĐÃ RA CỔNG C');
});
test('old OCR locations and an older exit are suppressed when the same plate enters a new visit',()=>{
  const state={vehicle:{id:'new',plate:'51K12345',entry_camera:'B'},lastExit:{id:'old',plate:'51K12345',exit_camera:'C',exited_at:at}};
  assert.deepEqual(build(state,[{plate:'51K12345',cameraId:'A',detectedAt:at}],new Set()),[]);
});
test('loss of an OCR detection does not declare that the vehicle exited',()=>{
  const markers=build(null,[{plate:'51K12345',cameraId:'A',detectedAt:at}],new Set());
  assert.equal(markers[0].kind,'detected');assert.equal(markers[0].status,'KHÔNG CÒN TRONG KHUNG');
});
