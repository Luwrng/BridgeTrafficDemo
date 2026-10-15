namespace BridgeTrafficDemo.Services;

public static class BridgePhases
{
    public const string Empty = "EMPTY";
    public const string Reserved = "RESERVED";
    public const string Occupied = "OCCUPIED";
    public const string Clearing = "CLEARING";
    public const string Unknown = "UNKNOWN";
    public const string Fault = "FAULT";
}

public sealed class BridgeStateMachine
{
    private bool _occupancyObserved;

    public string Phase { get; private set; } = BridgePhases.Unknown;
    public string? RouteId { get; private set; }
    public string? VehicleId { get; private set; }
    public bool IsOccupied => _occupancyObserved || Phase is BridgePhases.Occupied or BridgePhases.Clearing;
    public bool CanGrant => Phase == BridgePhases.Empty && RouteId is null;

    public bool TryReserve(
        string routeId,
        string vehicleId,
        bool exitClear,
        bool sensorsHealthy,
        bool barrierOpen,
        out string error)
    {
        if (Phase != BridgePhases.Empty)
            return Fail($"Không cấp quyền: trạng thái D là {Phase}, cần EMPTY.", out error);
        if (RouteId is not null)
            return Fail($"Không cấp quyền: tuyến {RouteId} vẫn đang giữ quyền.", out error);
        if (!exitClear)
            return Fail("Không cấp quyền: lối ra D bị chặn hoặc chưa xác nhận thông.", out error);
        if (!sensorsHealthy)
            return Fail("Không cấp quyền: cảm biến D không ở trạng thái khỏe.", out error);
        if (!barrierOpen)
            return Fail("Không cấp quyền: barrier E chưa được xác nhận mở.", out error);

        RouteId = routeId;
        VehicleId = vehicleId;
        Phase = BridgePhases.Reserved;
        error = string.Empty;
        return true;
    }

    public bool TryEnter(string routeId, string vehicleId, out string error)
    {
        if (Phase != BridgePhases.Reserved || RouteId != routeId || VehicleId != vehicleId)
            return EnterFault("Phát hiện D-IN không khớp quyền RESERVED hiện tại.", out error);

        Phase = BridgePhases.Occupied;
        _occupancyObserved = true;
        error = string.Empty;
        return true;
    }

    public bool TryBeginClearing(string routeId, string vehicleId, out string error)
    {
        if (Phase != BridgePhases.Occupied || RouteId != routeId || VehicleId != vehicleId)
            return EnterFault("D-OUT được kích hoạt khi không có đúng xe được xác nhận trong D.", out error);

        Phase = BridgePhases.Clearing;
        error = string.Empty;
        return true;
    }

    public bool TryConfirmEmpty(bool sensorsClear, out string error)
    {
        if (Phase is not (BridgePhases.Unknown or BridgePhases.Clearing))
            return Fail($"Không xác nhận EMPTY từ trạng thái {Phase}.", out error);
        if (!sensorsClear)
            return Fail("Không xác nhận EMPTY: cảm biến không cùng xác nhận vùng D trống.", out error);

        Phase = BridgePhases.Empty;
        _occupancyObserved = false;
        RouteId = null;
        VehicleId = null;
        error = string.Empty;
        return true;
    }

    public void EnterFault(bool possibleOccupancy = false)
    {
        _occupancyObserved |= possibleOccupancy;
        Phase = BridgePhases.Fault;
    }

    public void ClearFaultToUnknown()
    {
        if (Phase == BridgePhases.Fault)
            Phase = BridgePhases.Unknown;
    }

    public void MarkUnknown()
    {
        if (Phase != BridgePhases.Fault)
            Phase = BridgePhases.Unknown;
    }

    private bool EnterFault(string message, out string error)
    {
        _occupancyObserved = true;
        Phase = BridgePhases.Fault;
        error = message;
        return false;
    }

    private static bool Fail(string message, out string error)
    {
        error = message;
        return false;
    }
}
