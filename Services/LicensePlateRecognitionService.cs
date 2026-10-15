using System.Diagnostics;
using System.Globalization;
using System.Text.RegularExpressions;

namespace BridgeTrafficDemo.Services;

public sealed record LicensePlateRecognitionResult(
    bool Available,
    string? Plate,
    double Confidence,
    string Message,
    string? RawText = null);

public sealed class LicensePlateRecognitionService(LicensePlateDetectionService detector)
{
    private static readonly Regex VietnamesePlatePattern = new(
        @"(?<![A-Z0-9])\d{2}[A-Z]{1,2}\d{4,6}(?![A-Z0-9])",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public bool IsAvailable => FindTesseract() is not null && detector.IsAvailable;

    public async Task<LicensePlateRecognitionResult> RecognizeAsync(
        byte[] image,
        int imageWidth,
        int imageHeight,
        CancellationToken cancellationToken)
    {
        var executable = FindTesseract();
        if (executable is null || !detector.IsAvailable)
            return new LicensePlateRecognitionResult(false, null, 0, "Bộ nhận dạng YOLO/Tesseract chưa sẵn sàng trên máy này.");

        var bounds = await detector.DetectAsync(image, imageWidth, imageHeight, cancellationToken);
        if (bounds is null)
            return new LicensePlateRecognitionResult(true, null, 0, "Chưa tìm được vùng biển số trong khung hình.");

        var plateImage = await CropPlateAsync(image, imageWidth, imageHeight, bounds, cancellationToken);
        var results = new List<(string Text, double Confidence)>();
        foreach (var pageSegmentationMode in new[] { "6", "7" })
        {
            var result = await ReadPlateAsync(executable, plateImage, pageSegmentationMode, cancellationToken);
            if (!result.Available)
                return result;
            results.Add((result.Plate ?? "", result.Confidence));
        }

        foreach (var result in results)
        {
            var normalized = Regex.Replace((result.Text ?? "").ToUpperInvariant(), "[^A-Z0-9]", "");
            var match = VietnamesePlatePattern.Match(normalized);
            if (match.Success && result.Confidence >= 0.50)
                return new LicensePlateRecognitionResult(true, match.Value, result.Confidence, "Đã đọc được biển số.", result.Text);
        }

        return new LicensePlateRecognitionResult(
            true,
            null,
            0,
            "Đã tìm thấy biển số nhưng OCR chưa đọc đủ tin cậy.",
            string.Join(" | ", results.Select(result => result.Text).Where(text => !string.IsNullOrWhiteSpace(text))));
    }

    private static async Task<byte[]> CropPlateAsync(
        byte[] image,
        int imageWidth,
        int imageHeight,
        LicensePlateBounds bounds,
        CancellationToken cancellationToken)
    {
        var paddingX = Math.Max(4, (int)(bounds.Width * 0.15));
        var paddingY = Math.Max(4, (int)(bounds.Height * 0.25));
        var x = Math.Max(0, bounds.X - paddingX);
        var y = Math.Max(0, bounds.Y - paddingY);
        var right = Math.Min(imageWidth, bounds.X + bounds.Width + paddingX);
        var bottom = Math.Min(imageHeight, bounds.Y + bounds.Height + paddingY);
        x -= x % 2;
        y -= y % 2;
        var width = Math.Max(2, right - x);
        var height = Math.Max(2, bottom - y);
        width -= width % 2;
        height -= height % 2;
        var cropWidth = Math.Max(2, Math.Min(400, (int)Math.Round((double)width * 600 / height)));
        var cropHeight = Math.Clamp((int)Math.Round((double)height * cropWidth / width), 2, 600);

        var ffmpegPath = LicensePlateDetectionService.FindFFmpegPath()
            ?? throw new InvalidOperationException("FFmpeg is required to crop the detected plate.");
        var startInfo = new ProcessStartInfo(ffmpegPath)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in new[]
        {
            "-hide_banner",
            "-loglevel", "error",
            "-f", "image2pipe",
            "-i", "pipe:0",
            "-vf", $"crop={width}:{height}:{x}:{y},scale={cropWidth}:{cropHeight}",
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "pipe:1"
        })
            startInfo.ArgumentList.Add(argument);

        using var process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Could not start local FFmpeg to crop a detected plate.");
        using var output = new MemoryStream();
        var outputTask = process.StandardOutput.BaseStream.CopyToAsync(output, cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.StandardInput.BaseStream.WriteAsync(image, cancellationToken);
        process.StandardInput.Close();
        await process.WaitForExitAsync(cancellationToken);
        await outputTask;
        var error = await errorTask;
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"FFmpeg could not crop the detected plate: {error.Trim()}");
        return PlateImagePreprocessor.Prepare(output.ToArray(), cropWidth, cropHeight);
    }

    private static async Task<LicensePlateRecognitionResult> ReadPlateAsync(
        string executable,
        byte[] image,
        string pageSegmentationMode,
        CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        foreach (var argument in new[]
        {
            "stdin", "stdout", "--psm", pageSegmentationMode, "--dpi", "300", "-c",
            "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-", "tsv"
        })
            startInfo.ArgumentList.Add(argument);

        using var process = Process.Start(startInfo);
        if (process is null)
            return new LicensePlateRecognitionResult(false, null, 0, "Không khởi động được Tesseract cục bộ.");

        var outputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        await process.StandardInput.BaseStream.WriteAsync(image, cancellationToken);
        process.StandardInput.Close();
        await process.WaitForExitAsync(cancellationToken);
        var output = await outputTask;
        _ = await errorTask;

        if (process.ExitCode != 0)
            return new LicensePlateRecognitionResult(true, null, 0, "OCR không đọc được vùng biển số.");

        var recognizedWords = new List<(string Text, double Confidence)>();
        foreach (var line in output.Split('\n').Skip(1))
        {
            var columns = line.TrimEnd('\r').Split('\t');
            if (columns.Length < 12
                || !double.TryParse(columns[10], NumberStyles.Float, CultureInfo.InvariantCulture, out var confidence)
                || confidence < 0)
                continue;

            var text = columns[11].Trim();
            if (Regex.IsMatch(text, "[A-Z0-9]", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
                recognizedWords.Add((text, confidence));
        }

        var combined = string.Join(" ", recognizedWords.Select(word => word.Text));
        if (recognizedWords.Count == 0)
            return new LicensePlateRecognitionResult(true, null, 0, "Chưa đọc biển số.");

        var averageConfidence = recognizedWords.Average(word => word.Confidence) / 100;
        return new LicensePlateRecognitionResult(true, combined, averageConfidence, "OCR hoàn tất.");
    }

    private static string? FindTesseract()
    {
        var configuredPath = Environment.GetEnvironmentVariable("TESSERACT_EXE");
        if (!string.IsNullOrWhiteSpace(configuredPath) && File.Exists(configuredPath))
            return configuredPath;

        var standardPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "Tesseract-OCR",
            "tesseract.exe");
        if (File.Exists(standardPath))
            return standardPath;

        var localPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs",
            "Tesseract-OCR",
            "tesseract.exe");
        return File.Exists(localPath) ? localPath : null;
    }
}
