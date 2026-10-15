(() => {
  class CameraStatusPolling {
    constructor(cameraIds, clock = Date.now) {
      this.cameraIds = cameraIds;
      this.clock = clock;
      this.active = new Set();
      this.initial = true;
      this.nextCheckAt = 0;
      this.failures = 0;
    }

    targets(forceIds = null) {
      if (forceIds) return [...new Set(forceIds)].filter(id => this.cameraIds.includes(id));
      if (this.initial) return [...this.cameraIds];
      return this.due() ? [...this.active] : [];
    }

    due() { return this.clock() >= this.nextCheckAt; }

    update(cameraId, status) {
      if (status.configured) this.active.add(cameraId);
      else this.active.delete(cameraId);
    }

    finish(failed) {
      this.initial = false;
      this.failures = failed ? this.failures + 1 : 0;
      this.nextCheckAt = this.clock() + Math.min(30_000, 2500 * 2 ** Math.min(this.failures, 4));
    }
  }

  window.CameraStatusPolling = CameraStatusPolling;
})();
