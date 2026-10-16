(() => {
  // Worker OCR ghi sự kiện/bằng chứng. Xóa chỉ ẩn hồ sơ khỏi danh sách,
  // không tự đổi cổng vào/ra hoặc giải phóng vùng D.
  const apiRoot = "http://127.0.0.1:5085";
  const byId = id => document.getElementById(id);
  const status = byId("recordsStatus");
  const detailDialog = byId("visitDetailDialog");
  const deleteDialog = byId("deleteVisitDialog");
  const exitDialog = byId("confirmExitDialog");
  const editorDialog = byId('visitEditorDialog');
  const recordsVisible=()=>!byId('recordsView').hidden && window.FunctionDrawer.isOpen();
  let editorVisit = null, appliedFilters = {};
  let currentVehicle = null, exitVehicle = null;
  let occupancyRevision = -1;
  let pendingDelete = null, mutationRunning = false;
  let offset = 0, total = 0, loading = false, requestVersion = 0;
  let renderedRows = '', renderedPage = '';
  const limit = 30;
  const listPolling = new window.ServicePolling(8000);
  let detailRequest = 0;
  const dateFormat = new Intl.DateTimeFormat("vi-VN", { timeZone:"Asia/Bangkok", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false });
  const formatDate = value => value ? dateFormat.format(new Date(value)) : "—";
  const states = { INSIDE:"Đang bên trong", COMPLETE:"Đã ra", WAITING:"Chờ ngoài D", NEEDS_REVIEW:"Chưa đủ thông tin" };

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text; // Không ghép dữ liệu OCR vào HTML.
    if (className) node.className = className;
    return node;
  }

  // Tách giờ/ngày thành hai dòng để bảng bên phải đọc được rõ hơn.
  // Giá trị API và múi giờ UTC+7 không thay đổi.
  function appendDate(cell, value) {
    if (!value) { cell.textContent='—'; return; }
    const parts=Object.fromEntries(dateFormat.formatToParts(new Date(value)).map(part=>[part.type,part.value]));
    const date=element('time',undefined,'visit-datetime');
    date.dateTime=value; date.title=formatDate(value);
    date.append(element('span',`${parts.hour}:${parts.minute}:${parts.second}`),element('small',`${parts.day}/${parts.month}/${parts.year}`));
    cell.append(date);
  }

  async function request(path, options = {}) {
    const {timeoutMs = 12000, ...fetchOptions} = options;
    const response = await fetch(`${apiRoot}${path}`, { ...fetchOptions, signal:AbortSignal.timeout(timeoutMs) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Không đọc được dữ liệu hồ sơ.");
    return data;
  }

  function duration(visit) {
    if (!visit.entered_at) return "—";
    const seconds = Math.max(0, Math.floor(((visit.exited_at ? Date.parse(visit.exited_at) : Date.now()) - Date.parse(visit.entered_at)) / 1000));
    const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
    return `${days ? `${days} ngày ` : ""}${hours} giờ ${minutes} phút ${seconds % 60} giây`;
  }

  function showView() {
    offset=0; requestVersion++;
    window.FunctionDrawer.open('recordsView','showRecordsBtn');
    // Menu chỉ còn Hồ sơ và Cài đặt; vẫn giữ sửa hồ sơ đã có trong chi tiết.
    updateWriterHealth(window.CameraRecognition.recordingAvailable());
    loadVisits();
  }
  byId('showRecordsBtn').addEventListener('click',showView);

  function updatePeriodFields() {
    const period = byId('filterPeriod').value;
    for (const [id,visible] of [['filterDayField',period==='day'],['filterMonthField',period==='month'],['filterFromField',period==='range'],['filterToField',period==='range'],['filterDateBasisField',period!=='all']]) byId(id).hidden=!visible;
  }
  byId('filterPeriod').addEventListener('change',updatePeriodFields);
  byId('recordsFilterForm').addEventListener('submit',event => {
    event.preventDefault();
    const filters = {};
    for (const [key,id] of [['plate','filterPlate'],['gate','filterGate'],['status','filterStatus']])
      if (byId(id).value && !byId(id).disabled) filters[key]=byId(id).value;
    const period=byId('filterPeriod').value;
    try {
      if (period==='day' || period==='month') {
        const value=byId(period==='day' ? 'filterDay' : 'filterMonth').value;
        if (!value) throw new Error('Hãy chọn ngày hoặc tháng cần lọc.');
        filters[period]=value;
      }
      if (period==='range') {
        const start=byId('filterFrom').value,end=byId('filterTo').value;
        if (!start && !end) throw new Error('Hãy nhập ít nhất thời điểm từ hoặc đến.');
        if (start) filters.from=new Date(start+'+07:00').toISOString();
        if (end) filters.to=new Date(end+'+07:00').toISOString();
        if (filters.from && filters.to && filters.from>=filters.to) throw new Error('Thời điểm đến phải sau thời điểm từ.');
      }
      if (period!=='all') filters.date_basis=byId('filterDateBasis').value;
      appliedFilters=filters; offset=0; requestVersion++;
      byId('filterSummary').textContent=Object.keys(filters).length ? '· Đã áp dụng' : '';
      loadVisits();
    } catch(error) { status.textContent=error.message; }
  });
  byId('clearFiltersBtn').addEventListener('click',() => {
    byId('recordsFilterForm').reset(); appliedFilters={}; updatePeriodFields();
    byId('filterSummary').textContent=''; offset=0; requestVersion++; loadVisits();
  });

  async function loadVisits(force = true) {
    if (loading || !listPolling.begin(force)) return;
    loading = true;
    let failed = true;
    const version = requestVersion;
    status.textContent = "Đang tải hồ sơ…";
    try {
      const filters={...appliedFilters};
      const query=new URLSearchParams({...filters,offset:String(offset),limit:String(limit),deleted:'0'});
      const data = await request(`/api/visits?${query}`);
      failed = false;
      if (version !== requestVersion) return;
      const storageLabel = data.storage?.backend === "sqlserver" ? `SQL Server · ${data.storage.database}` : "SQLite";
      byId("recordsStorageInfo").textContent = `${storageLabel} · Ngày giờ UTC+7 · ảnh bằng chứng theo từng lượt`;
      if (data.occupancy) {
        renderCurrentVisit(data.occupancy);
        window.dispatchEvent(new CustomEvent("vehicle-recording-result", {detail:{occupancy:data.occupancy}}));
      }
      const counts = data.counts || {};
      byId("visitCounts").textContent = `Kết quả hiện tại: ${counts.INSIDE || 0} xe đang bên trong · ${counts.WAITING || 0} xe chờ · ${data.closed_count ?? counts.COMPLETE ?? 0} lượt đã ra.`;
      total = data.total;
      // Giữ vị trí cuộn khi dữ liệu tự làm mới; không dựng lại bảng nếu chỉ
      // thời gian lưu lại đổi (cột đó được cập nhật riêng mỗi giây).
      const signature = JSON.stringify(data.items), page = `${offset}:${JSON.stringify(appliedFilters)}`;
      const wrap = byId('visitsTableBody').closest('.records-table-wrap');
      const scrollTop = page===renderedPage ? wrap.scrollTop : 0, scrollLeft = wrap.scrollLeft;
      if (signature!==renderedRows || page!==renderedPage) {
      const body = byId("visitsTableBody"); body.replaceChildren();
      if (!data.items.length) {
        const cell = body.insertRow().insertCell(); cell.colSpan = 10; cell.className = "records-empty";
        cell.append(element('span','Không có hồ sơ phù hợp. Hãy thử xóa bộ lọc.','records-empty-message'));
      }
      for (const visit of data.items) {
        const row = body.insertRow();
        row.insertCell().textContent = String(offset + row.rowIndex);
        row.insertCell().append(element("strong", visit.plate));
        if (visit.origin==='manual') row.cells[1].append(element('small','Nhập thủ công'));
        for (const direction of ["entry", "exit"]) {
          const cell = row.insertCell();
          cell.append(element("span", visit[`${direction}_camera`] ? `${visit.status === "WAITING" && direction === "entry" ? "Chờ cổng" : "Cổng"} ${visit[`${direction}_camera`]}` : "—"));
          appendDate(row.insertCell(),visit[direction === "entry" ? "entered_at" : "exited_at"]);
        }
        const time = row.insertCell(); time.textContent = duration(visit); time.dataset.enteredAt = visit.entered_at || ""; time.dataset.exitedAt = visit.exited_at || "";
        const state = row.insertCell(); state.append(element("span", visit.exited_at ? 'Đã ra' : states[visit.status] || visit.status, `badge ${visit.status === "COMPLETE" ? "green" : "amber"}`));
        const notes = [...new Set([visit.notes,visit.status==='WAITING' ? 'Chờ ngoài D' : null].filter(Boolean))].join(' · ');
        const noteCell = row.insertCell(), note = element('span',notes || '—','visit-row-note');
        note.title = notes; noteCell.append(note);
        const button = element("button", "Xem hồ sơ"); button.type = "button";
        button.setAttribute("aria-label", `Mở hồ sơ ${visit.plate}`);
        button.addEventListener("click", () => openDetail(visit.id));
        const action = element("button", "Xóa", "visit-delete-button"); action.type = "button";
        action.setAttribute("aria-label", `Xóa hồ sơ ${visit.plate}`);
        action.title = "Xóa hồ sơ khỏi danh sách";
        action.disabled = mutationRunning;
        action.addEventListener("click", () => {
          if (mutationRunning) return;
          pendingDelete = visit; byId("deleteVisitPlate").textContent = visit.plate;
          byId("deleteVisitError").hidden = true; window.FunctionDrawer.open('deleteVisitDialog'); if (!deleteDialog.open) deleteDialog.show();
          byId("cancelDeleteVisitBtn").focus();
        });
        const actions = element("div", undefined, "visit-row-actions"); actions.append(button, action);
        row.insertCell().append(actions);
      }
      renderedRows=signature; renderedPage=page;
      wrap.scrollTop=scrollTop; wrap.scrollLeft=scrollLeft;
      }
      status.textContent = `${data.total} hồ sơ`;
      byId("recordsPageInfo").textContent = data.total ? `${offset + 1}–${Math.min(offset + limit, data.total)} / ${data.total} lượt` : "0 lượt";
      byId("recordsPreviousBtn").disabled = offset === 0;
      byId("recordsNextBtn").disabled = offset + limit >= total;
    } catch (error) {
      status.textContent = error.message==='Failed to fetch' ? 'Chưa kết nối máy chủ hồ sơ. Nhấn làm mới để thử lại.' : error.message;
    } finally {
      loading = false;
      listPolling.finish(failed);
      // Nếu trang dữ liệu thay đổi trong lúc fetch, tải lại đúng trang mới.
      if (version !== requestVersion && recordsVisible()) loadVisits();
    }
  }

  async function removeVisitFromList(visit, button) {
    mutationRunning = true; button.disabled = true;
    const message = byId("recordsActionMessage"); message.hidden = true;
    try {
      // Giữ API lưu trữ hiện có: không xóa vĩnh viễn dữ liệu hay ảnh bằng chứng.
      const data = await request(`/api/visits/${visit.id}/trash`, {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({confirm:true})
      });
      // Trạng thái D do server tính từ lượt chưa ra; thao tác danh sách không
      // được dùng để bật xanh hoặc giả lập sự kiện ra.
      window.dispatchEvent(new CustomEvent("vehicle-recording-result", {detail:data}));
      message.hidden = false;
      message.textContent = `${visit.plate} đã được xóa khỏi danh sách hồ sơ.`;
      if (deleteDialog.open) deleteDialog.close();
      offset = 0; requestVersion++; loadVisits();
    } catch (error) {
      const target = deleteDialog.open ? byId("deleteVisitError") : message;
      target.hidden = false; target.textContent = error.message;
    } finally { mutationRunning = false; button.disabled = false; }
  }
  byId("confirmDeleteVisitBtn").addEventListener("click", () => {
    if (pendingDelete && !mutationRunning) removeVisitFromList(pendingDelete, byId("confirmDeleteVisitBtn"));
  });
  for (const id of ["closeDeleteVisitBtn", "cancelDeleteVisitBtn"]) byId(id).addEventListener("click", () => deleteDialog.close());
  deleteDialog.addEventListener("close", () => { pendingDelete = null; byId("reloadRecordsBtn").focus({preventScroll:true}); });

  byId("reloadRecordsBtn").addEventListener("click", () => loadVisits());
  byId("recordsPreviousBtn").addEventListener("click", () => { if (loading) return; offset = Math.max(0, offset - limit); requestVersion++; loadVisits(); });
  byId("recordsNextBtn").addEventListener("click", () => { if (loading || offset + limit >= total) return; offset += limit; requestVersion++; loadVisits(); });

  async function openDetail(id) {
    const version = ++detailRequest;
    const content = byId("visitDetailContent"); content.replaceChildren(element("p", "Đang tải hồ sơ…"));
    byId("visitDetailTitle").textContent = "Hồ sơ lượt xe";
    byId("visitDetailSubtitle").textContent = "Ngày giờ và bằng chứng từ camera";
    window.FunctionDrawer.open('visitDetailDialog');
    if (!detailDialog.open) detailDialog.show();
    try {
      const visit = await request(`/api/visits/${id}`);
      if (version !== detailRequest || !detailDialog.open) return;
      byId("visitDetailTitle").textContent = `Hồ sơ ${visit.plate}`;
      byId('visitDetailSubtitle').textContent = visit.origin==='manual' ? 'Hồ sơ nhập tay · ảnh bổ sung và lịch sử chỉnh sửa' : 'Ngày giờ và ảnh camera gốc · các bổ sung được ghi riêng';
      content.replaceChildren();
      const facts = element("dl", undefined, "visit-facts");
      for (const [label, value] of [["Trạng thái", states[visit.status]], ["Cổng vào", visit.entry_camera || "—"], ["Cổng ra", visit.exit_camera || "—"], ["Ngày giờ vào", formatDate(visit.entered_at)], ["Ngày giờ ra", formatDate(visit.exited_at)], ["Thời gian lưu lại", duration(visit)]]) {
        const fact = element("div"); fact.append(element("dt", label), element("dd", value)); facts.append(fact);
      }
      content.append(facts);
      const editButton=element('button','Sửa / bổ sung thông tin','primary'); editButton.type='button'; editButton.disabled=Boolean(visit.deleted_at);
      editButton.addEventListener('click',() => { detailDialog.close(); openEditor(visit); });
      content.append(editButton);
      if (visit.deleted_at) content.append(element('p','Hồ sơ đã xóa khỏi danh sách; không thể chỉnh sửa.','note'));
      content.append(element('p',visit.origin==='manual' ? 'Nguồn tạo: nhập thủ công' : 'Nguồn tạo: nhận diện camera','note'));
      if (visit.notes) content.append(element('p',`Ghi chú: ${visit.notes}`));

      for (const correction of visit.corrections || [])
        content.append(element("p", `Xác nhận thủ công: ra cổng ${correction.camera_id} lúc ${formatDate(correction.occurred_at)} · Lý do: ${correction.reason} · Xác nhận lúc ${formatDate(correction.confirmed_at)}`, "note"));
      if (visit.issue) content.append(element("p", visit.issue, "note"));
      const images = element("div", undefined, "evidence-grid");
      for (const passage of visit.passages) {
        const card = element("article", undefined, "evidence-card");
        card.append(element("h3", `${passage.direction === "entry" ? "Xe vào" : passage.direction === "exit" ? "Xe ra" : "Xe chờ ngoài D"} · Cổng ${passage.camera_id}`));
        card.append(element("p", formatDate(passage.occurred_at), "note"));
        for (const [file, type, description] of [[passage.scene_file, "scene", "Ảnh toàn cảnh lúc qua cổng"], [passage.crop_file, "crop", "Ảnh cắt biển số lúc qua cổng"]]) {
          const img = element("img"); img.src = `${apiRoot}/evidence/${file}`; img.alt = description; img.className = `evidence-${type}`; img.loading = "lazy";
          img.addEventListener("error", () => { img.hidden = true; card.append(element("p", `${description}: file không đọc được`, "note")); });
          card.append(img);
          const link = element("a", `${description} · mở ảnh gốc`); link.href = img.src; link.target = "_blank"; link.rel = "noopener"; card.append(link, element("br"));
        }
        card.append(element("p", `${passage.source_name} · RTSP · thời điểm luồng ${passage.media_time.toFixed(2)} giây`, "note"));
        const metadata = element("details"); metadata.append(element("summary", "Thông tin bằng chứng"));
        metadata.append(element("p", `Độ tin cậy OCR: ${Math.round(passage.confidence * 100)}%`, "note"));
        metadata.append(element("p", `SHA-256 toàn cảnh: ${passage.scene_sha256}`, "note"));
        metadata.append(element("p", `SHA-256 biển số: ${passage.crop_sha256}`, "note"));
        card.append(metadata); images.append(card);
      }
      content.append(images);
      for (const attachment of visit.attachments || []) {
        const card=element('article',undefined,'evidence-card');
        const direction={entry:'Ảnh vào',exit:'Ảnh ra',other:'Ảnh bổ sung'}[attachment.direction];
        card.append(element('h3',`${direction} · Người vận hành đính kèm`));
        card.append(element('p',`${formatDate(attachment.created_at)}${attachment.notes ? ' · '+attachment.notes : ''}`,'note'));
        const image=element('img'); image.src=`${apiRoot}/evidence/${attachment.file_name}`; image.alt=direction+' đính kèm thủ công'; image.className='evidence-scene'; image.loading='lazy';
        card.append(image); images.append(card);
      }
      if (visit.changes?.length) {
        const audit=element('details',undefined,'visit-audit'); audit.append(element('summary',`Lịch sử nhập / sửa (${visit.changes.length})`));
        const labels={plate:'Biển số',entered_at:'Giờ vào',exited_at:'Giờ ra',entry_camera:'Cổng vào',exit_camera:'Cổng ra',status:'Trạng thái',notes:'Ghi chú'};
        for (const change of visit.changes) {
          audit.append(element('p',`${change.action==='manual-create'?'Nhập lượt thủ công':change.action==='reconcile-waiting-exit'?'Đối chiếu lượt chờ → ra':'Chỉnh sửa'} · ${formatDate(change.created_at)} · Lý do: ${change.reason}`));
          for (const [key,label] of Object.entries(labels)) if (change.before[key]!==change.after[key]) {
            const display=value => value==null || value==='' ? '—' : key.endsWith('_at') ? formatDate(value) : key==='status' ? states[value] || value : value;
            audit.append(element('p',`${label}: ${display(change.before[key])} → ${display(change.after[key])}`,'note'));
          }
        }
        content.append(audit);
      }
    } catch (error) { if (version === detailRequest) content.replaceChildren(element("p", error.message, "note")); }
  }
  byId("closeVisitDetailBtn").addEventListener("click", () => detailDialog.close());
  detailDialog.addEventListener("close", () => detailRequest++);

  function localInput(value) {
    if (!value) return '';
    const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Bangkok',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(part=>[part.type,part.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
  }
  function openEditor(visit) {
    if (!visit?.id) return;
    editorVisit=visit;
    byId('visitEditorForm').reset(); byId('visitEditorError').hidden=true; byId('editFilesInfo').textContent='';
    byId('visitEditorTitle').textContent=`Sửa hồ sơ ${visit.plate}`;
    for (const [key,id] of [['plate','editVisitPlate'],['entry_camera','editEntryCamera'],['exit_camera','editExitCamera'],['notes','editVisitNotes']]) byId(id).value=visit?.[key] || '';
    byId('editEnteredAt').value=localInput(visit.entered_at);
    byId('editExitedAt').value=visit ? localInput(visit.exited_at) : '';
    window.FunctionDrawer.open('visitEditorDialog');
    if (!editorDialog.open) editorDialog.show();
    byId('editVisitPlate').focus();
  }
  for (const id of ['closeVisitEditorBtn','cancelVisitEditorBtn']) byId(id).addEventListener('click',() => { if (!mutationRunning) editorDialog.close(); });
  editorDialog.addEventListener('cancel',event => { if (mutationRunning) event.preventDefault(); });
  byId('editVisitFiles').addEventListener('change',() => {
    const files=[...byId('editVisitFiles').files];
    byId('editFilesInfo').textContent=files.map(file=>`${file.name} (${Math.ceil(file.size/1024)} KB)`).join(' · ');
  });
  function readFile(file) {
    return new Promise((resolve,reject) => {
      const reader=new FileReader(); reader.onload=() => resolve(reader.result); reader.onerror=() => reject(new Error('Không đọc được ảnh đã chọn.'));
      reader.readAsDataURL(file);
    });
  }
  byId('visitEditorForm').addEventListener('submit',async event => {
    event.preventDefault(); if (mutationRunning || !editorVisit) return;
    mutationRunning=true; byId('saveVisitEditorBtn').disabled=true; byId('visitEditorError').hidden=true;
    try {
      const payload={confirm:true,expected_version:editorVisit.edit_version,attachments:[]};
      payload.review_note=editorVisit?.review_note || '';
      for (const [key,id] of [['plate','editVisitPlate'],['entry_camera','editEntryCamera'],['exit_camera','editExitCamera'],['notes','editVisitNotes'],['reason','editVisitReason']]) payload[key]=byId(id).value;
      for (const [key,id] of [['entered_at','editEnteredAt'],['exited_at','editExitedAt']]) {
        const value=byId(id).value;
        payload[key]=window.recordEditorTimestamp(value,editorVisit?.[key]);
      }
      const files=[...byId('editVisitFiles').files];
      if (files.length>4 || files.some(file=>file.size>2000000)) throw new Error('Chọn tối đa 4 ảnh, mỗi ảnh không quá 2 MB.');
      for (const file of files) payload.attachments.push({data:await readFile(file),direction:byId('editAttachmentDirection').value,notes:byId('editAttachmentNotes').value});
      const path=`/api/visits/${editorVisit.id}/edit`;
      const result=await request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),timeoutMs:25000});
      window.dispatchEvent(new CustomEvent('vehicle-recording-result',{detail:result}));
      editorDialog.close(); offset=0; requestVersion++; loadVisits();
      byId('recordsActionMessage').hidden=false; byId('recordsActionMessage').textContent='Đã lưu hồ sơ và lịch sử chỉnh sửa.';
      if (result.visitIds?.[0]) openDetail(result.visitIds[0]);
    } catch(error) { byId('visitEditorError').hidden=false; byId('visitEditorError').textContent=error.message; }
    finally { mutationRunning=false; byId('saveVisitEditorBtn').disabled=false; }
  });

  window.addEventListener("vehicle-recording-result", event => {
    if (event.detail.state === "error") { byId("recordingHealthMessage").hidden = false; byId("recordingHealthMessage").textContent = event.detail.message; }
    if (event.detail.visitIds?.length && recordsVisible()) loadVisits(false);
  });
  function updateWriterHealth(available) {
    byId("recordingHealthMessage").hidden = available;
    if (!available) byId("recordingHealthMessage").textContent = "Bộ OCR đang dùng chưa hỗ trợ ghi hồ sơ. Cần chạy worker cập nhật tại 5084.";
  }
  window.addEventListener("camera-recording-health", event => updateWriterHealth(event.detail.available));
  function renderCurrentVisit(state) {
    if (state && state.revision < occupancyRevision) return;
    if (state) occupancyRevision = state.revision;
    currentVehicle = state?.vehicle || null;
    byId("currentVisitStatus").textContent = !state ? "Chưa đọc được trạng thái D" : state.issue ? state.issue : `${currentVehicle ? `D có xe · ${currentVehicle.plate}` : 'D trống'} · ${window.GateSignals.describe(state)}`;
    byId("currentVisitInfo").textContent = currentVehicle
      ? `Vào cổng ${currentVehicle.entry_camera} lúc ${formatDate(currentVehicle.entered_at)} · Chưa ghi cổng ra${currentVehicle.deleted_at ? " · Hồ sơ đã xóa khỏi danh sách, vẫn giữ D cho tới khi xe ra." : ""}`
      : state?.lastExit ? `${state.lastExit.plate} · Vào cổng ${state.lastExit.entry_camera} → Ra cổng ${state.lastExit.exit_camera} lúc ${formatDate(state.lastExit.exited_at)}` : "Camera đầu tiên xác nhận biển số sẽ ghi lượt vào.";
    for (const id of ["openCurrentVisitBtn", "openConfirmExitBtn"]) byId(id).hidden = !currentVehicle;
  }
  window.addEventListener("gate-occupancy-changed", event => renderCurrentVisit(event.detail));
  byId("openCurrentVisitBtn").addEventListener("click", () => { if (currentVehicle) openDetail(currentVehicle.id); });
  byId("openConfirmExitBtn").addEventListener("click", () => {
    if (!currentVehicle) return;
    exitVehicle = {...currentVehicle};
    byId("confirmExitPlate").textContent = exitVehicle.plate;
    byId("confirmExitCamera").value = "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Bangkok",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).formatToParts(new Date()).map(part => [part.type, part.value]));
    byId("confirmExitTime").value = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
    byId("confirmExitReason").value = ""; byId("confirmExitError").hidden = true;
    window.FunctionDrawer.open('confirmExitDialog'); if (!exitDialog.open) exitDialog.show();
  });
  for (const id of ["closeConfirmExitBtn", "cancelConfirmExitBtn"]) byId(id).addEventListener("click", () => exitDialog.close());
  byId("confirmExitForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (!exitVehicle || mutationRunning) return;
    mutationRunning = true; byId("submitConfirmExitBtn").disabled = true;
    try {
      const data = await request(`/api/visits/${exitVehicle.id}/confirm-exit`, {method:"POST",headers:{"Content-Type":"application/json"}, body:JSON.stringify({confirm:true,camera:byId("confirmExitCamera").value,occurredAt:new Date(`${byId("confirmExitTime").value}+07:00`).toISOString(),reason:byId("confirmExitReason").value})});
      window.dispatchEvent(new CustomEvent("vehicle-recording-result", {detail:data}));
      exitDialog.close(); requestVersion++; loadVisits();
    } catch (error) { byId("confirmExitError").hidden = false; byId("confirmExitError").textContent = error.message; }
    finally { mutationRunning = false; byId("submitConfirmExitBtn").disabled = false; }
  });
  // Chỉ cập nhật danh sách khi đang xem; không thêm GET camera cho các ô trống.
  setInterval(() => { if (!document.hidden && recordsVisible()) loadVisits(false); }, 1000);
  setInterval(() => {
    if (!recordsVisible()) return;
    for (const cell of byId("visitsTableBody").querySelectorAll("[data-entered-at]")) if (cell.dataset.enteredAt && !cell.dataset.exitedAt)
      cell.textContent = duration({ entered_at:cell.dataset.enteredAt });
  }, 1000);

})();
