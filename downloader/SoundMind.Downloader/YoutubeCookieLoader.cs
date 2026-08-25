using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;

internal static class YoutubeCookieLoader
{
    public static IReadOnlyList<Cookie> LoadFromEnvironment()
    {
        var filePath =
            FirstNonEmpty(
                Environment.GetEnvironmentVariable("YOUTUBE_COOKIES_FILE"),
                Environment.GetEnvironmentVariable("YTDLP_COOKIES_FILE")
            ) ?? "";

        if (!string.IsNullOrWhiteSpace(filePath) && File.Exists(filePath))
            return ParseNetscapeCookieFile(File.ReadAllText(filePath));

        var b64 =
            FirstNonEmpty(
                Environment.GetEnvironmentVariable("YOUTUBE_COOKIES_B64"),
                Environment.GetEnvironmentVariable("YTDLP_COOKIES_B64")
            ) ?? "";

        if (string.IsNullOrWhiteSpace(b64))
            return [];

        try
        {
            var text = Encoding.UTF8.GetString(Convert.FromBase64String(b64.Trim()));
            return ParseNetscapeCookieFile(text);
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Failed to decode YouTube cookies from env: {exception.Message}");
            return [];
        }
    }

    private static string? FirstNonEmpty(params string?[] values)
    {
        foreach (var value in values)
        {
            if (!string.IsNullOrWhiteSpace(value))
                return value.Trim();
        }

        return null;
    }

    private static IReadOnlyList<Cookie> ParseNetscapeCookieFile(string content)
    {
        var cookies = new List<Cookie>();
        using var reader = new StringReader(content);
        while (reader.ReadLine() is { } line)
        {
            line = line.Trim();
            if (line.Length == 0 || (line.StartsWith('#') && !line.StartsWith("#HttpOnly_", StringComparison.Ordinal)))
                continue;

            var httpOnly = false;
            if (line.StartsWith("#HttpOnly_", StringComparison.Ordinal))
            {
                httpOnly = true;
                line = line["#HttpOnly_".Length..];
            }

            var parts = line.Split('\t');
            if (parts.Length < 7)
                continue;

            var domain = parts[0].Trim();
            var path = parts[2].Trim();
            var secure = parts[3].Trim().Equals("TRUE", StringComparison.OrdinalIgnoreCase);
            var name = parts[5].Trim();
            var value = parts[6].Trim();
            if (string.IsNullOrWhiteSpace(name))
                continue;

            try
            {
                var cookie = new Cookie(name, value, string.IsNullOrWhiteSpace(path) ? "/" : path, domain)
                {
                    Secure = secure,
                    HttpOnly = httpOnly,
                };
                cookies.Add(cookie);
            }
            catch
            {
                // Skip malformed cookie rows rather than failing the whole download.
            }
        }

        return cookies;
    }
}
