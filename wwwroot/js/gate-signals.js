// Interlock hiển thị dùng cùng kết quả ưu tiên từ backend, không tự đoán
// xanh chỉ vì D trống. Phản hồi cũ/sai hoặc mất kết nối đều về hai đỏ.
(function (root) {
  function select(state) {
    const red = {A:'RED',B:'RED',greenGate:null};
    if (!state || state.issue || state.signalPolicyVersion!==1) return red;
    const priority = state.occupied ? state.vehicle : state.nextVehicle;
    const gate = priority?.entry_camera;
    if (!['A','B'].includes(gate) || state.greenGate!==gate) return red;
    const other = gate==='A' ? 'B' : 'A';
    if (state.signals?.[gate]!=='GREEN' || state.signals?.[other]!=='RED') return red;
    return {...state.signals,greenGate:gate};
  }

  function render(state, lights) {
    const signals=select(state);
    // Tắt cả hai xanh TRƯỚC khi bật hướng mới. B -> A cũng không có
    // thời điểm trung gian cả hai cùng xanh trong lúc thay thuộc tính SVG.
    for (const gate of ['A','B']) {
      lights[gate].green.setAttribute('class','lamp-off');
      lights[gate].red.setAttribute('class','lamp-red');
    }
    if (signals.greenGate) {
      lights[signals.greenGate].red.setAttribute('class','lamp-off');
      lights[signals.greenGate].green.setAttribute('class','lamp-green');
    }
    return signals;
  }

  function describe(state) {
    const gate=select(state).greenGate;
    return gate==='A' ? 'A xanh · B đỏ' : gate==='B' ? 'A đỏ · B xanh' : 'A đỏ · B đỏ';
  }

  const api={select,render,describe};
  if (typeof module!=='undefined' && module.exports) module.exports=api;
  else root.GateSignals=api;
})(typeof window!=='undefined' ? window : globalThis);
