(() => {
  // Một request tại một thời điểm. Khi dịch vụ tắt, thử lại sau
  // 5/10/20/30 giây; không dồn GET mỗi 2 giây vào cổng đang đóng.
  class ServicePolling {
    constructor(interval, clock = Date.now) {
      this.interval = interval; this.clock = clock;
      this.failures = 0; this.nextAt = 0; this.inFlight = false;
    }
    begin(force = false) {
      if (this.inFlight || !force && this.clock() < this.nextAt) return false;
      this.inFlight = true;
      return true;
    }
    finish(failed) {
      this.inFlight = false;
      this.failures = failed ? this.failures + 1 : 0;
      this.nextAt = this.clock() + (failed ? Math.min(30000, 5000 * 2 ** Math.min(this.failures - 1, 3)) : this.interval);
    }
  }
  window.ServicePolling = ServicePolling;
})();
