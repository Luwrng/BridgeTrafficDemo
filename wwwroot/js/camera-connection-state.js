// Khóa nguồn độc lập từng camera. Lỗi mạng không phải xác nhận đã ngắt nguồn.
(function(root) {
  class CameraConnectionState {
    constructor() { this.configured=null; this.streamAvailable=false; this.reachable=false; this.busy=false; }
    update(status) {
      if(typeof status?.configured==='boolean')this.configured=status.configured;
      this.streamAvailable=Boolean(status?.streamAvailable); this.reachable=true;
    }
    unavailable() { this.reachable=false;this.streamAvailable=false; }
    get locked() { return this.configured===true; }
    get inputDisabled() { return this.busy||this.locked; }
    get connectDisabled() { return this.busy||this.locked; }
    get disconnectDisabled() { return this.busy||this.configured===false; }
  }
  if(typeof module!=='undefined'&&module.exports)module.exports=CameraConnectionState;
  else root.CameraConnectionState=CameraConnectionState;
})(typeof window!=='undefined'?window:globalThis);
