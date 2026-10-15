namespace BridgeTrafficDemo.Services;

internal sealed record DemoStep(
    string Kind,
    string From,
    string To,
    bool UsesBridge,
    string Status,
    string Event,
    int SegmentIndex,
    double Progress);

internal static class DemoRouteCatalog
{
    public static bool IsValid(string scenario) => TrafficNetwork.FindRoute(scenario) is not null;

    public static bool UsesBridge(string scenario) => TrafficNetwork.FindRoute(scenario)?.UsesBridge ?? false;

    public static List<DemoStep> BuildSteps(string scenario)
    {
        var steps = new List<DemoStep>();
        switch (scenario)
        {
            case "R1": AddBridgePass(steps, "A", "C", "A → D → C", 0); break;
            case "R2": AddBridgePass(steps, "B", "A", "B → D → A", 0); break;
            case "R3": AddDirectPass(steps, "B", "C", "B → C"); break;
            case "R4":
                AddDirectPass(steps, "B", "C", "B → C");
                AddBridgePass(steps, "C", "A", "C → D → A", 1);
                AddBridgePass(steps, "A", "C", "A → D → C", 2);
                break;
            case "R5": AddBridgePass(steps, "A", "E", "A → E qua D", 0); break;
            case "R6": AddBridgePass(steps, "E", "A", "E → A qua D", 0); break;
        }
        return steps;
    }

    private static void AddBridgePass(List<DemoStep> steps, string source, string destination, string route, int segmentIndex)
    {
        steps.Add(new("RESERVE", source, source, true, $"Xin quyền cho chặng {route}.", $"Yêu cầu quyền chặng {route}.", segmentIndex, 0));
        steps.Add(new("APPROACH", source, "D", true, $"Xe từ {source} tiến tới vùng D theo {route}.", $"Xe tiếp cận D trên chặng {route}.", segmentIndex, 0.18));
        steps.Add(new("D_IN", source, "D", true, "Mô phỏng D-IN xác nhận xe đã vào D.", "D-IN=DETECTED; chuyển RESERVED → OCCUPIED.", segmentIndex, 0.36));
        steps.Add(new("CROSS", "D", "D", true, "Xe di chuyển trong vùng cầu; khóa các tuyến xung đột.", "Xe đang ở vùng D; giữ interlock.", segmentIndex, 0.62));
        steps.Add(new("D_OUT", "D", destination, true, "Mô phỏng D-OUT xác nhận xe bắt đầu ra D.", "D-OUT=DETECTED; chuyển OCCUPIED → CLEARING.", segmentIndex, 0.86));
        steps.Add(new("CONFIRM_CLEAR", destination, destination, true, "Hai cảm biến cùng CLEAR; giải phóng quyền chặng.", "D-IN/D-OUT=CLEAR; chuyển CLEARING → EMPTY.", segmentIndex, 1));
    }

    private static void AddDirectPass(List<DemoStep> steps, string source, string destination, string route)
    {
        var segmentIndex = steps.Count == 0 ? 0 : steps[^1].SegmentIndex + 1;
        steps.Add(new("AUTHORIZE_DIRECT", source, source, false, $"Cấp quyền nhánh ngoài D cho {route}.", $"Cấp quyền tuyến trực tiếp {route}; không giữ quyền D.", segmentIndex, 0));
        steps.Add(new("MOVE", source, destination, false, $"Xe đi theo nhánh ngoài D: {route}.", $"Di chuyển cạnh {source} → {destination}; không chiếm dụng D.", segmentIndex, 0.65));
        steps.Add(new("COMPLETE_DIRECT", destination, destination, false, $"Hoàn tất chặng ngoài D: {route}.", $"Hoàn tất {route}; D-IN/D-OUT không đổi.", segmentIndex, 1));
    }
}
