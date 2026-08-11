using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using YoutubeDownloader.Core.Downloading;
using YoutubeDownloader.Core.Resolving;
using YoutubeExplode;
using YoutubeExplode.Common;
using YoutubeExplode.Videos;
using YoutubeExplode.Videos.Streams;

if (args.Length == 2 && args[0].Equals("search", StringComparison.OrdinalIgnoreCase))
{
    try
    {
        using var resolver = new QueryResolver();
        var search = await resolver.ResolveAsync($"?{args[1]}");
        var results = search.Videos.Take(5).Select(video => new
        {
            title = video.Title,
            author = video.Author.ChannelTitle,
            url = video.Url,
            durationSeconds = video.Duration?.TotalSeconds,
            thumbnail = video.Thumbnails.TryGetWithHighestResolution()?.Url,
        });

        Console.WriteLine(JsonSerializer.Serialize(results));
        return 0;
    }
    catch (Exception exception)
    {
        Console.Error.WriteLine(exception.Message);
        return 1;
    }
}

if (args.Length != 2)
{
    Console.Error.WriteLine(
        "Usage: SoundMind.Downloader <youtube-url> <output-file.mp4|output-file.mp3> | search <song-name>"
    );
    return 1;
}

if (VideoId.TryParse(args[0]) is not { } videoId)
{
    Console.Error.WriteLine("The supplied value is not a valid YouTube video URL or ID.");
    return 1;
}

var outputPath = Path.GetFullPath(args[1]);
var outputExtension = Path.GetExtension(outputPath).ToLowerInvariant();
if (outputExtension is not ".mp4" and not ".mp3")
{
    Console.Error.WriteLine("The output file must use either the .mp4 or .mp3 extension.");
    return 1;
}

try
{
    var ffmpegPath = FFmpeg.TryGetCliFilePath();
    if (ffmpegPath is null)
    {
        ffmpegPath = Path.Combine(AppContext.BaseDirectory, FFmpeg.CliFileName);
        Console.WriteLine("FFmpeg was not found; downloading it for the local downloader...");
        await FFmpeg.DownloadAsync(ffmpegPath);
    }

    var outputDirectory = Path.GetDirectoryName(outputPath);
    if (!string.IsNullOrWhiteSpace(outputDirectory))
        Directory.CreateDirectory(outputDirectory);

    var youtube = new YoutubeClient();
    var video = await youtube.Videos.GetAsync(videoId);

    using var downloader = new VideoDownloader();
    var preference = outputExtension == ".mp3"
        ? new VideoDownloadPreference(Container.Mp3, VideoQualityPreference.Lowest)
        : new VideoDownloadPreference(Container.Mp4, VideoQualityPreference.Lowest);

    var option = await downloader.GetBestDownloadOptionAsync(video.Id, preference);

    await downloader.DownloadVideoAsync(
        outputPath,
        video,
        option,
        includeSubtitles: false,
        ffmpegPath: ffmpegPath
    );

    Console.WriteLine(outputExtension == ".mp3"
        ? $"Downloaded audio-only MP3 to {outputPath}"
        : $"Downloaded lowest-quality MP4 to {outputPath}");
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception.Message);
    return 1;
}