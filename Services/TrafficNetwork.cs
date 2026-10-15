namespace BridgeTrafficDemo.Services;

public sealed record TrafficNode(string Id, string Label, int X, int Y, string Kind);

public sealed record TrafficEdge(
    string Id,
    string From,
    string To,
    string ConflictZone,
    bool Verified,
    string VerificationNote);

public sealed record TrafficRoute(
    string Id,
    string SourceRoute,
    IReadOnlyList<string> Nodes,
    IReadOnlyList<string> Edges,
    IReadOnlyList<string> ConflictZones,
    string EntrySignal,
    bool Verified,
    string VerificationNote)
{
    public bool UsesBridge => ConflictZones.Contains("D", StringComparer.Ordinal);
    public int StepCount => DemoRouteCatalog.BuildSteps(Id).Count;
}

public static class TrafficNetwork
{
    public static IReadOnlyList<TrafficNode> Nodes { get; } =
    [
        new("A", "A", 70, 320, "approach"),
        new("B", "B", 710, 340, "approach"),
        new("C", "C", 710, 115, "destination"),
        new("D", "D", 390, 220, "exclusive-zone"),
        new("E", "E", 300, 115, "barrier-branch")
    ];

    public static IReadOnlyList<TrafficEdge> Edges { get; } =
    [
        new("A-D", "A", "D", "D", true, "Sơ đồ thể hiện nhánh A tiếp cận đầu cầu."),
        new("D-A", "D", "A", "D", true, "Tuyến nguồn B → D → A."),
        new("B-D", "B", "D", "D", true, "Sơ đồ thể hiện nhánh B tiếp cận đầu cầu."),
        new("D-B", "D", "B", "D", true, "Chiều ngược chưa có tuyến nguồn xác nhận."),
        new("D-C", "D", "C", "D", true, "Tuyến nguồn A → D → C."),
        new("C-D", "C", "D", "D", false, "Chiều C → D trong tuyến xe đặc biệt cần xác minh hiện trường."),
        new("B-C", "B", "C", "B-C-bypass", true, "Tuyến nguồn B → C đi vòng ngoài D theo sơ đồ."),
        new("D-E", "D", "E", "D", true, "Nhánh E nhập vào D tại vị trí barrier theo sơ đồ."),
        new("E-D", "E", "D", "D", true, "Nhánh E nhập vào D tại vị trí barrier theo sơ đồ.")
    ];

    public static IReadOnlyList<TrafficRoute> Routes { get; } =
    [
        new("R1", "A → D → C", ["A", "D", "C"], ["A-D", "D-C"], ["D", "C-merge"], "A", true, "Tuyến nguồn đã thể hiện."),
        new("R2", "B → D → A", ["B", "D", "A"], ["B-D", "D-A"], ["D", "B-approach"], "B", true, "Tuyến nguồn đã thể hiện."),
        new("R3", "B → C", ["B", "C"], ["B-C"], ["B-C-bypass", "B-approach", "C-merge"], "B", true, "Tuyến bypass ngoài D; chia sẻ vùng tiếp cận B/C cần điều phối."),
        new("R4", "B → C → D → A → D → C", ["B", "C", "D", "A", "D", "C"], ["B-C", "C-D", "D-A", "A-D", "D-C"], ["B-C-bypass", "D", "B-approach", "C-merge"], "B", false, "Giữ nguyên tuyến nguồn. Chặng C → D và vùng xung đột cần xác minh; bị chặn ở strict mode."),
        new("R5", "A → E", ["A", "D", "E"], ["A-D", "D-E"], ["D", "E-barrier"], "A", true, "Hai đầu tuyến A/E; hành trình đi qua vùng D đến barrier."),
        new("R6", "E → A", ["E", "D", "A"], ["E-D", "D-A"], ["D", "E-barrier"], "NONE", true, "E không có đèn tín hiệu; tiếp cận qua barrier thủ công.")
    ];

    public static IReadOnlyList<IReadOnlyList<string>> ConflictMatrix { get; } =
        Routes.Select(route => (IReadOnlyList<string>)Routes
            .Where(other => !StringComparer.Ordinal.Equals(route.Id, other.Id)
                && route.ConflictZones.Intersect(other.ConflictZones, StringComparer.Ordinal).Any())
            .Select(other => other.Id)
            .ToArray())
        .ToArray();

    public static TrafficRoute? FindRoute(string id) =>
        Routes.FirstOrDefault(route => StringComparer.OrdinalIgnoreCase.Equals(route.Id, id));

    public static IReadOnlyList<string> GetConflicts(string routeId)
    {
        var route = FindRoute(routeId);
        return route is null
            ? Array.Empty<string>()
            : Routes.Where(other => other.Id != route.Id
                    && route.ConflictZones.Intersect(other.ConflictZones, StringComparer.Ordinal).Any())
                .Select(other => other.Id)
                .ToArray();
    }

    public static IReadOnlyList<string> ValidateGraph()
    {
        var nodeIds = Nodes.Select(node => node.Id).ToHashSet(StringComparer.Ordinal);
        var edgeIds = Edges.Select(edge => edge.Id).ToHashSet(StringComparer.Ordinal);
        var problems = new List<string>();
        problems.AddRange(Edges.Where(edge => !nodeIds.Contains(edge.From) || !nodeIds.Contains(edge.To))
            .Select(edge => $"Cạnh {edge.Id} tham chiếu nút không tồn tại."));
        problems.AddRange(Routes.SelectMany(route => route.Edges.Where(edgeId => !edgeIds.Contains(edgeId))
            .Select(edgeId => $"Tuyến {route.Id} tham chiếu cạnh {edgeId} không tồn tại.")));
        problems.AddRange(Routes.Where(route => route.Nodes.Count != route.Edges.Count + 1)
            .Select(route => $"Tuyến {route.Id} có số nút/cạnh không khớp."));
        return problems;
    }
}
