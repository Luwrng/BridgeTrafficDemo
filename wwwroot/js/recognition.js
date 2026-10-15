(() => {
  const cameraIds = ["A", "B", "C", "E"];
  const helperUrl = "http://127.0.0.1:5083";
  const paddleUrl = "http://127.0.0.1:5084";
  let recognitionUrl = paddleUrl;
  let recordingAvailable = false;
  const controllers = new Map();
  let available = false;
  let refreshRunning = false;
  let sourceAVersion = 0;
  const statusPolling = new window.CameraStatusPolling(cameraIds);
  let nextHealthCheckAt = 0;
  const node = id => document.getElementById(id);

  function createController(cameraId) {
    const aliases = {recognitionPreview: "camera" + cameraId + "RecognitionPreview",
      recognitionCrop: "camera" + cameraId + "RecognitionCrop",
      recognitionFrameInfo: "camera" + cameraId + "RecognitionFrameInfo",
      recognitionCount: "camera" + cameraId + "RecognitionCount",
      recognitionCurrentList: "camera" + cameraId + "RecognitionCurrentList",
      videoSourceName: "camera" + cameraId + "VideoSourceName",
      videoBackBtn: "camera" + cameraId + "VideoBackBtn",
      videoForwardBtn: "camera" + cameraId + "VideoForwardBtn",
      videoPlayBtn: "camera" + cameraId + "VideoPlayBtn",
      recognizeStillBtn: "camera" + cameraId + "RecognizeStillBtn",
      recognitionPauseBtn: "camera" + cameraId + "RecognitionPauseBtn",
      videoFile: "camera" + cameraId + "VideoFile",
      useLiveSourceBtn: "camera" + cameraId + "UseLiveSourceBtn"};
    const el = id => node(aliases[id] || id.replace(/^cameraA/, `camera${cameraId}`));
  const state = { kind: "live", name: `Camera ${cameraId} · RTSP`, epoch: 0, key: "", processing: false,
    enabled: true, frame: 0, lastToken: "", startedAt: performance.now(), objectUrl: null,
    request: null, current: [], activeKeys: new Set(), lastResultAt: 0,
    lastSample: null, hooks: null, timer: null, nextAttemptAt: 0, passSequence: 0,
    videoFrameTime: null, videoFrameNumber: null, frameCallback: null, videoFrameCanvas: null,
    sceneSample: null, identity: "", liveName: `Camera ${cameraId} · RTSP`, liveStatus: null,
    liveImageUrl: null, liveFrameId: null, frameLoading: false, imageDecoding: false,
    liveQueue: [], liveFrameAt: null, liveFrameTime: null, lastPlayedAt: 0, nextPlaybackAt: 0,
    speed: 1, autoSlow: false, effectiveSpeed: 1, engineSession: null };
  const formatTime = value => {
    const seconds = Math.max(0, Number(value) || 0);
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
  };
  const badge = (id, text, tone = "amber") => {
    el(id).textContent = text;
    el(id).className = `badge ${tone}`;
  };
  const source = () => state.kind === "file" ? el("cameraAVideo") : el("cameraAFeed");
  const dimensions = element => element instanceof HTMLVideoElement
    ? [element.videoWidth, element.videoHeight] : [element.naturalWidth, element.naturalHeight];
  const timeLabel = time => `${state.kind === "file" ? "Video" : "Luồng +"} ${formatTime(time)}`;

  function sceneChanged(element) {
    const canvas = document.createElement("canvas"); canvas.width = 16; canvas.height = 9;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(element, 0, 0, 16, 9);
    const sample = context.getImageData(0, 0, 16, 9).data;
    let difference = 0;
    if (state.sceneSample) for (let index = 0; index < sample.length; index++)
      if (index % 4 !== 3) difference += Math.abs(sample[index] - state.sceneSample[index]);
    state.sceneSample = new Uint8ClampedArray(sample);
    return difference / (16 * 9 * 3 * 255) > .16;
  }

  function watchVideoFrame() {
    const video = el("cameraAVideo");
    if (state.kind !== "file" || !video.requestVideoFrameCallback) return;
    const epoch = state.epoch;
    state.frameCallback = video.requestVideoFrameCallback((_, metadata) => {
      if (epoch === state.epoch && state.kind === "file") {
        state.videoFrameTime = metadata.mediaTime;
        state.videoFrameNumber = metadata.presentedFrames;
        if (video.videoWidth && video.videoHeight) {
          const snapshot = state.videoFrameCanvas || document.createElement("canvas");
          const scale = Math.min(1, 1280 / video.videoWidth);
          const width = Math.round(video.videoWidth * scale), height = Math.round(video.videoHeight * scale);
          if (snapshot.width !== width || snapshot.height !== height) { snapshot.width = width; snapshot.height = height; }
          snapshot.getContext("2d").drawImage(video, 0, 0, width, height);
          state.videoFrameCanvas = snapshot;
          if (sceneChanged(snapshot)) { reset("Video đổi cảnh · nhận diện lại"); return; }
        }
        watchVideoFrame();
      }
    });
  }

  function clearCurrent(message, tone = "amber", closePasses = false) {

    if (closePasses) state.activeKeys.clear();
    state.current = [];
    el("cameraADetectionOverlay").hidden = true;
    el("recognitionPreview").hidden = true;
    el("recognitionCrop").removeAttribute("src");
    el("recognitionCount").textContent = "0 vùng";
    el("recognitionCurrentList").replaceChildren();
    el("cameraAPlate").hidden = true;
    el("cameraAPlate").textContent = "";
    el("cameraAPlate").title = message;
    el("cameraALastSeen").textContent = state.name;
    el("cameraARawText").textContent = "Văn bản OCR gốc: —";
    const status = tone === "red" ? "LỖI OCR" : message.includes("tạm dừng") ? "DỪNG OCR"
      : message.includes("kết thúc") ? "HẾT VIDEO" : message.includes("Chờ hình") ? "CHỜ NGUỒN" : "ĐANG ĐỌC";
    badge("cameraAOcrStatus", status, tone); el("cameraAOcrStatus").title = message;
    el("recognitionCurrentList").textContent = message;
    state.hooks?.onCurrent?.([]);
  }

  function reset(reason, preserveLiveClock = false, sourceChanged = false) {
    const previousKey = state.identity;
    if (!state.identity || sourceChanged) state.identity = crypto.randomUUID();
    const previousClock = state.startedAt;
    state.epoch++; state.key = `${state.kind}-${crypto.randomUUID()}`;
    const video = el("cameraAVideo");
    if (state.frameCallback !== null && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(state.frameCallback);
    state.videoFrameTime = null; state.videoFrameNumber = null; state.frameCallback = null; state.videoFrameCanvas = null;
    state.frame = 0; state.lastToken = ""; state.lastSample = null; state.sceneSample = null; state.liveFrameId = null;
    state.startedAt = preserveLiveClock ? previousClock : performance.now();
    state.request?.abort(); state.request = null; state.processing = false;
    clearCurrent(reason, "amber", true);
    if (sourceChanged) { state.liveQueue = []; state.liveFrameAt = null; state.liveFrameTime = null; state.lastPlayedAt = 0; state.nextPlaybackAt = 0; state.effectiveSpeed = state.speed; }
    state.hooks?.onReset?.(reason, previousKey, sourceChanged);
    watchVideoFrame();
  }

  function applySourceVisibility() {
    const video = el("cameraAVideo"); const image = el("cameraAFeed");
    const file = state.kind === "file";
    video.hidden = !file;
    if (file) image.hidden = true;
    el("cameraAPlaceholder").hidden = file ? video.readyState >= 2 : !image.hidden;
    if (file) el("cameraAPlaceholder").textContent = video.error ? "Không tải được video" : "Đang tải video thực tế…";
    image.parentElement.classList.toggle("file-video", file);
    el("videoSourceName").textContent = `Nguồn hiện tại: ${state.name}`;
    el("cameraASource").textContent = file ? `Video riêng của ô ${cameraId}`
      : state.liveStatus?.sharedFrom ? `Nguồn A chung · ô ${cameraId}` : `RTSP · ô ${cameraId}`;
    for (const id of ["videoBackBtn", "videoForwardBtn", "videoPlayBtn", "recognizeStillBtn"])
      el(id).disabled = !file;
    el("videoPlayBtn").textContent = video.paused ? "Phát video" : "Tạm dừng video";
    el("recognitionPauseBtn").textContent = state.enabled ? "Tạm dừng nhận diện" : "Tiếp tục nhận diện";
    if (!file && state.effectiveSpeed < 1) {
      const lag = state.liveFrameAt ? Math.max(0, (Date.now() - state.liveFrameAt) / 1000) : 0;
      el("cameraASource").textContent += ` · phát lại trễ ${lag.toFixed(1)}s`;
    }
  }

  function renderResult(data, capture) {
    const detections = (data.detections || []).map(item => {
      // The backend owns tracking and consensus over distinct real frames.
      const confirmed = Boolean(item.confirmed && item.plate && item.evidenceCount >= 3 && item.confidence >= .70);
      return { ...item, confirmed, newlyConfirmed: item.newlyConfirmed && confirmed };
    });
    state.current = detections; state.lastResultAt = performance.now();
    // Hồ sơ vào–ra được lưu tại worker; không lưu lịch sử OCR riêng trong từng ô.

    const detectedAt = capture.capturedAt || data.detectedAt;
    state.hooks?.onCurrent?.(detections, { ...data, detectedAt, cameraId, sourceName: state.name, videoTime: capture.mediaTime, sourceKey: state.identity });
    for (const detection of detections) if (detection.newlyConfirmed)
      state.hooks?.onConfirmed?.({ ...detection, cameraId, detectedAt,
        sourceName: state.name, sourceKey: state.identity, eventId: `${state.key}:${detection.trackId}`, videoTime: capture.mediaTime });
    const confirmed = detections.filter(item => item.confirmed);
    const message = !detections.length ? "Không thấy biển số trong khung hình"
      : confirmed.length ? `${confirmed.length} biển số đã xác nhận` : "Đang nhận diện · chưa đủ bằng chứng";
    el("cameraAPlate").hidden = !confirmed.length;
    el("cameraAPlate").textContent = [...new Set(confirmed.map(item => item.plate))].join(" · ");
    el("cameraAPlate").title = "Biển số đã xác nhận từ hình ảnh";
    el("cameraALastSeen").textContent = `${state.name} · ${timeLabel(capture.mediaTime)}`;
    el("cameraARawText").textContent = `OCR gốc: ${detections.map(item => item.rawText).filter(Boolean).join(" | ") || "—"}`;
    badge("cameraAOcrStatus", !detections.length ? "KHÔNG THẤY BIỂN SỐ" : confirmed.length ? "NHẬN DIỆN THÀNH CÔNG" : "ĐANG XÁC NHẬN", confirmed.length ? "green" : "amber");
    el("recognitionCount").textContent = `${detections.length} vùng`;
    const list = el("recognitionCurrentList"); list.replaceChildren();
    for (const detection of detections) {
      const row = document.createElement("div"); row.className = "recognition-item";
      const crop = document.createElement("img"); crop.src = detection.crop; crop.alt = `Vùng biển số thực tế #${detection.trackId}`;
      const text = document.createElement("div"); const title = document.createElement("strong");
      title.textContent = detection.confirmed ? detection.plate : detection.plate ? `OCR tạm: ${detection.plate}` : "Không đọc được biển số";
      const detail = document.createElement("small");
      detail.textContent = `#${detection.trackId} · ${Math.round(detection.confidence * 100)}% · ${timeLabel(capture.mediaTime)} · ${state.name}${detection.confirmed ? "" : ` · ${detection.evidenceCount}/3 khung xác nhận`}`;
      text.append(title, detail); row.append(crop, text); list.append(row);
    }
    el("recognitionPreview").hidden = !detections.length;
    if (detections.length) {
      el("recognitionCrop").src = detections[0].crop;
      el("recognitionFrameInfo").textContent = `${Math.round(detections[0].confidence * 100)}% OCR · ${detections.length} vùng thật`;
    } else el("recognitionCrop").removeAttribute("src");
    drawBoxes(detections, data.frameWidth, data.frameHeight);
    state.effectiveSpeed = state.autoSlow && detections.length && !confirmed.length ? Math.min(.25, state.speed) : state.speed;
    if (state.kind === "file") el("cameraAVideo").playbackRate = state.effectiveSpeed;
    applySourceVisibility();
  }

  function drawBoxes(detections, width, height) {
    const overlay = el("cameraADetectionOverlay");
    overlay.hidden = !detections.length;
    if (!detections.length) return;
    overlay.width = width; overlay.height = height;
    const context = overlay.getContext("2d"); context.clearRect(0, 0, width, height);
    context.lineWidth = Math.max(2, width / 400); context.font = `bold ${Math.max(14, width / 48)}px system-ui`;
    for (const detection of detections) {
      const [x, y, boxWidth, boxHeight] = detection.box;
      context.strokeStyle = detection.confirmed ? "#34d399" : "#fbbf24";
      context.strokeRect(x, y, boxWidth, boxHeight);
      const label = detection.confirmed ? detection.plate : `#${detection.trackId} · đang đọc`;
      const textWidth = context.measureText(label).width + 8;
      const labelX = Math.max(0, Math.min(width - textWidth, x)); const labelY = Math.max(20, y - 3);
      context.fillStyle = "#07111fe8"; context.fillRect(labelX, labelY - 20, textWidth, 24);
      context.fillStyle = context.strokeStyle; context.fillText(label, labelX + 4, labelY - 2);
    }
  }

  async function process(force = false) {
    const video = el("cameraAVideo");
    if (state.kind === "live") {
      if (!state.hooks.liveAvailable()) { state.request?.abort(); clearCurrent("Chờ hình camera thật"); return; }
      const image = el("cameraAFeed");
      if (!image.hidden && image.naturalWidth && state.enabled && sceneChanged(image))
        reset("Luồng đổi cảnh · nhận diện lại", true);
    }
    if (state.lastResultAt && performance.now() - state.lastResultAt > 3500 && state.current.length
      && (state.kind !== "file" || !video.paused)) clearCurrent("Đang nhận diện khung mới");
    if (state.processing || !state.enabled && !force || performance.now() < state.nextAttemptAt && !force) return;
    if (state.kind === "file" && (video.ended || video.seeking || video.paused && !force)) return;
    if (!state.hooks.available()) { clearCurrent("OCR cục bộ chưa sẵn sàng", "red"); return; }
    if (state.kind === "live" && !state.hooks.liveAvailable()) { clearCurrent("Chờ hình camera thật"); return; }
    const element = source(); const [width, height] = dimensions(element);
    if (!width || !height || element.hidden) return;
    const mediaTime = state.kind === "file" ? state.videoFrameTime ?? video.currentTime : state.liveFrameTime ?? (performance.now() - state.startedAt) / 1000;
    const capturedAt = state.kind === "live" && state.liveFrameAt ? new Date(state.liveFrameAt).toISOString() : null;
    const token = state.kind === "file" ? `${state.videoFrameNumber ?? "seek"}:${mediaTime.toFixed(5)}` : String(state.liveFrameId ?? "");
    if (state.kind === "live" && !token) return;
    if (!force && token === state.lastToken) return;
    state.lastToken = token; state.processing = true;
    const epoch = state.epoch; const sourceKey = state.key;
    const controller = new AbortController(); state.request = controller;
    const canvas = document.createElement("canvas"); const scale = Math.min(1, 1280 / width);
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
    try {
      const capturedSource = state.kind === "file" ? state.videoFrameCanvas || element : element;
      canvas.getContext("2d").drawImage(capturedSource, 0, 0, canvas.width, canvas.height);
      const sampleCanvas = document.createElement("canvas"); sampleCanvas.width = 16; sampleCanvas.height = 9;
      const sampleContext = sampleCanvas.getContext("2d", { willReadFrequently: true });
      sampleContext.drawImage(canvas, 0, 0, 16, 9);
      const sample = sampleContext.getImageData(0, 0, 16, 9).data;
      let sceneCut = false;
      if (state.lastSample) {
        let difference = 0;
        for (let i = 0; i < sample.length; i++) if (i % 4 !== 3) difference += Math.abs(sample[i] - state.lastSample[i]);
        sceneCut = difference / (16 * 9 * 3 * 255) > .16;
      }
      state.lastSample = new Uint8ClampedArray(sample);
      const frame = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .9));
      if (!frame || epoch !== state.epoch) return;
      const response = await fetch(state.hooks.endpoint(), { method: "POST", signal: controller.signal,
        headers: { "Content-Type": "image/jpeg", "X-Frame-Width": String(canvas.width), "X-Frame-Height": String(canvas.height),
          "X-Source-Key": sourceKey, "X-Source-Name": encodeURIComponent(state.name),
          "X-Frame-Id": token, "X-Media-Time": String(mediaTime), "X-Scene-Cut": String(sceneCut),
          // Metadata giúp worker kiểm tra khung RTSP mới; file chỉ được xem/OCR.
          // Không gửi các header mới tới worker cũ khi đang dùng fallback OCR.
          ...(recordingAvailable ? { "X-Source-Kind":state.kind, "X-Captured-At":capturedAt || new Date().toISOString(), "X-Camera-Revision":String(state.liveStatus?.revision ?? "") } : {}) }, body: frame });
      const data = await response.json();
      if (response.status === 429) state.nextAttemptAt = performance.now() + 1200;
      if (!response.ok) throw new Error(data.error || `OCR HTTP ${response.status}`);
      if (epoch !== state.epoch || controller.signal.aborted) return;
      if (state.kind === "live" && !state.hooks.liveAvailable()) return;
      if (!Array.isArray(data.detections) || data.cameraId !== cameraId || data.sourceKey !== sourceKey || data.frameId !== token)
        throw new Error("Kết quả OCR không khớp nguồn/khung hình.");
      if (data.engineSession && state.engineSession && data.engineSession !== state.engineSession) {
        state.engineSession = data.engineSession;
        reset("Bộ OCR vừa khởi động lại · xác nhận lại khung mới", true);
        return;
      }
      state.engineSession = data.engineSession || state.engineSession;
      if (state.kind === "file" && !force && video.currentTime - mediaTime > 2) {
        clearCurrent("Đang xử lý khung mới · bỏ kết quả đã trễ"); return;
      }
      renderResult(data, { mediaTime, capturedAt });
      if (data.recording) window.dispatchEvent(new CustomEvent("vehicle-recording-result", { detail:{...data.recording,cameraId,plates:data.detections.filter(item=>item.confirmed).map(item=>item.plate)} }));
    } catch (error) {
      if (epoch === state.epoch && error.name !== "AbortError") clearCurrent(`Lỗi nhận diện: ${error.message}`, "red");
    } finally {
      if (epoch === state.epoch) { state.processing = false; state.request = null; }
    }
  }

  function useLiveSource(reason = "Đã chuyển về camera RTSP") {
    const video = el("cameraAVideo");
    video.pause(); state.kind = "live"; state.name = state.liveName; state.enabled = true;
    video.removeAttribute("src"); video.load();
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null; el("videoFile").value = "";
    el("cameraAFeed").removeAttribute("src");
    reset(reason, false, true); applySourceVisibility();
  }

  function init(hooks) {
    state.hooks = hooks;
    reset("Chờ khung hình thực tế");
    const video = el("cameraAVideo");
    el("videoFile").addEventListener("change", () => {
      const file = el("videoFile").files[0]; if (!file) return;
      video.pause();
      if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
      state.kind = "file"; state.name = file.name; state.enabled = true;
      reset("Đã đổi video · chờ phát", false, true);
      state.objectUrl = URL.createObjectURL(file); video.src = state.objectUrl; video.load(); video.playbackRate = state.speed;
      applySourceVisibility();
      hooks.refreshLive?.();
    });
    el("useLiveSourceBtn").addEventListener("click", () => {
      useLiveSource(); hooks.refreshLive?.();
    });
    el("recognitionPauseBtn").addEventListener("click", () => {
      state.enabled = !state.enabled;
      if (!state.enabled) { state.request?.abort(); clearCurrent("Đã tạm dừng nhận diện"); }
      applySourceVisibility();
    });
    el("recognizeStillBtn").addEventListener("click", () => process(true));
    el("videoPlayBtn").addEventListener("click", () => video.paused ? video.play().catch(() => clearCurrent("Không phát được video", "red")) : video.pause());
    el("videoBackBtn").addEventListener("click", () => { video.currentTime = Math.max(0, video.currentTime - 5); });
    el("videoForwardBtn").addEventListener("click", () => { video.currentTime = Math.min(video.duration || 0, video.currentTime + 5); });
    video.addEventListener("loadeddata", applySourceVisibility);
    video.addEventListener("playing", () => { applySourceVisibility(); process(); });
    video.addEventListener("pause", () => {
      if (state.kind !== "file") return;
      state.request?.abort(); clearCurrent("Video đang tạm dừng"); applySourceVisibility();
    });
    video.addEventListener("seeking", () => { if (state.kind === "file") reset("Đang tua video · xóa kết quả khung trước"); });
    video.addEventListener("seeked", () => { if (!video.paused) process(); });
    video.addEventListener("ended", () => { state.request?.abort(); clearCurrent("Video đã kết thúc", "amber", true); applySourceVisibility(); });
    video.addEventListener("error", () => { if (state.kind === "file") { state.request?.abort(); clearCurrent("Không tải được video thật", "red", true); applySourceVisibility(); } });

    el("cameraAAutoSlow").addEventListener("change", () => {
      state.autoSlow = el("cameraAAutoSlow").checked;
      if (!state.autoSlow) { state.effectiveSpeed = state.speed; video.playbackRate = state.speed; }
      applySourceVisibility();
    });
    applySourceVisibility();
  }

  function updateLive(status) {
    const previous = state.liveStatus;
    const revision = `${status.revision ?? 0}:${status.sharedFrom === "A" ? sourceAVersion : 0}`;
    if (previous && previous.revisionKey !== revision && state.kind === "live")
      reset("Nguồn RTSP đã đổi · đọc khung mới", false, true);
    state.liveStatus = { ...status, revisionKey: revision };
    state.liveName = status.sourceName || `Camera ${cameraId} · RTSP`;
    const image = el("cameraAFeed");
    image.dataset.sharedFrom = status.sharedFrom || "";
    if (state.kind === "live") {
      state.name = state.liveName;
      el("cameraAMessage").textContent = status.message || "Đang chờ nguồn camera.";
      image.hidden = !status.streamAvailable;
      el("cameraAPlaceholder").hidden = status.streamAvailable;
      el("cameraAPlaceholder").textContent = status.configured ? "Đang chờ khung hình từ nguồn đã chọn" : "Chưa kết nối nguồn camera";
      if (!status.streamAvailable) {
        image.removeAttribute("src"); delete image.dataset.revision;
        if (previous?.streamAvailable) { state.request?.abort(); clearCurrent("Mất hình nguồn · chờ kết nối lại", "red"); }
      }
    } else el("cameraAMessage").textContent = `Đang dùng video ${state.name}; dữ liệu riêng của ô ${cameraId}.`;
    applySourceVisibility();
    window.dispatchEvent(new CustomEvent("camera-source-state", { detail:{ cameraId } }));
  }

  async function refreshFrame() {
    const image = el("cameraAFeed");
    if (state.kind !== "live" || !state.liveStatus?.streamAvailable || state.frameLoading) return;
    state.frameLoading = true;
    const epoch = state.epoch;
    try {
      const response = await fetch(`${helperUrl}/camera/${cameraId}/frame?tick=${Date.now()}`, { signal: AbortSignal.timeout(2500) });
      if (!response.ok) return;
      const frameId = response.headers.get("X-Frame-Id");
      if (!frameId || frameId === state.liveQueue.at(-1)?.frameId || frameId === state.liveFrameId || epoch !== state.epoch) return;
      const blob = await response.blob();
      if (epoch !== state.epoch || state.kind !== "live") return;
      state.liveQueue.push({ blob, frameId, at: Date.now(), mediaTime: (performance.now() - state.startedAt) / 1000 });
      if (state.liveQueue.length > 120) {
        state.liveQueue = state.liveQueue.slice(-1);
        reset("Đã chuyển đoạn mới của nguồn thật", true);
      }
    } catch { /* Status and OCR report unavailable sources; keep the next frame retry independent. */ }
    finally { state.frameLoading = false; }
  }

  async function playBufferedFrame() {
    if (state.kind !== "live" || !state.liveStatus?.streamAvailable || state.imageDecoding || !state.liveQueue.length || performance.now() < state.nextPlaybackAt) return;
    state.imageDecoding = true;
    const epoch = state.epoch;
    const frame = state.speed === 1 ? state.liveQueue.pop() : state.liveQueue.shift();
    if (state.speed === 1) state.liveQueue = [];
    let imageUrl = URL.createObjectURL(frame.blob);
    try {
      const image = el("cameraAFeed"); image.src = imageUrl; await image.decode();
      if (epoch !== state.epoch || state.kind !== "live") return;
      if (state.liveImageUrl) URL.revokeObjectURL(state.liveImageUrl);
      state.liveImageUrl = imageUrl; imageUrl = null;
      state.liveFrameId = frame.frameId; state.liveFrameAt = frame.at; state.liveFrameTime = frame.mediaTime;
      image.dataset.frameId = frame.frameId; image.dataset.capturedAt = new Date(frame.at).toISOString();
      const interval = state.lastPlayedAt ? Math.min(1000, Math.max(120, frame.at - state.lastPlayedAt)) : 300;
      state.lastPlayedAt = frame.at; state.nextPlaybackAt = performance.now() + interval / state.effectiveSpeed;
      applySourceVisibility();
    } catch { /* A source change can cancel decoding; the next real frame retries. */ }
    finally { if (imageUrl) URL.revokeObjectURL(imageUrl); state.imageDecoding = false; }
  }

  return {
    init, process, updateLive, refreshFrame, playBufferedFrame, applySourceVisibility, useLiveSource,
    source, dimensions, sourceKey: () => state.identity, kind: () => state.kind, sourceName: () => state.name,
    hasVisualSource: () => dimensions(source()).every(value => value > 0),
    resetLive: reason => { if (state.kind === "live") reset(reason, false, true); },
    liveAvailable: () => Boolean(state.liveStatus?.streamAvailable),
    currentPlates: () => new Set(state.current.filter(item => item.confirmed).map(item => item.plate))
  };
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, signal: options.signal || AbortSignal.timeout(8000) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Lỗi nguồn camera (${response.status})`);
    return data;
  }

  function cameraUrl(cameraId, action) {
    return `${helperUrl}/camera/${cameraId}/${action}`;
  }

  async function refreshStatuses(forceIds = null) {
    if (refreshRunning) return;
    const targets = statusPolling.targets(forceIds);
    const fileNeedsHealth = [...controllers.values()].some(controller => controller.kind() === "file")
      && statusPolling.due() && Date.now() >= nextHealthCheckAt;
    if (!targets.length && !fileNeedsHealth) return;
    refreshRunning = true;
    try {
      let healthFailed = false;
      if (forceIds || Date.now() >= nextHealthCheckAt) {
        const health = await Promise.allSettled([request(`${paddleUrl}/status`), request(`${helperUrl}/status`)]);
        const ready = health.findIndex(result => result.status === "fulfilled" && result.value.available && result.value.pipelineVersion >= 2);
        available = ready !== -1;
        healthFailed = !available;
        nextHealthCheckAt = available ? Date.now() + 10_000 : 0;
        if (available) recognitionUrl = ready === 0 ? paddleUrl : helperUrl;
        recordingAvailable = available && Boolean(health[ready].value.recordingAvailable && health[ready].value.recordingVersion >= 2);
        window.dispatchEvent(new CustomEvent("camera-recording-health", { detail:{ available:recordingAvailable } }));
      }
      const results = await Promise.allSettled(targets.map(async cameraId => {
        const status = await request(cameraUrl(cameraId, "status"));
        statusPolling.update(cameraId, status);
        controllers.get(cameraId).updateLive(status);
      }));
      results.forEach((result, index) => {
        if (result.status === "rejected") controllers.get(targets[index]).updateLive({ configured: false,
          streamAvailable: false, message: "Không kết nối được máy chủ của nguồn camera này." });
      });
      statusPolling.finish(healthFailed || results.some(result => result.status === "rejected"));
    } finally { refreshRunning = false; }
  }

  function connectControls(cameraId) {
    const form = node(`camera${cameraId}Form`);
    const feedback = node(`camera${cameraId}ConfigMessage`);
    const controller = controllers.get(cameraId);
    let configuring = false;
    const showFeedback = (text, error = false) => {
      feedback.hidden = false; feedback.textContent = text;
      feedback.classList.toggle("config-error", error);
    };
    const configure = async (payload, disconnect = false) => {
      if (configuring) return;
      configuring = true;
      const buttons = form.querySelectorAll("button");
      buttons.forEach(button => { button.disabled = true; });
      showFeedback("Đang cập nhật nguồn camera…");
      try {
        const update = () => request(cameraUrl(cameraId, "configure"), { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        let result;
        try { result = await update(); }
        catch (error) {
          if (!(error instanceof TypeError || error.name === "TimeoutError")) throw error;
          showFeedback("Đang tự khởi động dịch vụ camera/OCR/hồ sơ…");
          await request("/api/system/start-camera", {method:"POST",headers:{"Content-Type":"application/json"},body:"{}",signal:AbortSignal.timeout(90000)});
          result = await update();
        }
        statusPolling.update(cameraId, result);
        controller.updateLive(result);
        if (disconnect) controller.resetLive("Đã ngắt nguồn của ô này");
        else controller.useLiveSource("Đã đổi nguồn RTSP của ô này");
        if (cameraId === "A") sourceAVersion++;
        showFeedback(result.message || "Đã cập nhật nguồn camera.");
        node(`camera${cameraId}Url`).value = "";
        node(`camera${cameraId}Url`).type = "password";
        node(`camera${cameraId}ShowUrlBtn`).textContent = "Hiện";
        node(`camera${cameraId}ShowUrlBtn`).setAttribute("aria-pressed", "false");
        await refreshStatuses([cameraId]);
      } catch (error) {
        // Giữ link đang nhập để người dùng thử lại sau khi khởi động dịch vụ.
        const offline = error instanceof TypeError || error.name === "TimeoutError";
        showFeedback(offline ? "Chưa kết nối máy chủ camera. Chạy Run-CameraSystem.cmd, sau đó nhấn Kết nối lại." : error.message, true);
      }
      finally { configuring = false; buttons.forEach(button => { button.disabled = false; }); }
    };
    form.addEventListener("submit", event => {
      event.preventDefault();
      const sources = node(`camera${cameraId}Url`).value.trim().split(/(?=rtsp:\/\/)/i).filter(Boolean);
      if (!sources.length || sources.some(value => value.trim() !== sources[0].trim())) {
        showFeedback("Chỉ nhập một nguồn RTSP.", true); return;
      }
      try {
        const url = new URL(sources[0].trim());
        if (url.protocol !== "rtsp:" || !url.hostname) throw new Error();
      } catch { showFeedback("Link phải bắt đầu bằng rtsp:// và có địa chỉ camera.", true); return; }
      configure({ mode: "rtsp", rtspUrl: sources[0].trim() });
    });
    node(`camera${cameraId}DisconnectBtn`).addEventListener("click", () => configure({ mode: "disconnect" }, true));
    const input = node(`camera${cameraId}Url`);
    const reveal = node(`camera${cameraId}ShowUrlBtn`);
    input.addEventListener("input", () => { if (!configuring) feedback.hidden = true; });
    reveal.addEventListener("click", () => {
      const visible = input.type === "password";
      input.type = visible ? "text" : "password";
      reveal.textContent = visible ? "Ẩn" : "Hiện";
      reveal.setAttribute("aria-pressed", String(visible));
    });
    form.closest("details").addEventListener("toggle", event => {
      if (!event.currentTarget.open) {
        input.type = "password"; reveal.textContent = "Hiện";
        reveal.setAttribute("aria-pressed", "false");
      }
    });
  }

  function init(hooks = {}) {
    for (const cameraId of cameraIds) {
      const controller = createController(cameraId); controllers.set(cameraId, controller);
      controller.init({
        available: () => available,
        liveAvailable: () => controller.liveAvailable(),
        endpoint: () => `${recognitionUrl}/recognize?cameraId=${cameraId}`,
        refreshLive: () => refreshStatuses([cameraId]),
        onReset: (...args) => hooks.onReset?.(cameraId, ...args),
        onCurrent: (...args) => hooks.onCurrent?.(cameraId, ...args),
        onConfirmed: detection => hooks.onConfirmed?.(detection),
      });
      connectControls(cameraId);
    }
    const tick = async () => {
      // One inference at a time, with every camera receiving its own turn.
      for (const cameraId of cameraIds) await controllers.get(cameraId).process();
      setTimeout(tick, 100);
    };
    const refreshFrames = () => { for (const controller of controllers.values()) controller.refreshFrame(); };
    const playFrames = () => { for (const controller of controllers.values()) controller.playBufferedFrame(); };
    window.addEventListener("camera-settings-open", () => refreshStatuses(cameraIds));
    refreshStatuses().then(refreshFrames); tick(); setInterval(refreshStatuses, 2500); setInterval(refreshFrames, 300); setInterval(playFrames, 50);
  }

  window.CameraRecognition = {
    init,
    recordingAvailable: () => recordingAvailable,
    hasVisualSource: cameraId => controllers.get(cameraId || "A")?.hasVisualSource() || false,
    sourceKey: cameraId => controllers.get(cameraId || "A")?.sourceKey(),
    currentPlates: cameraId => controllers.get(cameraId || "A")?.currentPlates() || new Set()
  };
})();
