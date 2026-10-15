namespace BridgeTrafficDemo.Models;

public sealed record StartScenarioRequest(
    string Scenario,
    string? VehicleId = null,
    string VehicleType = "car",
    string? LicensePlate = null);

public sealed record ScenarioSelectionRequest(string Scenario);

public sealed record BarrierRequest(string State, string Actor = "guard", string Reason = "");

public sealed record SensorUpdateRequest(string SensorId, string State);

public sealed record ConfirmEmptyRequest(string Actor = "operator");

public sealed record FaultRequest(
    bool ExitBlocked = false,
    bool SensorFault = false,
    bool VehicleStuck = false,
    bool EmergencyStop = false);

public sealed record ApiResult(bool Ok, string? Error = null, bool Queued = false, string? VehicleId = null);

public sealed record VehicleState(
    string Id,
    string Type,
    string RouteId,
    string CurrentNode,
    string JourneyState,
    bool HasBridgePermission,
    DateTimeOffset UpdatedAt,
    int StepIndex = 0,
    int StepCount = 0,
    int SegmentIndex = 0,
    double RouteProgress = 0,
    string? LicensePlate = null);

public sealed record QueueEntry(
    string VehicleId,
    string VehicleType,
    string RouteId,
    int Position,
    DateTimeOffset RequestedAt,
    string? LicensePlate = null);

public sealed record SignalState(
    string Id,
    string RouteId,
    string State,
    string DeviceState,
    DateTimeOffset ChangedAt,
    string Reason);

public sealed record BridgeState(
    string Scenario,
    int StepIndex,
    int StepCount,
    string Phase,
    bool Occupied,
    string? GrantedDirection,
    bool DIn,
    bool DOut,
    bool ExitBlocked,
    bool BarrierOpen,
    bool SensorFault,
    bool VehicleStuck,
    bool EmergencyStop,
    bool FailSafe,
    string Status,
    IReadOnlyDictionary<string, string> Signals,
    IReadOnlyList<string> Logs,
    string BarrierState,
    bool RouteVerified,
    string RouteVerificationNote,
    string? CurrentVehicleId,
    IReadOnlyDictionary<string, string> Sensors,
    IReadOnlyList<VehicleState> Vehicles,
    IReadOnlyList<QueueEntry> Queue,
    IReadOnlyList<SignalState> SignalDetails,
    int SegmentIndex = 0,
    double RouteProgress = 0,
    string? CurrentVehicleType = null);
