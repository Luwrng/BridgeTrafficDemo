using System.Diagnostics;
using Microsoft.ML.OnnxRuntime;
using Microsoft.ML.OnnxRuntime.Tensors;

namespace BridgeTrafficDemo.Services;

public sealed record LicensePlateBounds(int X, int Y, int Width, int Height);

public sealed class LicensePlateDetectionService : IDisposable
{
    private const int ModelSize = 640;
    private readonly InferenceSession? _session;
    private readonly string? _ffmpegPath;

    public LicensePlateDetectionService()
    {
        _ffmpegPath = FindFFmpegPath();
        var modelPath = FindModel();
        if (modelPath is not null)
        {
            _session = new InferenceSession(modelPath, new Microsoft.ML.OnnxRuntime.SessionOptions
            {
                GraphOptimizationLevel = GraphOptimizationLevel.ORT_ENABLE_ALL,
                IntraOpNumThreads = 2
            });
        }
    }

    public bool IsAvailable => _session is not null && _ffmpegPath is not null;

    public async Task<LicensePlateBounds?> DetectAsync(
        byte[] image,
        int imageWidth,
        int imageHeight,
        CancellationToken cancellationToken)
    {
        if (!IsAvailable)
            return null;
        if (imageWidth <= 0 || imageHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(imageWidth), "Frame dimensions must be positive.");

        var rgb = await DecodeAsModelInputAsync(image, cancellationToken);
        var tensor = new DenseTensor<float>(new[] { 1, 3, ModelSize, ModelSize });
        for (var y = 0; y < ModelSize; y++)
        for (var x = 0; x < ModelSize; x++)
        {
            var pixel = (y * ModelSize + x) * 3;
            tensor[0, 0, y, x] = rgb[pixel] / 255f;
            tensor[0, 1, y, x] = rgb[pixel + 1] / 255f;
            tensor[0, 2, y, x] = rgb[pixel + 2] / 255f;
        }

        var inputName = _session!.InputMetadata.Keys.Single();
        using var outputs = _session.Run(new[]
        {
            NamedOnnxValue.CreateFromTensor(inputName, tensor)
        });
        var output = outputs.Single().AsTensor<float>();
        return FindBestPlate(output, imageWidth, imageHeight);
    }

    private async Task<byte[]> DecodeAsModelInputAsync(byte[] image, CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo(_ffmpegPath!)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in new[]
        {
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "image2pipe",
            "-i", "pipe:0",
            "-vf", "scale=640:640:force_original_aspect_ratio=decrease,pad=640:640:(ow-iw)/2:(oh-ih)/2:color=0x727272",
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "pipe:1"
        })
            startInfo.ArgumentList.Add(argument);

        using var process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Could not start local FFmpeg for plate detection.");
        var output = new MemoryStream(ModelSize * ModelSize * 3);
        var outputTask = process.StandardOutput.BaseStream.CopyToAsync(output, cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.StandardInput.BaseStream.WriteAsync(image, cancellationToken);
        process.StandardInput.Close();
        await process.WaitForExitAsync(cancellationToken);
        await outputTask;
        var error = await errorTask;

        if (process.ExitCode != 0)
            throw new InvalidOperationException($"FFmpeg could not decode a camera frame: {error.Trim()}");
        if (output.Length != ModelSize * ModelSize * 3)
            throw new InvalidDataException("FFmpeg returned an incomplete model input frame.");
        return output.ToArray();
    }

    private static LicensePlateBounds? FindBestPlate(Tensor<float> output, int imageWidth, int imageHeight)
    {
        var dimensions = output.Dimensions.ToArray();
        if (dimensions.Length != 3 || dimensions[0] != 1)
            throw new InvalidDataException("Unexpected YOLO plate detector output shape.");

        var channelsFirst = dimensions[1] < dimensions[2];
        var channelCount = dimensions[channelsFirst ? 1 : 2];
        var detectionCount = dimensions[channelsFirst ? 2 : 1];
        if (channelCount < 5)
            throw new InvalidDataException("YOLO plate detector output has too few channels.");

        var bestScore = 0.20f;
        var bestX = 0f;
        var bestY = 0f;
        var bestWidth = 0f;
        var bestHeight = 0f;
        for (var index = 0; index < detectionCount; index++)
        {
            var centerX = GetValue(output, channelsFirst, index, 0);
            var centerY = GetValue(output, channelsFirst, index, 1);
            var width = GetValue(output, channelsFirst, index, 2);
            var height = GetValue(output, channelsFirst, index, 3);
            var score = GetValue(output, channelsFirst, index, 4);
            if (score <= bestScore)
                continue;

            bestScore = score;
            bestX = centerX;
            bestY = centerY;
            bestWidth = width;
            bestHeight = height;
        }

        if (bestScore <= 0.20f || bestWidth <= 0 || bestHeight <= 0)
            return null;

        if (Math.Max(bestX, bestY) <= 1.5f)
        {
            bestX *= ModelSize;
            bestY *= ModelSize;
            bestWidth *= ModelSize;
            bestHeight *= ModelSize;
        }

        var scale = Math.Min((float)ModelSize / imageWidth, (float)ModelSize / imageHeight);
        var paddingX = (ModelSize - imageWidth * scale) / 2;
        var paddingY = (ModelSize - imageHeight * scale) / 2;
        var x1 = Math.Clamp((bestX - bestWidth / 2 - paddingX) / scale, 0, imageWidth);
        var y1 = Math.Clamp((bestY - bestHeight / 2 - paddingY) / scale, 0, imageHeight);
        var x2 = Math.Clamp((bestX + bestWidth / 2 - paddingX) / scale, 0, imageWidth);
        var y2 = Math.Clamp((bestY + bestHeight / 2 - paddingY) / scale, 0, imageHeight);
        return x2 > x1 && y2 > y1
            ? new LicensePlateBounds((int)x1, (int)y1, (int)(x2 - x1), (int)(y2 - y1))
            : null;
    }

    private static float GetValue(Tensor<float> output, bool channelsFirst, int detection, int channel) =>
        channelsFirst ? output[0, channel, detection] : output[0, detection, channel];

    private static string? FindModel()
    {
        var configuredPath = Environment.GetEnvironmentVariable("LICENSE_PLATE_MODEL");
        if (!string.IsNullOrWhiteSpace(configuredPath) && File.Exists(configuredPath))
            return configuredPath;

        var modelPath = Path.Combine(AppContext.BaseDirectory, "Models", "license-plate-yolov8n.onnx");
        return File.Exists(modelPath) ? modelPath : null;
    }

    public static string? FindFFmpegPath()
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

    public void Dispose() => _session?.Dispose();
}
