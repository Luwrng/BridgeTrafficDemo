using System.Text;

namespace BridgeTrafficDemo.Services;

internal static class PlateImagePreprocessor
{
    private readonly record struct Point(double X, double Y);

    // Rectify the bright plate surface inside the YOLO crop, while keeping the original crop as fallback.
    public static byte[] Prepare(byte[] rgb, int width, int height)
    {
        if (rgb.Length != width * height * 3)
            throw new InvalidDataException("Incomplete RGB plate crop.");

        var corners = FindPlateCorners(rgb, width, height);
        var (topLeft, topRight, bottomRight, bottomLeft) = corners
            ?? (new Point(0, 0), new Point(width - 1, 0), new Point(width - 1, height - 1), new Point(0, height - 1));
        var plateWidth = (Distance(topLeft, topRight) + Distance(bottomLeft, bottomRight)) / 2;
        var plateHeight = (Distance(topLeft, bottomLeft) + Distance(topRight, bottomRight)) / 2;
        const int outputWidth = 360;
        var outputHeight = Math.Clamp((int)Math.Round(outputWidth * plateHeight / Math.Max(1, plateWidth)), 60, 360);
        const int border = 10;
        var rasterWidth = outputWidth + border * 2;
        var rasterHeight = outputHeight + border * 2;
        var pixels = new byte[rasterWidth * rasterHeight];
        Array.Fill(pixels, (byte)255);
        var minimum = 255;
        var maximum = 0;
        for (var y = 0; y < outputHeight; y++)
        for (var x = 0; x < outputWidth; x++)
        {
            var u = (double)x / (outputWidth - 1);
            var v = (double)y / (outputHeight - 1);
            var sourceX = topLeft.X * (1 - u) * (1 - v) + topRight.X * u * (1 - v)
                + bottomLeft.X * (1 - u) * v + bottomRight.X * u * v;
            var sourceY = topLeft.Y * (1 - u) * (1 - v) + topRight.Y * u * (1 - v)
                + bottomLeft.Y * (1 - u) * v + bottomRight.Y * u * v;
            var gray = SampleGray(rgb, width, height, sourceX, sourceY);
            pixels[(y + border) * rasterWidth + x + border] = gray;
            minimum = Math.Min(minimum, gray);
            maximum = Math.Max(maximum, gray);
        }
        if (maximum - minimum > 30)
        {
            for (var y = border; y < rasterHeight - border; y++)
            for (var x = border; x < rasterWidth - border; x++)
            {
                var index = y * rasterWidth + x;
                pixels[index] = (byte)((pixels[index] - minimum) * 255 / (maximum - minimum));
            }
        }
        var header = Encoding.ASCII.GetBytes($"P5\n{rasterWidth} {rasterHeight}\n255\n");
        return [.. header, .. pixels];
    }

    private static (Point TopLeft, Point TopRight, Point BottomRight, Point BottomLeft)? FindPlateCorners(byte[] rgb, int width, int height)
    {
        foreach (var threshold in new[] { 170, 150, 130, 110 })
        {
            var mask = new bool[width * height];
            for (var index = 0; index < mask.Length; index++)
            {
                var r = rgb[index * 3]; var g = rgb[index * 3 + 1]; var b = rgb[index * 3 + 2];
                var low = Math.Min(r, Math.Min(g, b));
                var high = Math.Max(r, Math.Max(g, b));
                mask[index] = low > threshold && high - low < 60;
            }
            var largest = Array.Empty<int>();
            var pending = new Stack<int>();
            for (var index = 0; index < mask.Length; index++)
            {
                if (!mask[index]) continue;
                var component = new List<int>();
                mask[index] = false;
                pending.Push(index);
                while (pending.TryPop(out var current))
                {
                    component.Add(current);
                    var x = current % width; var y = current / width;
                    foreach (var neighbor in new[] { x > 0 ? current - 1 : -1, x < width - 1 ? current + 1 : -1,
                        y > 0 ? current - width : -1, y < height - 1 ? current + width : -1 })
                    {
                        if (neighbor < 0 || !mask[neighbor]) continue;
                        mask[neighbor] = false;
                        pending.Push(neighbor);
                    }
                }
                if (component.Count > largest.Length) largest = component.ToArray();
            }
            if (largest.Length < width * height * .08 || largest.Length > width * height * .9) continue;
            var points = largest.Select(index => new Point(index % width, index / width)).ToArray();
            var tl = points.MinBy(point => point.X + point.Y);
            var tr = points.MaxBy(point => point.X - point.Y);
            var br = points.MaxBy(point => point.X + point.Y);
            var bl = points.MinBy(point => point.X - point.Y);
            var surfaceWidth = (Distance(tl, tr) + Distance(bl, br)) / 2;
            var surfaceHeight = (Distance(tl, bl) + Distance(tr, br)) / 2;
            var aspect = surfaceWidth / Math.Max(1, surfaceHeight);
            if (aspect is >= 1.2 and <= 6.5 && surfaceWidth > 50 && surfaceHeight > 20
                && tl.X < tr.X && bl.X < br.X && tl.Y < bl.Y && tr.Y < br.Y)
                return (tl, tr, br, bl);
        }
        return null;
    }

    private static double Distance(Point first, Point second) =>
        Math.Sqrt(Math.Pow(first.X - second.X, 2) + Math.Pow(first.Y - second.Y, 2));

    private static byte SampleGray(byte[] rgb, int width, int height, double x, double y)
    {
        var left = Math.Clamp((int)x, 0, width - 1); var top = Math.Clamp((int)y, 0, height - 1);
        var right = Math.Min(left + 1, width - 1); var bottom = Math.Min(top + 1, height - 1);
        var dx = x - left; var dy = y - top;
        double Gray(int px, int py)
        {
            var index = (py * width + px) * 3;
            return (rgb[index] * 77 + rgb[index + 1] * 150 + rgb[index + 2] * 29) / 256.0;
        }
        return (byte)Math.Clamp(Gray(left, top) * (1 - dx) * (1 - dy) + Gray(right, top) * dx * (1 - dy)
            + Gray(left, bottom) * (1 - dx) * dy + Gray(right, bottom) * dx * dy, 0, 255);
    }
}
