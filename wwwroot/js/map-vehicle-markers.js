// Dùng các nhãn có sẵn trên sơ đồ. Vị trí chỉ lấy từ cổng đã xác nhận;
// không nội suy đường đi hoặc giả lập xe giữa hai lần đọc camera.
(function(root) {
  function build(state,observations,currentPlates,now=Date.now()) {
    const vehicle=state&&!state.issue?state.vehicle:null;
    const exited=state&&!state.issue?state.lastExit:null;
    const waiting=state&&!state.issue?state.nextVehicle:null;
    const special=new Set([vehicle?.plate,exited?.plate,waiting?.plate].filter(Boolean));
    const markers=observations.filter(item=>!special.has(item.plate)).map(item=>({...item,priority:2,kind:'detected',
      status:!currentPlates.has(`${item.cameraId}:${item.plate}`)?'KHÔNG CÒN TRONG KHUNG':now-Date.parse(item.detectedAt)>30000?'LẦN CUỐI PHÁT HIỆN':'VỪA PHÁT HIỆN'}));
    if(waiting&&waiting.id!==vehicle?.id)markers.push({plate:waiting.plate,cameraId:waiting.entry_camera,detectedAt:waiting.entered_at,
      priority:0,kind:'waiting',status:'CHỜ NGOÀI D'});
    // Khi cùng biển đã có lượt vào mới, không ghim nhãn ra của lượt trước.
    if(exited&&exited.plate!==vehicle?.plate&&exited.plate!==waiting?.plate)markers.push({plate:exited.plate,cameraId:exited.exit_camera,
      detectedAt:exited.exited_at,priority:1,kind:'exited',status:`ĐÃ RA CỔNG ${exited.exit_camera}`});
    return markers.filter(item=>['A','B','C','E'].includes(item.cameraId))
      .sort((a,b)=>a.priority-b.priority||Date.parse(b.detectedAt)-Date.parse(a.detectedAt));
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={build};else root.MapVehicleMarkers={build};
})(typeof window!=='undefined'?window:globalThis);
