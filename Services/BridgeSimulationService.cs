using BridgeTrafficDemo.Models;
using System.Text.RegularExpressions;

namespace BridgeTrafficDemo.Services;

public sealed class BridgeSimulationService
{
    private static readonly Regex LicensePlatePattern = new(
        @"^\d{2}[A-Z]{1,2}\d{4,6}$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly HashSet<string> SupportedSensorStates = new(StringComparer.OrdinalIgnoreCase)
    {
        "CLEAR", "DETECTED", "OFFLINE", "FAULT", "STALE", "STUCK_ON", "STUCK_OFF", "CONTRADICTORY", "UNKNOWN"
    };

    private readonly object _gate = new();
    private readonly BridgeStateMachine _bridge = new();
    private readonly List<string> _logs = new();
    private readonly Dictionary<string, string> _sensors = new(StringComparer.OrdinalIgnoreCase)
    {
        ["D-IN"] = "CLEAR",
        ["D-OUT"] = "CLEAR"
    };
    private readonly Dictionary<string, SignalState> _signals = new(StringComparer.Ordinal)
    {
        ["A"] = new("A", "R1/R5", "UNKNOWN", "SIMULATED", DateTimeOffset.UtcNow, "Chưa có lượt được cấp."),
        ["B"] = new("B", "R2/R3/R4", "UNKNOWN", "SIMULATED", DateTimeOffset.UtcNow, "Chưa có lượt được cấp.")
    };
    private readonly List<VehicleRun> _activeRuns = new();
    private readonly Queue<VehicleRun> _queue = new();
    private readonly Dictionary<string, VehicleState> _vehicles = new(StringComparer.Ordinal);
    private VehicleRun? _displayRun;
    private string _scenario = "R1";
    private string _barrierState = "OPEN";
    private string _status = "Mô phỏng sẵn sàng: D=EMPTY, cảm biến CLEAR, barrier OPEN.";
    private bool _exitBlocked;
    private bool _sensorFault;
    private bool _vehicleStuck;
    private bool _emergencyStop;

    private sealed class VehicleRun
    {
        public required TrafficRoute Route { get; init; }
        public required string VehicleId { get; init; }
        public required string VehicleType { get; init; }
        public string? LicensePlate { get; init; }
        public required DateTimeOffset RequestedAt { get; init; }
        public required List<DemoStep> Steps { get; init; }
        public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
        public int StepIndex { get; set; }
        public string CurrentNode { get; set; } = "";
        public string JourneyState { get; set; } = "QUEUED";
        public bool HasBridgePermission { get; set; }
        public string? ActiveEntrySignal { get; set; }
        public bool ActivePermissionUsesBridge { get; set; }
    }

    public BridgeSimulationService()
    {
        _bridge.TryConfirmEmpty(sensorsClear: true, out _);
        AddLog("Chế độ mô phỏng khởi động sẵn sàng: D=EMPTY, D-IN/D-OUT=CLEAR, barrier=OPEN. Không điều khiển phần cứng.", "INFO");
        RefreshSignals();
    }

    public ApiResult SelectScenario(string scenario)
    {
        lock (_gate)
        {
            var route = TrafficNetwork.FindRoute(scenario?.Trim() ?? "");
            if (route is null)
                return Reject($"Mã tuyến không hợp lệ: {string.Join(", ", TrafficNetwork.Routes.Select(item => item.Id))}.");

            _scenario = route.Id;
            if (_activeRuns.Count > 0 || _queue.Count > 0
                || _bridge.Phase is BridgePhases.Reserved or BridgePhases.Occupied or BridgePhases.Clearing)
            {
                _status = $"Đã chọn tuyến {route.Id}; xe đang chạy và hàng đợi được giữ nguyên.";
                AddLog(_status, "INFO");
                RefreshSignals();
                return new ApiResult(true, _status);
            }

            _displayRun = null;
            _vehicles.Clear();
            _status = $"Đã nạp tuyến {route.Id}; sơ đồ ở điểm đầu, sẵn sàng tạo xe.";
            AddLog(_status, "OK");
            RefreshSignals();
            return new ApiResult(true, _status);
        }
    }

    public ApiResult StartScenario(StartScenarioRequest request)
    {
        lock (_gate)
        {
            var route = TrafficNetwork.FindRoute(request.Scenario?.Trim() ?? "");
            if (route is null)
                return Reject($"Mã tuyến không hợp lệ: {string.Join(", ", TrafficNetwork.Routes.Select(item => item.Id))}.");
            if (request.VehicleType is null || request.VehicleType.Trim().Length is 0 or > 32)
                return Reject("Loại xe phải có từ 1 đến 32 ký tự.");
            var licensePlate = request.LicensePlate?.Trim().ToUpperInvariant();
            if (licensePlate is not null && !LicensePlatePattern.IsMatch(licensePlate))
                return Reject("Biển số không hợp lệ.");
            if (licensePlate is not null && _vehicles.Values.Any(vehicle =>
                    string.Equals(vehicle.LicensePlate, licensePlate, StringComparison.OrdinalIgnoreCase)
                    && vehicle.JourneyState != "COMPLETE"))
                return Reject($"Biển số {licensePlate} đã có chuyến mô phỏng đang hoạt động hoặc xếp hàng.");

            var vehicleId = string.IsNullOrWhiteSpace(request.VehicleId)
                ? $"SIM-{Guid.NewGuid():N}"[..12]
                : request.VehicleId.Trim();
            if (vehicleId.Length > 40 || _vehicles.ContainsKey(vehicleId))
                return Reject("Mã xe không hợp lệ hoặc đã tồn tại.");

            var now = DateTimeOffset.UtcNow;
            var run = new VehicleRun
            {
                Route = route,
                VehicleId = vehicleId,
                VehicleType = request.VehicleType.Trim(),
                LicensePlate = licensePlate,
                RequestedAt = now,
                Steps = DemoRouteCatalog.BuildSteps(route.Id),
                CurrentNode = route.Nodes[0]
            };
            _scenario = route.Id;
            _displayRun = run;
            _vehicles[vehicleId] = ToVehicleState(run, "QUEUED", false);

            if (CanActivate(run))
            {
                run.JourneyState = "READY";
                _activeRuns.Add(run);
                _vehicles[vehicleId] = ToVehicleState(run, run.JourneyState, false);
                _status = $"Xe {vehicleId} được nhận vào mô phỏng tuyến {route.Id}; chưa cấp quyền qua D.";
                AddLog(_status, "INFO");
                RefreshSignals();
                return new ApiResult(true, _status, VehicleId: vehicleId);
            }

            _queue.Enqueue(run);
            _status = $"Xe {vehicleId} được xếp hàng FIFO cho tuyến {route.Id}; chưa có quyền lưu thông.";
            AddLog(_status, "INFO");
            return new ApiResult(true, _status, Queued: true, VehicleId: vehicleId);
        }
    }

    public ApiResult Step()
    {
        lock (_gate)
        {
            PromoteQueue();
            var run = _activeRuns.OrderBy(item => item.RequestedAt).FirstOrDefault();
            if (run is null)
                return Reject("Không có xe sẵn sàng. Xác nhận D trống, barrier và lỗi trước khi chạy bước tiếp.");
            _displayRun = run;
            if (run.StepIndex >= run.Steps.Count)
                return Reject("Tuyến xe đã hoàn tất.");

            var step = run.Steps[run.StepIndex];
            if (step.Kind == "RESERVE")
            {
                var alreadyReserved = _bridge.Phase == BridgePhases.Reserved
                    && _bridge.RouteId == run.Route.Id
                    && _bridge.VehicleId == run.VehicleId;
                if (!alreadyReserved)
                {
                    if (AnyFault() || _exitBlocked || _barrierState != "OPEN" || !SensorsHealthyAndClear())
                        return Reject("Không thể cấp quyền: cần D=EMPTY, cảm biến khỏe, lối ra thông và barrier OPEN.");
                    if (!_bridge.TryReserve(run.Route.Id, run.VehicleId, !_exitBlocked, SensorsHealthyAndClear(), _barrierState == "OPEN", out var error))
                        return Reject(error);
                }
                run.HasBridgePermission = true;
                run.JourneyState = "AUTHORIZED";
                run.ActiveEntrySignal = step.From;
                run.ActivePermissionUsesBridge = step.UsesBridge;
                _status = alreadyReserved
                    ? $"Quyền chặng {run.Route.Id} đã được cấp sau khi D trống; tiếp tục từ {step.From}."
                    : $"Đã cấp quyền chặng {run.Route.Id} cho xe {run.VehicleId}; điểm vào {step.From}.";
            }
            else if (step.Kind == "AUTHORIZE_DIRECT")
            {
                run.JourneyState = "AUTHORIZED";
                run.ActiveEntrySignal = step.From;
                run.ActivePermissionUsesBridge = false;
                _status = $"Đã cấp quyền mô phỏng tuyến ngoài D {run.Route.Id}; không chiếm dụng vùng cầu.";
            }
            else if (step.Kind == "COMPLETE_DIRECT")
            {
                run.CurrentNode = step.To;
                run.JourneyState = "MOVING";
                run.ActiveEntrySignal = null;
                run.ActivePermissionUsesBridge = false;
                _status = step.Status;
            }
            else if (step.Kind == "D_IN")
            {
                _sensors["D-IN"] = "DETECTED";
                _sensors["D-OUT"] = "CLEAR";
                if (!_bridge.TryEnter(run.Route.Id, run.VehicleId, out var error))
                    return Fault(error);
                run.CurrentNode = "D";
                run.JourneyState = "OCCUPIED";
                run.HasBridgePermission = false;
                run.ActivePermissionUsesBridge = false;
                _status = $"Xe {run.VehicleId} đã được xác nhận vào D.";
            }
            else if (step.Kind == "CROSS")
            {
                _sensors["D-IN"] = "CLEAR";
                _status = $"Xe {run.VehicleId} đang di chuyển trong vùng D.";
            }
            else if (step.Kind == "D_OUT")
            {
                _sensors["D-OUT"] = "DETECTED";
                if (!_bridge.TryBeginClearing(run.Route.Id, run.VehicleId, out var error))
                    return Fault(error);
                run.CurrentNode = step.To;
                run.JourneyState = "CLEARING";
                run.ActivePermissionUsesBridge = false;
                _status = $"Xe {run.VehicleId} đã ra khỏi D; đang xác minh cảm biến CLEAR.";
            }
            else if (step.Kind == "CONFIRM_CLEAR")
            {
                _sensors["D-IN"] = "CLEAR";
                _sensors["D-OUT"] = "CLEAR";
                if (!_bridge.TryConfirmEmpty(SensorsHealthyAndClear(), out var error))
                    return Reject(error);
                run.CurrentNode = step.To;
                run.JourneyState = "MOVING";
                _status = $"D được xác nhận EMPTY sau chặng của xe {run.VehicleId}; quyền chặng đã giải phóng.";
            }
            else
            {
                run.CurrentNode = step.Kind == "APPROACH" ? step.From : step.To;
                run.JourneyState = "MOVING";
                _status = step.Status;
            }

            AddLog(step.Event, step.Kind is "D_IN" or "D_OUT" ? "INFO" : "OK");
            run.StepIndex++;
            run.UpdatedAt = DateTimeOffset.UtcNow;
            if (run.StepIndex >= run.Steps.Count)
            {
                run.JourneyState = "COMPLETE";
                run.HasBridgePermission = false;
                _status = $"Xe {run.VehicleId} hoàn tất tuyến {run.Route.Id}.";
                AddLog(_status, "OK");
                _activeRuns.Remove(run);
            }
            else if (step.Kind == "CONFIRM_CLEAR" && run.Steps[run.StepIndex].Kind == "RESERVE")
            {
                var nextStep = run.Steps[run.StepIndex];
                if (!AnyFault() && !_exitBlocked && _barrierState == "OPEN" && SensorsHealthyAndClear()
                    && _bridge.TryReserve(run.Route.Id, run.VehicleId, true, true, true, out _))
                {
                    run.HasBridgePermission = true;
                    run.JourneyState = "AUTHORIZED";
                    run.ActiveEntrySignal = nextStep.From;
                    run.ActivePermissionUsesBridge = true;
                    _status = $"D đã EMPTY; tự cấp quyền chặng kế tiếp {run.Route.Id} cho xe {run.VehicleId}.";
                    AddLog(_status, "INFO");
                }
            }
            _vehicles[run.VehicleId] = ToVehicleState(run, run.JourneyState, run.HasBridgePermission);
            PromoteQueue();
            RefreshSignals();
            return new ApiResult(true, _status, VehicleId: run.VehicleId);
        }
    }

    public ApiResult UpdateSensor(SensorUpdateRequest request)
    {
        lock (_gate)
        {
            var id = request.SensorId?.Trim().ToUpperInvariant() ?? "";
            var state = request.State?.Trim().ToUpperInvariant() ?? "";
            if (!_sensors.ContainsKey(id)) return Reject("SensorId chỉ nhận D-IN hoặc D-OUT.");
            if (!SupportedSensorStates.Contains(state)) return Reject("Trạng thái cảm biến không hợp lệ.");

            _sensors[id] = state;
            AddLog($"Mô phỏng {id}={state}.", state is "FAULT" or "OFFLINE" or "CONTRADICTORY" or "STUCK_ON" or "STUCK_OFF" ? "ERROR" : "INFO");
            if (state is "FAULT" or "OFFLINE" or "STALE" or "STUCK_ON" or "STUCK_OFF" or "CONTRADICTORY")
            {
                _sensorFault = true;
                _bridge.EnterFault();
                _status = $"FAULT: cảm biến {id} ở trạng thái {state}; mọi quyền D bị khóa.";
                RefreshSignals();
                return new ApiResult(true, _status);
            }

            if (state == "DETECTED")
            {
                var owner = _activeRuns.FirstOrDefault(run => run.VehicleId == _bridge.VehicleId);
                if (id == "D-OUT" && _sensors["D-IN"] != "CLEAR")
                    return Fault("D-OUT tác động trong khi D-IN vẫn DETECTED; cảm biến mâu thuẫn hoặc thứ tự sự kiện không hợp lệ.");
                var accepted = id == "D-IN"
                    ? owner is not null && _bridge.TryEnter(owner.Route.Id, owner.VehicleId, out _)
                    : owner is not null && _bridge.TryBeginClearing(owner.Route.Id, owner.VehicleId, out _);
                if (!accepted)
                {
                    _bridge.EnterFault(possibleOccupancy: true);
                    _status = $"FAULT: {id} được kích hoạt không có xe/quyền hợp lệ.";
                    AddLog(_status, "ERROR");
                    RefreshSignals();
                    return new ApiResult(false, _status);
                }
                if (owner is not null)
                {
                    owner.CurrentNode = id == "D-IN" ? "D" : owner.Route.Nodes.Last();
                    owner.JourneyState = id == "D-IN" ? "OCCUPIED" : "CLEARING";
                    owner.HasBridgePermission = false;
                    owner.StepIndex++;
                    owner.UpdatedAt = DateTimeOffset.UtcNow;
                }
            }
            else if (state == "CLEAR" && _bridge.Phase == BridgePhases.Clearing && SensorsHealthyAndClear())
            {
                if (!_bridge.TryConfirmEmpty(true, out var error)) return Reject(error);
                _status = "D được xác nhận EMPTY bằng hai cảm biến CLEAR trong mô phỏng.";
                AddLog(_status, "OK");
                var owner = _activeRuns.FirstOrDefault(run => run.VehicleId == _bridge.VehicleId);
                if (owner is not null)
                {
                    owner.StepIndex++;
                    owner.JourneyState = "MOVING";
                    owner.UpdatedAt = DateTimeOffset.UtcNow;
                }
            }

            RefreshSignals();
            return new ApiResult(true, "Đã ghi nhận trạng thái cảm biến mô phỏng.");
        }
    }

    public ApiResult ConfirmBridgeEmpty(string actor)
    {
        lock (_gate)
        {
            if (string.IsNullOrWhiteSpace(actor)) return Reject("Cần ghi nhận người xác nhận.");
            if (AnyFault() || _vehicleStuck) return Reject("Không thể xác nhận EMPTY khi lỗi hoặc xe kẹt còn hoạt động.");
            _sensors["D-IN"] = "CLEAR";
            _sensors["D-OUT"] = "CLEAR";
            if (!_bridge.TryConfirmEmpty(true, out var error)) return Reject(error);
            _status = $"Người thao tác {actor.Trim()} xác nhận D EMPTY trong mô phỏng.";
            AddLog(_status, "WARN");
            PromoteQueue();
            RefreshSignals();
            return new ApiResult(true, _status);
        }
    }

    public ApiResult SetBarrier(BarrierRequest request)
    {
        lock (_gate)
        {
            var state = request.State?.Trim().ToUpperInvariant() ?? "";
            if (state is not ("OPEN" or "CLOSED" or "MOVING" or "UNKNOWN" or "FAULT"))
                return Reject("Barrier nhận OPEN, CLOSED, MOVING, UNKNOWN hoặc FAULT.");
            if (string.IsNullOrWhiteSpace(request.Actor)) return Reject("Cần ghi nhận người thao tác barrier.");
            _barrierState = state;
            _status = $"Barrier mô phỏng chuyển sang {state} do {request.Actor.Trim()}; không có lệnh phần cứng.";
            AddLog($"{_status} {request.Reason}".Trim(), state == "FAULT" ? "ERROR" : "INFO");
            if (state == "FAULT") _bridge.EnterFault();
            else if (state != "OPEN" && _bridge.Phase == BridgePhases.Reserved) _bridge.MarkUnknown();
            PromoteQueue();
            RefreshSignals();
            return new ApiResult(true, _status);
        }
    }

    public void SetFaults(FaultRequest request)
    {
        lock (_gate)
        {
            _exitBlocked = request.ExitBlocked;
            _sensorFault = request.SensorFault;
            _vehicleStuck = request.VehicleStuck;
            _emergencyStop = request.EmergencyStop;
            if (_sensorFault || _vehicleStuck || _emergencyStop)
            {
                _bridge.EnterFault(_vehicleStuck);
                _status = "FAULT: lỗi cảm biến, xe kẹt hoặc dừng khẩn cấp; không cấp quyền mới.";
                AddLog(_status, "ERROR");
            }
            else
            {
                _status = _exitBlocked ? "Lối ra bị chặn; yêu cầu qua D được giữ trong hàng đợi." : "Đã cập nhật cờ lỗi mô phỏng.";
                AddLog(_status, _exitBlocked ? "WARN" : "INFO");
            }
            PromoteQueue();
            RefreshSignals();
        }
    }

    public void ClearFaults()
    {
        lock (_gate)
        {
            _exitBlocked = false;
            _sensorFault = false;
            _vehicleStuck = false;
            _emergencyStop = false;
            _bridge.ClearFaultToUnknown();
            _status = "Đã xóa cờ lỗi mô phỏng; trạng thái D là UNKNOWN cho đến khi xác nhận cảm biến CLEAR.";
            AddLog(_status, "WARN");
            RefreshSignals();
        }
    }

    public ApiResult Reset()
    {
        lock (_gate)
        {
            if (_bridge.Phase is BridgePhases.Reserved or BridgePhases.Occupied or BridgePhases.Clearing)
                return Reject($"Không thể reset tiến trình khi D={_bridge.Phase}; cần hoàn tất/xác minh chặng hiện tại trước.");

            foreach (var run in _activeRuns.Where(item => !item.Route.UsesBridge).ToArray())
            {
                run.JourneyState = "CANCELLED";
                _vehicles[run.VehicleId] = ToVehicleState(run, run.JourneyState, false);
            }
            _activeRuns.RemoveAll(item => !item.Route.UsesBridge);
            while (_queue.TryDequeue(out var queued))
            {
                queued.JourneyState = "CANCELLED";
                _vehicles[queued.VehicleId] = ToVehicleState(queued, queued.JourneyState, false);
            }
            _displayRun = null;
            _status = $"Reset mô phỏng. Trạng thái vật lý D vẫn là {_bridge.Phase}; reset không xác nhận cầu trống.";
            AddLog(_status, "WARN");
            RefreshSignals();
            return new ApiResult(true, _status);
        }
    }

    public BridgeState GetState()
    {
        lock (_gate)
        {
            RefreshSignals();
            var signalMap = _signals.ToDictionary(pair => pair.Key, pair => pair.Value.State, StringComparer.Ordinal);
            var currentRun = _activeRuns.OrderBy(run => run.RequestedAt).FirstOrDefault() ?? _displayRun;
            var scenario = currentRun?.Route.Id ?? _scenario;
            var route = currentRun?.Route ?? TrafficNetwork.FindRoute(scenario)!;
            var queued = _queue.Select((run, index) => new QueueEntry(
                run.VehicleId, run.VehicleType, run.Route.Id, index + 1, run.RequestedAt, run.LicensePlate)).ToArray();
            var occupied = _bridge.IsOccupied || _vehicleStuck;
            var faults = AnyFault();
            var lastStep = currentRun is null || currentRun.StepIndex == 0
                ? null
                : currentRun.Steps[Math.Min(currentRun.StepIndex - 1, currentRun.Steps.Count - 1)];
            return new BridgeState(
                scenario,
                currentRun?.StepIndex ?? 0,
                currentRun?.Steps.Count ?? DemoRouteCatalog.BuildSteps(scenario).Count,
                _bridge.Phase,
                occupied,
                _bridge.Phase == BridgePhases.Reserved ? _bridge.RouteId : null,
                _sensors["D-IN"] == "DETECTED",
                _sensors["D-OUT"] == "DETECTED",
                _exitBlocked,
                _barrierState == "OPEN",
                _sensorFault,
                _vehicleStuck,
                _emergencyStop,
                faults,
                _status,
                signalMap,
                _logs.TakeLast(150).Reverse().ToArray(),
                _barrierState,
                route.Verified,
                route.VerificationNote,
                currentRun?.VehicleId ?? _bridge.VehicleId,
                new Dictionary<string, string>(_sensors, StringComparer.Ordinal),
                _vehicles.Values.OrderBy(vehicle => vehicle.UpdatedAt).ToArray(),
                queued,
                _signals.Values.ToArray(),
                lastStep?.SegmentIndex ?? currentRun?.Steps.FirstOrDefault()?.SegmentIndex ?? 0,
                lastStep?.Progress ?? 0,
                currentRun?.VehicleType);
        }
    }

    public IReadOnlyList<string> GetLogs()
    {
        lock (_gate) return _logs.TakeLast(150).Reverse().ToArray();
    }

    public IReadOnlyList<VehicleState> GetVehicles()
    {
        lock (_gate) return _vehicles.Values.OrderBy(vehicle => vehicle.UpdatedAt).ToArray();
    }

    public IReadOnlyList<QueueEntry> GetQueue()
    {
        lock (_gate) return _queue.Select((run, index) => new QueueEntry(
            run.VehicleId, run.VehicleType, run.Route.Id, index + 1, run.RequestedAt, run.LicensePlate)).ToArray();
    }

    private bool CanActivate(VehicleRun run)
    {
        if (AnyConflictWithActive(run.Route)) return false;
        if (run.Route.UsesBridge && (_bridge.Phase != BridgePhases.Empty || _exitBlocked || _barrierState != "OPEN" || AnyFault())) return false;
        return true;
    }

    private bool AnyConflictWithActive(TrafficRoute route) =>
        _activeRuns.Any(active => route.ConflictZones.Intersect(active.Route.ConflictZones, StringComparer.Ordinal).Any());

    private void PromoteQueue()
    {
        if (!_queue.TryPeek(out var next) || !CanActivate(next)) return;
        var firstStep = next.Steps[0];
        if (firstStep.Kind == "RESERVE")
        {
            if (!_bridge.TryReserve(next.Route.Id, next.VehicleId, !_exitBlocked, SensorsHealthyAndClear(), _barrierState == "OPEN", out _))
                return;
            next.JourneyState = "AUTHORIZED";
            next.HasBridgePermission = true;
            next.ActiveEntrySignal = firstStep.From;
            next.ActivePermissionUsesBridge = true;
        }
        else if (firstStep.Kind == "AUTHORIZE_DIRECT")
        {
            next.JourneyState = "AUTHORIZED";
            next.ActiveEntrySignal = firstStep.From;
            next.ActivePermissionUsesBridge = false;
        }
        else
        {
            next.JourneyState = "READY";
        }
        _queue.Dequeue();
        _activeRuns.Add(next);
        _displayRun = next;
        _vehicles[next.VehicleId] = ToVehicleState(next, next.JourneyState, next.HasBridgePermission);
        _status = next.ActivePermissionUsesBridge
            ? $"FIFO cấp quyền D cho xe {next.VehicleId}, tuyến {next.Route.Id}; đèn đầu tuyến được mở xanh."
            : $"FIFO cấp quyền tuyến ngoài D cho xe {next.VehicleId}, tuyến {next.Route.Id}.";
        AddLog(_status, "INFO");
    }

    private ApiResult Fault(string message)
    {
        _bridge.EnterFault(possibleOccupancy: true);
        _status = $"FAULT: {message}";
        AddLog(_status, "ERROR");
        RefreshSignals();
        return new ApiResult(false, _status);
    }

    private ApiResult Reject(string message)
    {
        _status = message;
        AddLog($"Từ chối thao tác: {message}", "WARN");
        RefreshSignals();
        return new ApiResult(false, message);
    }

    private bool SensorsHealthyAndClear() =>
        _sensors["D-IN"] == "CLEAR" && _sensors["D-OUT"] == "CLEAR" && !_sensorFault;

    private bool AnyFault() => _sensorFault || _vehicleStuck || _emergencyStop || _bridge.Phase == BridgePhases.Fault || _barrierState == "FAULT";

    private void RefreshSignals()
    {
        var now = DateTimeOffset.UtcNow;
        var readyRoute = _activeRuns.OrderBy(run => run.RequestedAt).FirstOrDefault()?.Route
            ?? TrafficNetwork.FindRoute(_scenario);
        var emptyAndReady = _bridge.Phase == BridgePhases.Empty && SensorsHealthyAndClear()
            && !AnyFault() && !_exitBlocked && _barrierState == "OPEN";
        foreach (var signalId in new[] { "A", "B" })
        {
            var active = _activeRuns.FirstOrDefault(run => run.ActiveEntrySignal == signalId
                && (run.JourneyState == "AUTHORIZED" || run.HasBridgePermission && run.ActivePermissionUsesBridge));
            var route = active?.Route;
            var safe = route is not null && !AnyFault() && !_exitBlocked && (!active!.ActivePermissionUsesBridge || (_bridge.Phase == BridgePhases.Reserved && _barrierState == "OPEN"));
            var readyGreen = emptyAndReady && (_activeRuns.Count == 0 || readyRoute?.EntrySignal == signalId);
            var desired = safe || readyGreen ? "GREEN" : AnyFault() ? "RED" : _bridge.Phase == BridgePhases.Unknown ? "UNKNOWN" : "RED";
            var reason = safe
                ? $"Quyền mô phỏng hợp lệ cho xe {active!.VehicleId}, tuyến {route!.Id}."
                : readyGreen ? $"D trống; hướng {signalId} sẵn sàng trong mô phỏng. Quyền vào D vẫn cấp theo liên động."
                : AnyFault() ? "Khóa do lỗi/fail-safe."
                : _bridge.Phase is BridgePhases.Occupied or BridgePhases.Clearing ? "D đang OCCUPIED/CLEARING; khóa tuyến xung đột."
                : _bridge.Phase == BridgePhases.Unknown ? "Trạng thái vật lý D UNKNOWN; không cấp quyền D."
                : _exitBlocked ? "Lối ra bị chặn."
                : _barrierState != "OPEN" && route?.UsesBridge == true ? $"Barrier {_barrierState}; không cấp quyền D."
                : "Chưa có quyền được cấp.";
            var prior = _signals[signalId];
            if (prior.State != desired || prior.Reason != reason)
                _signals[signalId] = prior with { State = desired, ChangedAt = now, Reason = reason };
        }
    }

    private static VehicleState ToVehicleState(VehicleRun run, string journeyState, bool hasPermission)
    {
        var lastStep = run.StepIndex == 0 ? null : run.Steps[Math.Min(run.StepIndex - 1, run.Steps.Count - 1)];
        return new VehicleState(
            run.VehicleId,
            run.VehicleType,
            run.Route.Id,
            run.CurrentNode,
            journeyState,
            hasPermission,
            run.UpdatedAt,
            run.StepIndex,
            run.Steps.Count,
            lastStep?.SegmentIndex ?? run.Steps.FirstOrDefault()?.SegmentIndex ?? 0,
            lastStep?.Progress ?? 0,
            run.LicensePlate);
    }

    private void AddLog(string message, string level)
    {
        _logs.Add($"{DateTimeOffset.Now:HH:mm:ss} [{level}] {message}");
        if (_logs.Count > 500) _logs.RemoveRange(0, _logs.Count - 500);
    }
}
