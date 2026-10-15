(() => {
  // Worker OCR ghi sự kiện/bằng chứng. Xóa có khôi phục chỉ đổi danh sách,
  // không tự đổi cổng vào/ra hoặc giải phóng vùng D.
  const apiRoot = "http://127.0.0.1:5085";
  const byId = id => document.getElementById(id);
  const status = byId("recordsStatus");
  const detailDialog = byId("visitDetailDialog");
  const deleteDialog = byId("deleteVisitDialog");
  const exitDialog = byId("confirmExitDialog");
  let currentVehicle = null, exitVehicle = null;
  let occupancyRevision = -1;
  let deleted = false, pendingDelete = null, mutationRunning = false;
  let offset = 0, total = 0, loading = false, requestVersion = 0;
  const limit = 30;
  const listPolling = new window.ServicePolling(8000);
  let detailRequest = 0;
  const dateFormat = new Intl.DateTimeFormat("vi-VN", { timeZone:"Asia/Bangkok", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false });
  const formatDate = value => value ? dateFormat.format(new Date(value)) : "—";
  const states = { INSIDE:"Đang bên trong", COMPLETE:"Đã ra", WAITING:"Chờ ngoài D", NEEDS_REVIEW:"Cần kiểm tra" };

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text; // Không ghép dữ liệu OCR vào HTML.
    if (className) node.className = className;
    return node;
  }

  async function request(path, options = {}) {
    const response = await fetch(`${apiRoot}${path}`, { ...options, signal:AbortSignal.timeout(6000) });
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

  function showView(records) {
    byId("monitorView").hidden = records;
    byId("recordsView").hidden = !records;
    byId("showMonitorBtn").setAttribute("aria-pressed", String(!records));
    byId("showRecordsBtn").setAttribute("aria-pressed", String(records));
    // Giữ nguyên DOM ảnh/video và luồng RTSP khi chuyển màn hình.
    if (records) { updateWriterHealth(window.CameraRecognition.recordingAvailable()); loadVisits(); }
  }
  byId("showMonitorBtn").addEventListener("click", () => showView(false));
  byId("showRecordsBtn").addEventListener("click", () => showView(true));

  async function loadVisits(force = true) {
    if (loading || !listPolling.begin(force)) return;
    loading = true;
    let failed = true;
    const version = requestVersion;
    const selectedDeleted = deleted;
    status.textContent = "Đang tải hồ sơ…";
    try {
      const data = await request(`/api/visits?offset=${offset}&limit=${limit}&deleted=${selectedDeleted ? 1 : 0}`);
      failed = false;
      if (version !== requestVersion) return;
      const storageLabel = data.storage?.backend === "sqlserver" ? `SQL Server · ${data.storage.database}` : "SQLite";
      byId("recordsStorageInfo").textContent = `${storageLabel} · Ngày giờ UTC+7 · ảnh bằng chứng theo từng lượt`;
      if (data.occupancy) {
        renderCurrentVisit(data.occupancy);
        window.dispatchEvent(new CustomEvent("vehicle-recording-result", {detail:{occupancy:data.occupancy}}));
      }
      const counts = data.counts || {};
      byId("visitCounts").textContent = `Danh sách hồ sơ: ${counts.INSIDE || 0} xe đang trong D · ${counts.WAITING || 0} xe chờ · ${counts.COMPLETE || 0} lượt đã ra. Thùng rác xem ở tab riêng.`;
      total = data.total;
      const body = byId("visitsTableBody"); body.replaceChildren();
      if (!data.items.length) {
        const cell = body.insertRow().insertCell(); cell.colSpan = 6; cell.className = "records-empty";
        cell.textContent = selectedDeleted ? "Thùng rác trống." : "Chưa có hồ sơ trong danh sách này. Hồ sơ đã xóa nằm trong Thùng rác; camera sẽ ghi lượt mới khi xác nhận biển số.";
      }
      for (const visit of data.items) {
        const row = body.insertRow();
        row.insertCell().append(element("strong", visit.plate));
        for (const direction of ["entry", "exit"]) {
          const cell = row.insertCell();
          cell.append(element("span", visit[`${direction}_camera`] ? `${visit.status === "WAITING" && direction === "entry" ? "Chờ cổng" : "Cổng"} ${visit[`${direction}_camera`]}` : "—"));
          cell.append(element("small", formatDate(visit[direction === "entry" ? "entered_at" : "exited_at"])));
        }
        const time = row.insertCell(); time.textContent = duration(visit); time.dataset.enteredAt = visit.entered_at || ""; time.dataset.exitedAt = visit.exited_at || "";
        const state = row.insertCell(); state.append(element("span", states[visit.status] || visit.status, `badge ${visit.status === "COMPLETE" ? "green" : "amber"}`));
        if (visit.issue) state.append(element("small", visit.issue));
        const button = element("button", "Xem ảnh / hồ sơ"); button.type = "button";
        button.setAttribute("aria-label", `Mở hồ sơ ${visit.plate}`);
        button.addEventListener("click", () => openDetail(visit.id));
        const action = element("button", selectedDeleted ? "Khôi phục" : "Xóa", selectedDeleted ? "" : "visit-trash-button"); action.type = "button";
        action.setAttribute("aria-label", `${selectedDeleted ? "Khôi phục" : "Xóa"} hồ sơ ${visit.plate}`);
        action.title = selectedDeleted ? "Khôi phục hồ sơ và ảnh" : "Xóa hồ sơ vào Thùng rác";
        action.disabled = mutationRunning;
        action.addEventListener("click", () => {
          if (mutationRunning) return;
          if (selectedDeleted) changeDeleted(visit, false, action);
          else {
            pendingDelete = visit; byId("deleteVisitPlate").textContent = visit.plate;
            byId("deleteVisitError").hidden = true; deleteDialog.showModal();
            byId("cancelDeleteVisitBtn").focus();
          }
        });
        const actions = element("div", undefined, "visit-row-actions"); actions.append(button, action);
        row.insertCell().append(actions);
      }
      status.textContent = `${data.total} hồ sơ${selectedDeleted ? " trong Thùng rác" : ""}`;
      byId("recordsPageInfo").textContent = data.total ? `${offset + 1}–${Math.min(offset + limit, data.total)} / ${data.total} lượt` : "0 lượt";
      byId("recordsPreviousBtn").disabled = offset === 0;
      byId("recordsNextBtn").disabled = offset + limit >= total;
    } catch (error) {
      status.textContent = "Chưa kết nối máy chủ hồ sơ. Chạy Run-CameraSystem.cmd rồi thử lại.";
    } finally {
      loading = false;
      listPolling.finish(failed);
      // Nếu trang dữ liệu thay đổi trong lúc fetch, tải lại đúng trang mới.
      if (version !== requestVersion && !byId("recordsView").hidden) loadVisits();
    }
  }

  for (const [id, value] of [["showActiveVisitsBtn", false], ["showDeletedVisitsBtn", true]]) byId(id).addEventListener("click", () => {
    deleted = value; offset = 0; requestVersion++;
    byId("showActiveVisitsBtn").setAttribute("aria-pressed", String(!deleted));
    byId("showDeletedVisitsBtn").setAttribute("aria-pressed", String(deleted));
    loadVisits();
  });
  async function changeDeleted(visit, toDeleted, button) {
    mutationRunning = true; button.disabled = true;
    const message = byId("recordsActionMessage"); message.hidden = true;
    try {
      const data = await request(`/api/visits/${visit.id}/${toDeleted ? "trash" : "restore"}`, {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({confirm:true})
      });
      // Trạng thái D do server tính từ lượt chưa ra; thao tác danh sách không
      // được dùng để bật xanh hoặc giả lập sự kiện ra.
      window.dispatchEvent(new CustomEvent("vehicle-recording-result", {detail:data}));
      message.hidden = false;
      message.textContent = toDeleted ? `${visit.plate} đã chuyển vào Thùng rác; có thể khôi phục.` : `${visit.plate} đã được khôi phục cùng ảnh bằng chứng.`;
      if (deleteDialog.open) deleteDialog.close();
      offset = 0; requestVersion++; loadVisits();
    } catch (error) {
      const target = toDeleted && deleteDialog.open ? byId("deleteVisitError") : message;
      target.hidden = false; target.textContent = error.message;
    } finally { mutationRunning = false; button.disabled = false; }
  }
  byId("confirmDeleteVisitBtn").addEventListener("click", () => {
    if (pendingDelete && !mutationRunning) changeDeleted(pendingDelete, true, byId("confirmDeleteVisitBtn"));
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
    detailDialog.showModal();
    try {
      const visit = await request(`/api/visits/${id}`);
      if (version !== detailRequest || !detailDialog.open) return;
      byId("visitDetailTitle").textContent = `Hồ sơ ${visit.plate}`;
      content.replaceChildren();
      const facts = element("dl", undefined, "visit-facts");
      for (const [label, value] of [["Trạng thái", states[visit.status]], ["Cổng vào", visit.entry_camera || "—"], ["Cổng ra", visit.exit_camera || "—"], ["Ngày giờ vào", formatDate(visit.entered_at)], ["Ngày giờ ra", formatDate(visit.exited_at)], ["Thời gian lưu lại", duration(visit)]]) {
        const fact = element("div"); fact.append(element("dt", label), element("dd", value)); facts.append(fact);
      }
      content.append(facts);
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
    } catch (error) { if (version === detailRequest) content.replaceChildren(element("p", error.message, "note")); }
  }
  byId("closeVisitDetailBtn").addEventListener("click", () => detailDialog.close());
  detailDialog.addEventListener("close", () => detailRequest++);

  window.addEventListener("vehicle-recording-result", event => {
    if (event.detail.state === "error") { byId("recordingHealthMessage").hidden = false; byId("recordingHealthMessage").textContent = event.detail.message; }
    if (event.detail.visitIds?.length && !byId("recordsView").hidden) loadVisits(false);
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
    byId("currentVisitStatus").textContent = !state ? "Chưa đọc được trạng thái D" : state.issue ? state.issue : currentVehicle ? `D có xe · ${currentVehicle.plate}` : "D trống · đèn xanh";
    byId("currentVisitInfo").textContent = currentVehicle
      ? `Vào cổng ${currentVehicle.entry_camera} lúc ${formatDate(currentVehicle.entered_at)} · Chưa ghi cổng ra${currentVehicle.deleted_at ? " · Hồ sơ nằm trong Thùng rác, vẫn giữ D cho tới khi xe ra." : ""}`
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
    exitDialog.showModal();
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
  setInterval(() => { if (!document.hidden && !byId("recordsView").hidden) loadVisits(false); }, 1000);
  setInterval(() => {
    if (byId("recordsView").hidden) return;
    for (const cell of byId("visitsTableBody").querySelectorAll("[data-entered-at]")) if (cell.dataset.enteredAt && !cell.dataset.exitedAt)
      cell.textContent = duration({ entered_at:cell.dataset.enteredAt });
  }, 1000);

})();
