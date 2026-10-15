using BridgeTrafficDemo.Models;
using BridgeTrafficDemo.Services;
using Microsoft.ML.OnnxRuntime;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddSingleton<BridgeSimulationService>();
builder.Services.AddSingleton<CameraStreamService>();
builder.Services.AddSingleton<LicensePlateDetectionService>();
builder.Services.AddSingleton<LicensePlateRecognitionService>();
builder.Services.AddSingleton<CameraSystemStartupService>();
builder.Services.AddHostedService(provider => provider.GetRequiredService<CameraSystemStartupService>());

var app = builder.Build();

app.UseDefaultFiles();
app.UseStaticFiles();

// Tự phục hồi dịch vụ khi người dùng kết nối camera sau một lần worker dừng.
// Chỉ web local được gọi, không nhận lệnh/path/RTSP từ request này.
app.MapPost("/api/system/start-camera", async (HttpContext context, CameraSystemStartupService startup) =>
{
    var origin = context.Request.Headers.Origin.ToString();
    if (context.Connection.RemoteIpAddress is not { } remote || !System.Net.IPAddress.IsLoopback(remote)
        || (origin.Length > 0 && origin != $"{context.Request.Scheme}://{context.Request.Host}"))
        return Results.Forbid();
    try
    {
        await startup.EnsureStartedAsync(context.RequestAborted);
        return Results.Ok(new { available = true });
    }
    catch (Exception error) when (error is not OperationCanceledException)
    {
        return Results.Problem("Dịch vụ chưa sẵn sàng. Kiểm tra Data/service-logs.", statusCode:503);
    }
});

app.MapGet("/api/state", (BridgeSimulationService simulation) => Results.Ok(simulation.GetState()));
app.MapGet("/api/logs", (BridgeSimulationService simulation) => Results.Ok(simulation.GetLogs()));
app.MapGet("/api/network", () => Results.Ok(new
{
    simulationOnly = true,
    nodes = TrafficNetwork.Nodes,
    edges = TrafficNetwork.Edges,
    routes = TrafficNetwork.Routes,
    conflicts = TrafficNetwork.Routes.ToDictionary(route => route.Id, route => TrafficNetwork.GetConflicts(route.Id)),
    validation = TrafficNetwork.ValidateGraph()
}));
app.MapGet("/api/routes", () => Results.Ok(TrafficNetwork.Routes));
app.MapGet("/api/vehicles", (BridgeSimulationService simulation) => Results.Ok(simulation.GetVehicles()));
app.MapGet("/api/queue", (BridgeSimulationService simulation) => Results.Ok(simulation.GetQueue()));
app.MapGet("/api/camera/A/status", async (CameraStreamService camera, HttpContext context) =>
    Results.Ok(await camera.GetStatusAsync(context.RequestAborted)));
app.MapPost("/api/camera/A/configure", async (CameraConfigureRequest request, CameraStreamService camera, HttpContext context) =>
{
    if (context.Connection.RemoteIpAddress is not { } remoteAddress || !System.Net.IPAddress.IsLoopback(remoteAddress))
        return Results.Forbid();

    var result = await camera.ConfigureAsync(request.RtspUrl, context.RequestAborted);
    return result.Ok ? Results.Ok(new { configured = true, message = result.Message }) : Results.BadRequest(new { error = result.Message });
});
app.MapDelete("/api/camera/A/configure", async (CameraStreamService camera, HttpContext context) =>
{
    if (context.Connection.RemoteIpAddress is not { } remoteAddress || !System.Net.IPAddress.IsLoopback(remoteAddress))
        return Results.Forbid();

    await camera.DisconnectAsync();
    return Results.Ok(new { configured = false, message = "Camera A đã ngắt." });
});
app.MapGet("/api/camera/A/recognition-status", (LicensePlateRecognitionService recognition) =>
    Results.Ok(new { available = recognition.IsAvailable }));
app.MapPost("/api/camera/A/recognize", async (
    HttpContext context,
    LicensePlateRecognitionService recognition,
    BridgeSimulationService simulation,
    ILogger<Program> logger) =>
{
    if (context.Connection.RemoteIpAddress is not { } remoteAddress || !System.Net.IPAddress.IsLoopback(remoteAddress))
        return Results.Forbid();
    if (context.Request.ContentType is not { } contentType
        || !contentType.StartsWith("image/", StringComparison.OrdinalIgnoreCase))
        return Results.BadRequest(new { error = "Cần gửi ảnh khung hình camera." });
    var scenario = context.Request.Query["scenario"].ToString();
    var route = TrafficNetwork.FindRoute(scenario);
    if (route is null || route.Nodes.Count == 0 || route.Nodes[0] != "A")
        return Results.Conflict(new { error = "Camera A chỉ nhận diện tuyến đầu vào A (R1 hoặc R5)." });
    if (context.Request.ContentLength is > 2_000_000)
        return Results.BadRequest(new { error = "Ảnh khung hình vượt quá 2 MB." });
    if (!int.TryParse(context.Request.Headers["X-Frame-Width"], out var frameWidth)
        || !int.TryParse(context.Request.Headers["X-Frame-Height"], out var frameHeight)
        || frameWidth is < 1 or > 4096
        || frameHeight is < 1 or > 4096)
        return Results.BadRequest(new { error = "Kích thước khung hình không hợp lệ." });

    using var image = new MemoryStream();
    var buffer = new byte[32 * 1024];
    while (true)
    {
        var read = await context.Request.Body.ReadAsync(buffer, context.RequestAborted);
        if (read == 0) break;
        if (image.Length + read > 2_000_000)
            return Results.BadRequest(new { error = "Ảnh khung hình vượt quá 2 MB." });
        image.Write(buffer, 0, read);
    }

    LicensePlateRecognitionResult result;
    try
    {
        result = await recognition.RecognizeAsync(image.ToArray(), frameWidth, frameHeight, context.RequestAborted);
    }
    catch (Exception error) when (error is IOException or InvalidOperationException or OnnxRuntimeException)
    {
        logger.LogError(error, "Local license plate recognition failed.");
        return Results.Problem(
            title: "Không thể xử lý khung hình camera.",
            detail: error.Message,
            statusCode: StatusCodes.Status500InternalServerError);
    }

    if (!result.Available)
        return Results.Problem(result.Message, statusCode: StatusCodes.Status503ServiceUnavailable);

    var state = simulation.GetState();
    return Results.Ok(new
    {
        cameraId = "A",
        plate = result.Plate,
        rawText = result.RawText,
        confidence = result.Confidence,
        message = result.Message,
        detectedAt = DateTimeOffset.UtcNow,
        phase = state.Phase
    });
});
app.MapGet("/api/camera/A/stream", async (CameraStreamService camera, HttpContext context) =>
{
    var copied = await camera.CopyStreamToAsync(
        context.Response.Body,
        contentType => context.Response.ContentType = contentType,
        context.RequestAborted);
    if (!copied && !context.Response.HasStarted)
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
});

app.MapPost("/api/scenario/select", (ScenarioSelectionRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.SelectScenario(request.Scenario);
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.Conflict(new { error = result.Error, state = simulation.GetState() });
});

app.MapPost("/api/scenario/start", (StartScenarioRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.StartScenario(request);
    if (!result.Ok) return Results.Conflict(new { error = result.Error, state = simulation.GetState() });
    return result.Queued
        ? Results.Accepted("/api/queue", new { result.VehicleId, state = simulation.GetState() })
        : Results.Ok(simulation.GetState());
});

app.MapPost("/api/requests", (StartScenarioRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.StartScenario(request);
    if (!result.Ok) return Results.Conflict(new { error = result.Error, state = simulation.GetState() });
    return result.Queued
        ? Results.Accepted("/api/queue", new { result.VehicleId, state = simulation.GetState() })
        : Results.Ok(simulation.GetState());
});

app.MapPost("/api/scenario/step", (BridgeSimulationService simulation) =>
{
    var result = simulation.Step();
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.Conflict(new { error = result.Error, state = simulation.GetState() });
});

app.MapPost("/api/reset", (BridgeSimulationService simulation) =>
{
    var result = simulation.Reset();
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.Conflict(new { error = result.Error, state = simulation.GetState() });
});

app.MapPost("/api/barrier", (BarrierRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.SetBarrier(request);
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.BadRequest(new { error = result.Error });
});

app.MapPost("/api/sensors", (SensorUpdateRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.UpdateSensor(request);
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.Conflict(new { error = result.Error, state = simulation.GetState() });
});

app.MapPost("/api/sensors/confirm-empty", (ConfirmEmptyRequest request, BridgeSimulationService simulation) =>
{
    var result = simulation.ConfirmBridgeEmpty(request.Actor);
    return result.Ok ? Results.Ok(simulation.GetState()) : Results.Conflict(new { error = result.Error, state = simulation.GetState() });
});

app.MapPost("/api/faults", (FaultRequest request, BridgeSimulationService simulation) =>
{
    simulation.SetFaults(request);
    return Results.Ok(simulation.GetState());
});

app.MapPost("/api/faults/clear", (BridgeSimulationService simulation) =>
{
    simulation.ClearFaults();
    return Results.Ok(simulation.GetState());
});

app.MapFallbackToFile("index.html");
app.Run();
