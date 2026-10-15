(() => {
  // Thao tác ngay cạnh camera. Không di chuyển/tạo lại node ảnh hoặc RTSP.
  const drawer=document.getElementById('functionDrawer');
  const workspace=document.getElementById('monitorWorkspace');
  const toggle=document.getElementById('appMenuToggle');
  const content=document.getElementById('functionDrawerContent');
  const paneIds=['recordsView','visitEditorDialog','visitDetailDialog','cameraSettingsDialog','confirmExitDialog','deleteVisitDialog'];
  const panes=paneIds.map(id=>document.getElementById(id));
  let activePane=null;
  let recordsContext='showRecordsBtn';
  for (const pane of panes) {
    pane.classList.add('drawer-pane'); content.append(pane); pane.hidden=true;
    if (pane.tagName==='DIALOG') pane.addEventListener('close',() => {
      if (activePane!==pane.id) return;
      open(pane.id==='cameraSettingsDialog' ? null : 'recordsView');
    });
  }
  function open(paneId=activePane,context=null) {
    activePane=paneId;
    if (context) recordsContext=context;
    drawer.hidden=false; workspace.classList.add('drawer-open');
    toggle.setAttribute('aria-expanded','true'); toggle.setAttribute('aria-label','Thu gọn bảng chức năng');
    for (const pane of panes) pane.hidden=pane.id!==paneId;
    const selected=paneId==='cameraSettingsDialog' ? 'openSettingsBtn' : paneId==='visitEditorDialog' ? 'newVisitBtn' : paneId ? recordsContext : null;
    for (const button of document.querySelectorAll('#appMenu button')) button.setAttribute('aria-pressed',String(button.id===selected));
    document.getElementById('drawerEmptyNote').hidden=Boolean(paneId);
  }
  function close() {
    // Giữ biểu mẫu đang điền khi thu gọn, mở lại không mất dữ liệu nhập dở.
    drawer.hidden=true; workspace.classList.remove('drawer-open');
    toggle.setAttribute('aria-expanded','false'); toggle.setAttribute('aria-label','Mở bảng chức năng'); toggle.focus();
  }
  toggle.addEventListener('click',() => drawer.hidden ? open() : close());
  document.getElementById('closeFunctionDrawerBtn').addEventListener('click',close);
  document.addEventListener('keydown',event => { if (event.key==='Escape' && !drawer.hidden) close(); });
  window.FunctionDrawer={open,close,isOpen:()=>!drawer.hidden};
})();
