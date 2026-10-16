(() => {
  // Chỉ backend sở hữu kết nối Modbus. Không mô phỏng ngõ ra bằng timer UI.
  const byId=id=>document.getElementById(id),api='http://127.0.0.1:5085/api/equipment';
  const poll=new window.ServicePolling(2000),forms={},populated={};
  let snapshot=null,busy=false,serviceAvailable=false,requestEpoch=0;
  const names={ALL_RED:'Hai hướng đỏ',A_GREEN:'A xanh · B đỏ',B_GREEN:'B xanh · A đỏ',CLOSED:'Đã đóng',OPEN:'Đã mở',OPENING:'Đang mở',CLOSING:'Đang đóng',STOPPED:'Dừng giữa hành trình'};
  const title=name=>name==='lights'?'Lights':'Barrier';
  const time=value=>value ? new Date(value).toLocaleTimeString('vi-VN',{timeZone:'Asia/Bangkok',hour12:false}) : '—';
  function selectDevice(name,focus=false) {
    for(const item of ['lights','barrier']) {
      const tab=byId(`equipment${title(item)}Tab`),selected=item===name;
      tab.setAttribute('aria-selected',String(selected)); tab.tabIndex=selected?0:-1;
      byId(`equipment${title(item)}Panel`).hidden=!selected;
      if(selected&&focus)tab.focus();
    }
  }
  for(const name of ['lights','barrier']) {
    const tab=byId(`equipment${title(name)}Tab`);
    tab.addEventListener('click',()=>selectDevice(name));
    tab.addEventListener('keydown',event=>{
      if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
        event.preventDefault();selectDevice(event.key==='Home'?'lights':event.key==='End'?'barrier':name==='lights'?'barrier':'lights',true);
      }
    });
  }

  // Tạo biểu mẫu riêng cho hai bộ điều khiển; địa chỉ thanh ghi không có giá trị
  // mặc định để tránh ghi nhầm ngõ ra khi khách dùng vùng nhớ khác.
  for (const name of ['lights','barrier']) {
    const details=document.createElement('details'); details.className='equipment-config'; details.open=true;
    const summary=document.createElement('summary'); summary.textContent='Cấu hình kết nối Modbus TCP'; details.append(summary);
    const form=document.createElement('form'); form.id=`equipment${title(name)}Form`;
    const grid=document.createElement('div'); grid.className='equipment-config-grid';
    const fields=[['host','IP bộ điều khiển','text','192.168.x.x'],['port','Cổng TCP','number','502'],['unit','Unit ID','number','1'],
      ['commandRegister','Thanh ghi lệnh','number','Offset từ 0'],['statusRegister','Thanh ghi trạng thái','number','Offset từ 0'],['heartbeatRegister','Thanh ghi heartbeat','number','Offset từ 0']];
    if(name==='barrier')fields.push(['obstacleInput','Địa chỉ cảm biến (DI)','number','Offset từ 0']);
    for (const [key,label,type,placeholder] of fields) {
      const field=document.createElement('label'); field.textContent=label;
      const input=document.createElement('input'); input.name=key; input.type=type; input.required=true; input.placeholder=placeholder; input.autocomplete='off';
      if(type==='number') { input.min=['port','unit'].includes(key)?'1':'0'; input.max=key==='unit'?'247':'65535'; input.step='1'; }
      if(key==='port')input.value='502'; if(key==='unit')input.value='1';
      field.append(input); grid.append(field);
    }
    form.append(grid);
    const label=document.createElement('label'); label.className='equipment-confirm';
    const confirm=document.createElement('input'); confirm.type='checkbox'; confirm.name='contractConfirmed'; confirm.required=true;
    label.append(confirm,document.createTextNode('PLC đã được lập trình theo bảng lệnh bên dưới; địa chỉ đã đối chiếu với kỹ thuật viên.')); form.append(label);
    const save=document.createElement('button'); save.type='submit'; save.textContent='Lưu cấu hình'; form.append(save);
    const note=document.createElement('p'); note.className='note'; note.textContent='Lưu trên máy chủ. Kết nối chỉ đọc; ngắt thiết bị trước khi đổi địa chỉ.'; form.append(note);
    details.append(form); byId(`equipment${title(name)}Config`).append(details); forms[name]=form;
    form.addEventListener('submit',event=>{
      event.preventDefault(); const values=Object.fromEntries(new FormData(form));
      for(const key of Object.keys(values))if(!['host','contractConfirmed'].includes(key))values[key]=Number(values[key]);
      values.contractConfirmed=confirm.checked;
      mutate('configure',{device:name,config:values},'Đã lưu cấu hình. Nhấn Kết nối để đọc thiết bị.');
    });
  }

  function render() {
    if(serviceAvailable) {
      const count=Object.values(snapshot?.devices||{}).filter(device=>device.connected).length;
      byId('equipmentServiceStatus').textContent=count?`Modbus TCP · Đang đọc ${count} bộ điều khiển`:'Dịch vụ Modbus TCP sẵn sàng · Chưa có thiết bị kết nối';
    }
    for(const name of ['lights','barrier']) {
      const prefix=`equipment${title(name)}`,device=snapshot?.devices?.[name],connected=Boolean(serviceAvailable&&device?.connected),enabled=connected&&device.enabled;
      const label=byId(prefix+'Connection'); label.textContent=!serviceAvailable?'Chưa đọc được dịch vụ':connected?(enabled?'Đã cho phép điều khiển':'Đã kết nối · chỉ đọc'):device?.error?'Lỗi kết nối':device?.config?'Chưa kết nối':'Chưa cấu hình';
      label.dataset.connected=String(connected);
      byId(prefix+'ConnectBtn').disabled=busy||!serviceAvailable||!device?.config||connected;
      byId(prefix+'DisconnectBtn').disabled=busy||!connected;
      byId(prefix+'EnableBtn').disabled=busy||!connected||enabled;
      for(const input of forms[name].querySelectorAll('input,button'))input.disabled=busy||connected||!serviceAvailable;
      byId(prefix+'Feedback').textContent=connected?`Phản hồi PLC lúc ${time(device.lastReadAt)} · ${names[device.feedback]||'Chưa xác định'}`:'Chưa có phản hồi PLC đang kết nối';
      const error=byId(prefix+'Error'); error.hidden=!device?.error; error.textContent=device?.error||'';
      byId(prefix+'Command').textContent=device?.lastCommand?`Đã gửi: ${device.lastCommand.label} · ${time(device.lastCommand.sentAt)}. Trạng thái thực tế xem ở phản hồi PLC.`:'Chưa gửi lệnh';
      if(device?.config&&!populated[name]) {
        for(const [key,value]of Object.entries(device.config)) {
          const input=forms[name].elements.namedItem(key); if(!input)continue;
          if(input.type==='checkbox')input.checked=Boolean(value); else input.value=String(value);
        }
        forms[name].closest('details').open=false; populated[name]=true;
      }
    }
    const lights=snapshot?.devices?.lights,barrier=snapshot?.devices?.barrier;
    const lightsReady=serviceAvailable&&lights?.connected&&lights.enabled&&!busy,barrierReady=serviceAvailable&&barrier?.connected&&barrier.enabled&&!busy;
    for(const gate of ['A','B']) {
      const node=byId('equipmentSignal'+gate),known=serviceAvailable&&lights?.connected&&lights.feedback;
      const green=known&&lights.feedback===gate+'_GREEN'; node.dataset.state=known?(green?'green':'red'):'unknown'; node.textContent=known?(green?'XANH · ĐƯỢC ĐI':'ĐỎ · DỪNG'):'Chưa xác định';
      byId('equipmentGreen'+gate+'Btn').disabled=!lightsReady;
    }
    byId('equipmentAllRedBtn').disabled=byId('equipmentAutoBtn').disabled=!lightsReady;
    byId('equipmentAutoBtn').setAttribute('aria-pressed',String(lightsReady&&lights.mode==='AUTO'));
    if(lights?.autoBlocked)byId('equipmentLightsCommand').textContent+=' '+lights.autoBlocked;
    const feedback=serviceAvailable&&barrier?.connected?barrier.feedback:null;
    byId('equipmentBarrierState').textContent=names[feedback]||'Chưa xác định';
    byId('equipmentObstacleState').textContent='Cảm biến: '+(!feedback||barrier.obstacle===null?'chưa có dữ liệu':barrier.obstacle?'CÓ VẬT CẢN':'Thông thoáng');
    byId('equipmentOpenBarrierBtn').disabled=!barrierReady||!lightsReady||!['A_GREEN','B_GREEN'].includes(lights?.feedback)||['OPEN','OPENING','CLOSING'].includes(feedback);
    byId('equipmentCloseBarrierBtn').disabled=!barrierReady||barrier.obstacle!==false||['CLOSED','OPENING','CLOSING'].includes(feedback);
    byId('equipmentStopBarrierBtn').disabled=!barrierReady;
    byId('equipmentRefreshBtn').disabled=busy;
  }

  async function request(path,options) {
    const response=await fetch(api+path,{...options,signal:AbortSignal.timeout(12000)});
    const data=await response.json();
    if(!response.ok) {const error=new Error(data.error||'Không đọc được thiết bị.');error.equipment=data.equipment;throw error;}
    return data;
  }
  async function load(force=false) {
    if(busy||!poll.begin(force))return;
    const epoch=requestEpoch;
    let failed=true;
    try {
      const data=await request(''); failed=false;
      if(epoch!==requestEpoch)return;
      snapshot=data; serviceAvailable=true;
    } catch(error) {
      if(epoch===requestEpoch) {serviceAvailable=false; byId('equipmentServiceStatus').textContent='Chưa đọc được dịch vụ thiết bị. Nhấn làm mới để kết nối lại.';}
    } finally {poll.finish(failed);render();}
  }
  async function mutate(path,payload,message) {
    if(busy)return;
    requestEpoch++; busy=true;render();
    const status=byId('equipmentActionStatus'); status.textContent='Đang thực hiện…'; status.dataset.error='false';
    try {
      snapshot=await request('/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,confirm:true})});
      serviceAvailable=true; status.textContent=message;
      if(path==='configure')populated[payload.device]=false;
    } catch(error) {if(error.equipment)snapshot=error.equipment;else serviceAvailable=false;status.textContent=error.message==='Failed to fetch'?'Chưa kết nối máy chủ thiết bị.':error.message;status.dataset.error='true';}
    finally {busy=false;render();}
  }
  for(const name of ['lights','barrier'])for(const action of ['connect','disconnect','enable']) {
    const id=`equipment${title(name)}${action[0].toUpperCase()+action.slice(1)}Btn`;
    byId(id).addEventListener('click',()=>mutate(action==='connect'?'connect':'action',{device:name,...(action==='connect'?{}:{action})},
      action==='connect'?'Đã đọc được thiết bị. Chưa cho phép gửi lệnh điều khiển.':action==='enable'?'Đã gửi lệnh khởi tạo điều khiển. Đối chiếu trạng thái phản hồi PLC.':'Đã ngắt kết nối; PLC xử lý mất heartbeat theo cấu hình.'));
  }
  const commands={equipmentGreenABtn:['lights','greenA'],equipmentGreenBBtn:['lights','greenB'],equipmentAllRedBtn:['lights','red'],equipmentAutoBtn:['lights','auto'],
    equipmentOpenBarrierBtn:['barrier','open'],equipmentCloseBarrierBtn:['barrier','close'],equipmentStopBarrierBtn:['barrier','stop']};
  for(const [id,[device,action]]of Object.entries(commands))byId(id).addEventListener('click',()=>mutate('action',{device,action},'Yêu cầu đã được xử lý. Xem lệnh đã gửi và phản hồi PLC để xác định trạng thái.'));
  byId('equipmentRefreshBtn').addEventListener('click',()=>load(true));
  byId('openEquipmentBtn').addEventListener('click',()=>{window.FunctionDrawer.open('equipmentView','openEquipmentBtn');load(true);});
  window.addEventListener('gate-occupancy-changed',event=>{
    const state=event.detail;
    byId('equipmentLiveStatus').textContent=!state?'Chưa đọc được D':state.issue|| (state.vehicle?`D có xe · ${state.vehicle.plate}`:'D trống');
    byId('equipmentLivePriority').textContent=window.GateSignals.describe(state)+' · Ưu tiên do camera xác định';
  });
  // Không tăng GET khi bảng chưa mở. Backend tiếp tục điều khiển dù đóng bảng.
  setInterval(()=>{if(!document.hidden&&!byId('equipmentView').hidden&&window.FunctionDrawer.isOpen())load();},1000);
  render();
})();
