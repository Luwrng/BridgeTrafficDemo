using System.Diagnostics;
using System.ComponentModel;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;

namespace BridgeTrafficDemo.Services;

public sealed record CameraStreamStatus(bool Configured, bool StreamAvailable, string Message);

public sealed class CameraStreamService : IDisposable
{
    private readonly SemaphoreSlim _gate = new(1, 1);
    private Process? _vlc;
    private Process? _ffmpeg;
    private CancellationTokenSource? _captureCancellation;
    private Task? _captureTask;
    private byte[]? _latestFrame;
    private long _lastFrameAt;
    private int _streamPort;
    private string _message = "Chưa cấu hình camera A.";

    public async Task<(bool Ok, string Message)> ConfigureAsync(string rtspUrl, CancellationToken cancellationToken)
    {
        var urls = Regex.Split(rtspUrl?.Trim() ?? "", "(?=rtsp://)", RegexOptions.IgnoreCase)
            .Where(value => !string.IsNullOrWhiteSpace(value)).Select(value => value.Trim()).ToArray();
        if (urls.Length > 1 && urls.Skip(1).Any(value => value != urls[0]))
            return (false, "Chỉ nhập một nguồn RTSP; các địa chỉ đang khác nhau.");
        rtspUrl = urls.FirstOrDefault() ?? "";
        if (!TryValidateRtspUrl(rtspUrl, out var validationError))
            return (false, validationError);

        await _gate.WaitAsync(cancellationToken);
        try
        {
            StopProcesses();
            var vlcPath = FindVlc();
            if (vlcPath is null)
            {
                _message = "Không tìm thấy VLC. Cài VLC hoặc đặt biến môi trường VLC_EXE.";
                return (false, _message);
            }
            var ffmpegPath = FindFFmpeg();
            if (ffmpegPath is null)
            {
                _message = "Không tìm thấy FFmpeg. Cài FFmpeg hoặc đặt biến môi trường FFMPEG_EXE.";
                return (false, _message);
            }

            var remoteControlPort = GetAvailablePort();
            _streamPort = GetAvailablePort();
            var startInfo = new ProcessStartInfo(vlcPath)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            foreach (var argument in new[]
            {
                "--no-one-instance",
                "--intf=dummy",
                "--extraintf=oldrc",
                $"--rc-host=127.0.0.1:{remoteControlPort}",
                "--rtsp-tcp",
                "--no-audio",
                "--verbose=-1",
                $"--sout=#standard{{access=http,mux=ts,dst=127.0.0.1:{_streamPort}/camera}}"
            })
                startInfo.ArgumentList.Add(argument);

            _vlc = Process.Start(startInfo);
            if (_vlc is null)
            {
                _message = "Không khởi động được VLC.";
                return (false, _message);
            }

            _vlc.OutputDataReceived += (_, _) => { };
            _vlc.ErrorDataReceived += (_, _) => { };
            _vlc.BeginOutputReadLine();
            _vlc.BeginErrorReadLine();

            using var client = await ConnectToVlcAsync(remoteControlPort, cancellationToken);
            if (client is null)
            {
                StopProcesses();
                _message = "VLC không mở được kênh điều khiển nội bộ.";
                return (false, _message);
            }

            var escapedUrl = rtspUrl.Trim().Replace("\\", "\\\\", StringComparison.Ordinal)
                .Replace("\"", "\\\"", StringComparison.Ordinal);
            await using var writer = new StreamWriter(client.GetStream());
            await writer.WriteLineAsync($"add \"{escapedUrl}\"".AsMemory(), cancellationToken);
            await writer.FlushAsync(cancellationToken);

            var ffmpegInfo = new ProcessStartInfo(ffmpegPath)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            foreach (var argument in new[]
            {
                "-nostdin",
                "-hide_banner",
                "-loglevel", "error",
                "-i", $"http://127.0.0.1:{_streamPort}/camera",
                "-an",
                "-vf", "fps=8,scale=960:-2",
                "-f", "image2pipe",
                "-c:v", "mjpeg",
                "-q:v", "6",
                "pipe:1"
            })
                ffmpegInfo.ArgumentList.Add(argument);

            _ffmpeg = Process.Start(ffmpegInfo);
            if (_ffmpeg is null)
            {
                StopProcesses();
                _message = "Không khởi động được FFmpeg.";
                return (false, _message);
            }

            _ffmpeg.ErrorDataReceived += (_, _) => { };
            _ffmpeg.BeginErrorReadLine();
            _captureCancellation = new CancellationTokenSource();
            _captureTask = CaptureFramesAsync(_ffmpeg, _captureCancellation.Token);
            _message = "Đang chuyển luồng camera cục bộ sang MJPEG.";
            return (true, _message);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception) when (exception is IOException or SocketException or InvalidOperationException
            or Win32Exception or UnauthorizedAccessException)
        {
            StopProcesses();
            _message = "Không thể khởi tạo camera A. Kiểm tra địa chỉ RTSP, thông tin đăng nhập và VLC.";
            return (false, _message);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<CameraStreamStatus> GetStatusAsync(CancellationToken cancellationToken)
    {
        await _gate.WaitAsync(cancellationToken);
        try
        {
            if (_vlc is null || _vlc.HasExited)
                return new CameraStreamStatus(false, false, _message);

            var latestFrame = Volatile.Read(ref _latestFrame);
            var frameIsFresh = latestFrame is not null
                && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - Interlocked.Read(ref _lastFrameAt) < 3000;
            if (frameIsFresh)
                return new CameraStreamStatus(true, true, "Camera A đang phát hình qua bộ chuyển nội bộ.");

            var message = _ffmpeg is null || _ffmpeg.HasExited
                ? "Bộ chuyển video không nhận được khung hình từ camera."
                : "Đang chờ khung hình đầu tiên từ camera.";
            return new CameraStreamStatus(true, false, message);
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task DisconnectAsync()
    {
        await _gate.WaitAsync();
        try
        {
            StopProcesses();
            _message = "Camera A đã ngắt.";
        }
        finally
        {
            _gate.Release();
        }
    }

    public async Task<bool> CopyStreamToAsync(Stream destination, Action<string> setContentType, CancellationToken cancellationToken)
    {
        if (_vlc is null || _vlc.HasExited)
            return false;

        try
        {
            setContentType("multipart/x-mixed-replace; boundary=frame");
            byte[]? lastWrittenFrame = null;
            while (!cancellationToken.IsCancellationRequested)
            {
                var frame = Volatile.Read(ref _latestFrame);
                if (frame is not null && !ReferenceEquals(frame, lastWrittenFrame))
                {
                    var header = Encoding.ASCII.GetBytes(
                        $"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {frame.Length}\r\n\r\n");
                    await destination.WriteAsync(header, cancellationToken);
                    await destination.WriteAsync(frame, cancellationToken);
                    await destination.WriteAsync("\r\n"u8.ToArray(), cancellationToken);
                    await destination.FlushAsync(cancellationToken);
                    lastWrittenFrame = frame;
                }

                if ((_ffmpeg is null || _ffmpeg.HasExited)
                    && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - Interlocked.Read(ref _lastFrameAt) >= 3000)
                    return true;

                await Task.Delay(50, cancellationToken);
            }
            return true;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return true;
        }
        catch (IOException)
        {
            return true;
        }
    }

    private static bool TryValidateRtspUrl(string? value, out string error)
    {
        if (!Uri.TryCreate(value?.Trim(), UriKind.Absolute, out var uri)
            || !string.Equals(uri.Scheme, "rtsp", StringComparison.OrdinalIgnoreCase)
            || !IPAddress.TryParse(uri.Host, out var address)
            || !IsLocalAddress(address))
        {
            error = "Nhập URL RTSP của camera trong mạng nội bộ (địa chỉ IP riêng).";
            return false;
        }

        error = string.Empty;
        return true;
    }

    private static bool IsLocalAddress(IPAddress address)
    {
        if (IPAddress.IsLoopback(address) || address.IsIPv6LinkLocal || address.IsIPv6SiteLocal)
            return true;
        if (address.AddressFamily != AddressFamily.InterNetwork)
            return false;

        var bytes = address.GetAddressBytes();
        return bytes[0] == 10
            || bytes[0] == 192 && bytes[1] == 168
            || bytes[0] == 172 && bytes[1] is >= 16 and <= 31
            || bytes[0] == 169 && bytes[1] == 254;
    }

    private static string? FindVlc()
    {
        var configuredPath = Environment.GetEnvironmentVariable("VLC_EXE");
        if (!string.IsNullOrWhiteSpace(configuredPath) && File.Exists(configuredPath))
            return configuredPath;

        var standardPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "VideoLAN",
            "VLC",
            "vlc.exe");
        return File.Exists(standardPath) ? standardPath : null;
    }

    private static string? FindFFmpeg()
    {
        var configuredPath = Environment.GetEnvironmentVariable("FFMPEG_EXE");
        if (!string.IsNullOrWhiteSpace(configuredPath) && File.Exists(configuredPath))
            return configuredPath;

        foreach (var pathEntry in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            var candidate = Path.Combine(pathEntry.Trim('"'), "ffmpeg.exe");
            if (File.Exists(candidate))
                return candidate;
        }

        var wingetPackages = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Microsoft",
            "WinGet",
            "Packages");
        if (Directory.Exists(wingetPackages))
        {
            foreach (var packageDirectory in Directory.EnumerateDirectories(wingetPackages, "Gyan.FFmpeg.Shared*"))
            foreach (var versionDirectory in Directory.EnumerateDirectories(packageDirectory, "ffmpeg-*"))
            {
                var candidate = Path.Combine(versionDirectory, "bin", "ffmpeg.exe");
                if (File.Exists(candidate))
                    return candidate;
            }
        }

        return null;
    }

    private static int GetAvailablePort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    private static async Task<TcpClient?> ConnectToVlcAsync(int port, CancellationToken cancellationToken)
    {
        for (var attempt = 0; attempt < 25; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var client = new TcpClient();
            try
            {
                await client.ConnectAsync(IPAddress.Loopback, port, cancellationToken);
                return client;
            }
            catch (SocketException)
            {
                client.Dispose();
                await Task.Delay(200, cancellationToken);
            }
        }

        return null;
    }

    private async Task CaptureFramesAsync(Process process, CancellationToken cancellationToken)
    {
        var input = new byte[32 * 1024];
        using var frame = new MemoryStream();
        var collecting = false;
        var previousByte = -1;
        try
        {
            while (true)
            {
                var count = await process.StandardOutput.BaseStream.ReadAsync(input, cancellationToken);
                if (count == 0)
                    break;

                for (var index = 0; index < count; index++)
                {
                    var currentByte = input[index];
                    if (!collecting)
                    {
                        if (previousByte == 0xff && currentByte == 0xd8)
                        {
                            frame.SetLength(0);
                            frame.WriteByte(0xff);
                            frame.WriteByte(0xd8);
                            collecting = true;
                        }
                    }
                    else
                    {
                        frame.WriteByte(currentByte);
                        if (frame.Length > 5_000_000)
                        {
                            frame.SetLength(0);
                            collecting = false;
                        }
                        else if (previousByte == 0xff && currentByte == 0xd9)
                        {
                            if (ReferenceEquals(_ffmpeg, process))
                            {
                                Volatile.Write(ref _latestFrame, frame.ToArray());
                                Interlocked.Exchange(ref _lastFrameAt, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                            }
                            frame.SetLength(0);
                            collecting = false;
                        }
                    }

                    previousByte = currentByte;
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception error) when (error is IOException or InvalidOperationException)
        {
        }
    }

    private void StopProcesses()
    {
        _captureCancellation?.Cancel();
        StopProcess(_ffmpeg);
        StopProcess(_vlc);
        _captureCancellation?.Dispose();
        _captureCancellation = null;
        _captureTask = null;
        Volatile.Write(ref _latestFrame, null);
        Interlocked.Exchange(ref _lastFrameAt, 0);
        _ffmpeg = null;
        _vlc = null;
        _streamPort = 0;
    }

    private static void StopProcess(Process? process)
    {
        if (process is null)
            return;
        try
        {
            if (!process.HasExited)
                process.Kill(entireProcessTree: true);
            process.Dispose();
        }
        catch (Exception error) when (error is InvalidOperationException or Win32Exception)
        {
        }
    }

    public void Dispose()
    {
        StopProcesses();
        _gate.Dispose();
    }
}
