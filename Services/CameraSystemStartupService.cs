using System.Diagnostics;

namespace BridgeTrafficDemo.Services;

/// <summary>
/// Khởi động camera, PaddleOCR và API hồ sơ trước khi web nhận yêu cầu.
/// Chỉ gọi launcher cố định; không truyền RTSP/mật khẩu qua dòng lệnh.
/// Worker đang chạy được giữ lại, vì tắt broker sẽ mất link lưu trong RAM.
/// </summary>
public sealed class CameraSystemStartupService(IWebHostEnvironment environment) : IHostedService
{
    private readonly SemaphoreSlim startupLock = new(1, 1);
    public Task StartAsync(CancellationToken cancellationToken) => EnsureStartedAsync(cancellationToken);

    public async Task EnsureStartedAsync(CancellationToken cancellationToken)
    {
        await startupLock.WaitAsync(cancellationToken);
        try { await StartWorkersAsync(cancellationToken); }
        finally { startupLock.Release(); }
    }

    private async Task StartWorkersAsync(CancellationToken cancellationToken)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("Launcher camera hiện được cấu hình cho Windows.");
        var script = Path.Combine(environment.ContentRootPath, "tools", "Start-CameraSystem.ps1");
        var bundledShell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "native", "powershell", "pwsh.exe");
        var start = new ProcessStartInfo(File.Exists(bundledShell) ? bundledShell : "powershell.exe")
        {
            WorkingDirectory = environment.ContentRootPath,
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        foreach (var argument in new[] { "-NoProfile", "-File", script, "-ServicesOnly" })
            start.ArgumentList.Add(argument);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("Không mở được launcher camera.");
        // Đọc hai pipe song song để launcher không bị kẹt vì buffer đầy.
        var output = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var error = process.StandardError.ReadToEndAsync(cancellationToken);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(90));
        try { await process.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException)
        {
            if (!process.HasExited) process.Kill();
            throw;
        }
        await Task.WhenAll(output, error);
        if (process.ExitCode != 0)
            throw new InvalidOperationException("Camera/OCR/hồ sơ chưa sẵn sàng. Xem Data/service-logs và chạy lại project.");
    }

    // Không tắt worker khi web reload/debug: nguồn RTSP và lượt đang mở được giữ.
    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
