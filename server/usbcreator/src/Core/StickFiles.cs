using System.Text.Json;

namespace Netdispatch.UsbCreator.Core;

/// <summary>The stick's two partitions, as directories (on Windows, <c>\\?\Volume{...}\</c> paths).</summary>
public sealed record StickRoots(string Boot, string Data)
{
    public const string BootPart = "boot";
    public const string DataPart = "data";
    public const string BootLabel = "NDBOOT";
    public const string DataLabel = "NDDATA";

    public string Root(string part) => part switch
    {
        BootPart => Boot,
        DataPart => Data,
        _ => throw new HubException($"unknown stick partition '{part}'"),
    };

    /// <summary>A hub-supplied path (forward slashes, relative) below one partition. Refuses
    /// anything that would leave it.</summary>
    public string Resolve(string part, string relative)
    {
        var segments = relative.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (segments.Length == 0 || relative.StartsWith('/') || relative.Contains('\\', StringComparison.Ordinal)
            || segments.Any(s => s is "." or ".." || s.Contains(':', StringComparison.Ordinal)))
        {
            throw new HubException($"refusing the path '{relative}'");
        }
        return Path.Combine([Root(part), .. segments]);
    }
}

/// <summary>What the writer last put on a stick: <c>netdispatch/state.json</c> on NDDATA.</summary>
public sealed record StickState(string StickId, string? HubUrl, string? BootFiles, Dictionary<int, string> Images)
{
    public const string PathOnData = "netdispatch/state.json";
    public const string KeyOnData = "netdispatch/stick.key";
    public const string InfoOnData = "netdispatch/stick.json";

    public static StickState? Load(StickRoots roots)
    {
        var path = roots.Resolve(StickRoots.DataPart, PathOnData);
        if (!File.Exists(path))
        {
            return null;
        }
        try
        {
            return JsonSerializer.Deserialize<StickState>(File.ReadAllText(path), HubJson.Options);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public void Save(StickRoots roots) =>
        AtomicFile.WriteAllText(roots.Resolve(StickRoots.DataPart, PathOnData), JsonSerializer.Serialize(this, HubJson.Options));

    /// <summary>The stick id from <c>netdispatch/stick.json</c>, which the hub renders: present on any stick.</summary>
    public static string? ReadStickId(string dataRoot)
    {
        var path = Path.Combine(dataRoot, "netdispatch", "stick.json");
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            return doc.RootElement.TryGetProperty("id", out var id) ? id.GetString() : null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException)
        {
            return null;
        }
    }
}

internal static class AtomicFile
{
    /// <summary>Write next to the target, then rename over it. Keeps the bytes as given (CRLF stays CRLF).</summary>
    public static void WriteAllText(string path, string content)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var tmp = path + ".nd-new";
        File.WriteAllText(tmp, content, new System.Text.UTF8Encoding(false));
        File.Move(tmp, path, overwrite: true);
    }
}

public static class StickSizing
{
    public const long MiB = 1024 * 1024;
    public const int MinBootMegabytes = 2048;

    /// <summary>The boot partition holds two copies of every image's boot files, so an
    /// update can sit beside the old copy, plus room for the boot files themselves.</summary>
    public static int BootMegabytes(long bootBytes) =>
        (int)Math.Max(MinBootMegabytes, (2 * bootBytes + 512 * MiB + MiB - 1) / MiB);

    /// <summary>Disk bytes a stick needs: the data partition with ~3% filesystem slack and
    /// 256 MiB to spare, the boot partition, and the 1 MiB before the first partition.</summary>
    public static long RequiredBytes(long dataBytes, int bootMegabytes) =>
        dataBytes + dataBytes / 32 + 256 * MiB + bootMegabytes * MiB + MiB;
}
