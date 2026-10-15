const $ = id => document.getElementById(id);
let currentFramePlates = new Set();
const plateDetections = new Map();
const cameraConfiguredStates = { A:false, B:false, C:false, E:false };
let mapMarkerSignature = "";
const cameraMarkerSlots = {
  A:[[54,427],[54,476],[54,525]], B:[[449,481],[449,530],[449,579]],
  C:[[453,115],[453,164],[453,213]], E:[[199,138],[199,187],[199,236]]
};
let expandedCamera = null;
let cameraDetailReturnFocus = null;
let cameraDetailInertElements = [];

function openCameraDetail(card) {
  if (expandedCamera) return;
  expandedCamera = card;
  cameraDetailReturnFocus = document.activeElement;
  const cameraId = card.id.replace(/^camera|Card$/g, "");
  card.classList.add("is-expanded");
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", `camera${cameraId}Title`);
  card.querySelector(".camera-detail-tools").hidden = false;
  $("cameraDetailBackdrop").hidden = false;
  document.body.classList.add("camera-detail-open");

  // Inert the other controls without moving or replacing any live camera image.
  cameraDetailInertElements = [...document.querySelectorAll(
    ".app-header, footer, .panel, .camera-panel > :not(.camera-grid), .camera-card"
  )].filter(element => element !== card && !element.contains(card))
    .map(element => ({ element, wasInert: element.inert }));
  for (const { element } of cameraDetailInertElements) element.inert = true;
  card.querySelector(".camera-close").focus({ preventScroll: true });
}

function closeCameraDetail() {
  if (!expandedCamera) return;
  expandedCamera.classList.remove("is-expanded");
  expandedCamera.removeAttribute("role");
  expandedCamera.removeAttribute("aria-modal");
  expandedCamera.removeAttribute("aria-labelledby");
  expandedCamera.querySelector(".camera-detail-tools").hidden = true;
  $("cameraDetailBackdrop").hidden = true;
  document.body.classList.remove("camera-detail-open");
  for (const { element, wasInert } of cameraDetailInertElements) element.inert = wasInert;
  cameraDetailInertElements = [];
  const card = expandedCamera;
  expandedCamera = null;
  const returnFocus = cameraDetailReturnFocus?.matches("button")
    ? cameraDetailReturnFocus : card.querySelector(".camera-expand");
  returnFocus?.focus({ preventScroll: true });
  cameraDetailReturnFocus = null;
}

for (const card of document.querySelectorAll(".camera-card")) {
  card.querySelector(".camera-expand").addEventListener("click", () => openCameraDetail(card));
  card.querySelector(".camera-close").addEventListener("click", closeCameraDetail);
  card.addEventListener("click", event => {
    if (!event.target.closest("button, video, input, label, select, summary, details") && !window.getSelection()?.toString()) openCameraDetail(card);
  });
}
$("cameraDetailBackdrop").addEventListener("click", closeCameraDetail);
document.addEventListener("keydown", event => {
  if (!expandedCamera) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeCameraDetail();
  } else if (event.key === "Tab") {
    const controls = [...expandedCamera.querySelectorAll("button:not(:disabled), video[controls], summary")]
      .filter(element => element.getClientRects().length && !element.closest("[hidden]"));
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
      event.preventDefault(); (event.shiftKey ? last : first)?.focus({ preventScroll: true });
    }
  }
});
function normalizePlate(value) {
  return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}
function formatDetectionTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "không rõ thời điểm" : date.toLocaleString("vi-VN", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}
function recordPlateDetection(data) {
  const plate = normalizePlate(data.plate);
  if (!/^\d{2}[A-Z]{1,2}\d{4,6}$/.test(plate))
    return null;

  const cameraId = data.cameraId;
  if (!["A", "B", "C", "E"].includes(cameraId)
      || !(cameraConfiguredStates[cameraId] || window.CameraRecognition?.hasVisualSource(cameraId)))
    return null;
  const detectedAt = data.detectedAt && !Number.isNaN(Date.parse(data.detectedAt))
    ? new Date(data.detectedAt).toISOString()
    : new Date().toISOString();
  const detection = {
    plate,
    rawText: String(data.rawText || data.plate || ""),
    cameraId,
    detectedAt,
    confidence: Number(data.confidence) || 0,
    sourceName: data.sourceName || `Camera ${cameraId}`,
    sourceKey: data.sourceKey,
    videoTime: data.videoTime
  };
  plateDetections.set(`${cameraId}:${plate}`, detection);
  renderPlateDetections([]);
  return detection;
}
function renderPlateDetections() {
  const markers = $("plateMarkers");
  if (!markers) return;
  const perCamera = new Map();
  const latest = [...plateDetections.values()].sort((a, b) => Date.parse(b.detectedAt) - Date.parse(a.detectedAt))
    .filter(detection => {
      const count = perCamera.get(detection.cameraId) || 0;
      perCamera.set(detection.cameraId, count + 1);
      return count < cameraMarkerSlots[detection.cameraId].length;
    });
  const detectionsByCamera = new Map();
  for (const detection of latest) {
    const sameCamera = detectionsByCamera.get(detection.cameraId) || [];
    sameCamera.push(detection);
    detectionsByCamera.set(detection.cameraId, sameCamera);
  }

  const markerData = latest.map(detection => {
    const elapsed = Date.now() - Date.parse(detection.detectedAt);
    const status = !currentFramePlates.has(`${detection.cameraId}:${detection.plate}`)
      ? "KHÔNG CÒN PHÁT HIỆN TRONG KHUNG"
      : elapsed > 30_000 ? "LẦN CUỐI PHÁT HIỆN" : "VỪA PHÁT HIỆN";
    const listAtCamera = detectionsByCamera.get(detection.cameraId) || [];
    const index = listAtCamera.indexOf(detection);
    const slots = cameraMarkerSlots[detection.cameraId];
    const [x, y] = slots[index] || slots[slots.length - 1];
    return { detection, status, x, y };
  });
  const signature = JSON.stringify(markerData.map(({ detection, status, x, y }) =>
    [detection.plate, detection.cameraId, detection.detectedAt, status, x, y]));
  if (signature !== mapMarkerSignature) {
    const svgNamespace = "http://www.w3.org/2000/svg";
    const fragment = document.createDocumentFragment();
    for (const { detection, status, x, y } of markerData) {
      const marker = document.createElementNS(svgNamespace, "g");
      marker.setAttribute("class", `plate-marker${status.startsWith("LẦN CUỐI") ? " stale" : ""}${status.startsWith("ĐÃ RỜI") ? " departed" : ""}`);
      marker.setAttribute("transform", `translate(${x} ${y})`);
      const title = document.createElementNS(svgNamespace, "title");
      title.textContent = `${detection.plate} · Camera ${detection.cameraId} · ${formatDetectionTime(detection.detectedAt)} · ${status}`;
      marker.append(title);
      const background = document.createElementNS(svgNamespace, "rect");
      background.setAttribute("width", "150");
      background.setAttribute("height", "42");
      background.setAttribute("rx", "5");
      marker.append(background);
      for (const [className, text, offsetY] of [
        ["", detection.plate, 15],
        ["marker-camera", `CAM ${detection.cameraId} · ${formatDetectionTime(detection.detectedAt)}`, 28],
        ["marker-status", status, 39]
      ]) {
        const label = document.createElementNS(svgNamespace, "text");
        if (className) label.setAttribute("class", className);
        label.setAttribute("x", "6");
        label.setAttribute("y", String(offsetY));
        label.textContent = text;
        marker.append(label);
      }
      fragment.append(marker);
    }
    markers.replaceChildren(fragment);
    mapMarkerSignature = signature;
  }

  const note = $("mapDetectionNote");
  if (note) note.textContent = latest.length
    ? `${latest.length} nhãn biển số theo từng camera và lần phát hiện.`
    : "Chờ biển số xác nhận từ camera đang kết nối.";

}
const framePlatesByCamera = new Map();
let occupancyRevision = -1;
// Chỉ trạng thái bền vững từ worker/API hồ sơ được phép thay đổi xe ở D và đèn.
// Kết quả OCR ở cổng vẫn có thể hiển thị, nhưng không tự tạo chuyến trên trình duyệt.
function renderOccupancy(state) {
  if (state && state.revision < occupancyRevision) return;
  if (state) occupancyRevision = state.revision;
  const occupied = state?.occupied, vehicle = state?.vehicle;
  const badge = $("bridgeBadge");
  badge.textContent = !state ? "D · CHƯA ĐỌC ĐƯỢC TRẠNG THÁI" : state.issue ? "D · CẦN KIỂM TRA" : occupied ? `D CÓ XE · ${vehicle?.plate || ""}` : "D TRỐNG";
  badge.className = `badge ${state && !occupied ? "green" : "red"}`;
  document.querySelector(".map-site").classList.toggle("d-occupied", Boolean(occupied));
  $("activeDVehicle").style.display = vehicle ? "" : "none";
  $("activeDPlate").textContent = vehicle?.plate || "";
  $("activeDVehicle").querySelector("title").textContent = vehicle ? `${vehicle.plate} · vào cổng ${vehicle.entry_camera} · ${formatDetectionTime(vehicle.entered_at)}` : "";
  const note = $("gateEventNote"), lastExit = state?.lastExit;
  // Cổng vào là nơi đọc đầu tiên; cổng ra là camera khác đọc đúng cùng biển.
  // Hiển thị ngay trên sơ đồ, cả sau reload, để không phải mở lịch sử mới biết.
  note.textContent = !state ? "Chưa đọc được lượt xe đang theo dõi."
    : state.issue ? state.issue
    : vehicle ? `${vehicle.plate} · Vào cổng ${vehicle.entry_camera} lúc ${formatDetectionTime(vehicle.entered_at)} · Chưa có cổng ra`
    : lastExit ? `${lastExit.plate} · Vào cổng ${lastExit.entry_camera} → Ra cổng ${lastExit.exit_camera} lúc ${formatDetectionTime(lastExit.exited_at)} · Đã mở xanh`
    : "D trống · Chờ biển số đầu tiên để ghi cổng vào.";
  for (const camera of ["A", "B"]) {
    $(`svg${camera}R`).setAttribute("class", state && !occupied ? "lamp-off" : "lamp-red");
    $(`svg${camera}G`).setAttribute("class", state && !occupied ? "lamp-green" : "lamp-off");
  }
  window.dispatchEvent(new CustomEvent("gate-occupancy-changed", {detail:state}));
}
const occupancyPolling = new window.ServicePolling(2000);
async function refreshOccupancy(force = false) {
  if (!occupancyPolling.begin(force)) return;
  let failed = true;
  try {
    const response = await fetch("http://127.0.0.1:5085/api/occupancy", { signal:AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error("Không đọc được trạng thái D");
    renderOccupancy(await response.json());
    failed = false;
  } catch { renderOccupancy(null); }
  finally { occupancyPolling.finish(failed); }
}
window.addEventListener("vehicle-recording-result", event => {
  if (event.detail.occupancy) renderOccupancy(event.detail.occupancy);
  const note = $("cameraRecordingNote");
  if (event.detail.state === "error") note.textContent = event.detail.message;
  else if (event.detail.plates?.length) {
    const messages = {entry:"Đã lưu lượt vào · D có xe",exit:"Đã lưu cổng ra · D trống",waiting:"Đã lưu xe chờ ngoài D",'already-inside':"Xe vẫn trong D · chờ camera khác đọc cổng ra",'preview-only':"Nguồn xem trước · chưa ghi hồ sơ vào–ra",'stale-frame':"Khung đã cũ · chờ khung mới để lưu hồ sơ",'source-changed':"Nguồn vừa thay đổi · đang xác nhận lại"};
    if (messages[event.detail.state]) note.textContent = `CAM ${event.detail.cameraId} · ${event.detail.plates.join(" · ")} · ${messages[event.detail.state]}`;
  }
});
refreshOccupancy();
// Khi server tắt, giãn lần thử lại đến 30 giây, không gửi GET mỗi 2 giây.
setInterval(() => { if (!document.hidden) refreshOccupancy(); }, 1000);
window.CameraRecognition.init({
  onReset: (cameraId, reason, previousKey, sourceChanged) => {
    framePlatesByCamera.delete(cameraId);
    currentFramePlates = new Set([...framePlatesByCamera.values()].flatMap(values => [...values]));
    if (sourceChanged) for (const [key, detection] of plateDetections) if (detection.cameraId === cameraId) plateDetections.delete(key);
    mapMarkerSignature = ""; renderPlateDetections([]);
  },
  onCurrent: (cameraId, detections, frame) => {
    cameraConfiguredStates[cameraId] = window.CameraRecognition.hasVisualSource(cameraId);
    framePlatesByCamera.set(cameraId, new Set(detections.filter(item => item.confirmed).map(item => `${cameraId}:${item.plate}`)));
    currentFramePlates = new Set([...framePlatesByCamera.values()].flatMap(values => [...values]));
    if (frame) for (const detection of detections.filter(item => item.confirmed))
      recordPlateDetection({ ...detection, cameraId, detectedAt: frame.detectedAt,
        sourceName: frame.sourceName, sourceKey: frame.sourceKey, videoTime: frame.videoTime });
    renderPlateDetections([]);
  }
});
