using System.Text.Json;
using System.Text.Json.Serialization;

namespace Netdispatch.UsbCreator.Core;

/// <summary>The hub's JSON documents (server/app/routers/usb_sticks.py), snake_case on the wire.</summary>
public static class HubJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };
}

public sealed record PairStartResponse(string Code, string PollToken, int ExpiresIn);

public sealed record PairPollResponse(string Status, int? CreatorId, string? Name, string? CreatorKey);

public sealed record ProfileEntry(
    int Id,
    int IsoId,
    string IsoName,
    string IsoStatus,
    string BootMethod,
    int? UnattendId,
    string? UnattendName,
    string Label,
    long? BootBytes,
    long? DataBytes,
    string? Error);

public sealed record Profile(int Id, string Name, string? Description, List<ProfileEntry> Entries, int Sticks)
{
    /// <summary>Bytes of the boot-partition files of every image, counted once per image.</summary>
    public long BootBytes => Entries.Where(e => e.Error is null).DistinctBy(e => e.IsoId).Sum(e => e.BootBytes ?? 0);

    public long DataBytes => Entries.Where(e => e.Error is null).DistinctBy(e => e.IsoId).Sum(e => e.DataBytes ?? 0);
}

public sealed record ProfilesResponse(string HubUrl, List<Profile> Profiles);

public sealed record StickInfo(string Id, string Name, int? ProfileId, string? ProfileName, string? CreatorName);

public sealed record CreatedStick(StickInfo Stick, string StickKey, string HubUrl);

/// <summary>One file of an image on the stick. <c>Src</c> and <c>Cache</c> are URL paths, already encoded.</summary>
public sealed record ManifestFile(string Part, string Dest, string Src, string Cache, long Size, long Mtime);

public sealed record ManifestImage(
    int IsoId,
    string Name,
    string? BootMethod,
    string? Etag,
    long BootBytes,
    long DataBytes,
    string? CacheUrl,
    List<ManifestFile>? Files,
    bool Keep,
    string? Error)
{
    public bool Copyable => Error is null && !Keep && Files is { Count: > 0 } && Etag is not null;
}

public sealed record Manifest(StickInfo Stick, string HubUrl, string BootFiles, List<ManifestImage> Images);

public sealed record LayoutFile(string Part, string Path, string Content);

public sealed record LayoutDir(string Part, string Path);

public sealed record Layout(List<LayoutFile> Files, List<LayoutDir> ReplaceDirs, List<string> Problems);

public sealed record ReportItem(int IsoId, string? Etag, string Status, long BytesDone, long BytesTotal, string? Message);

/// <summary>A stick's boot partition template: the gzipped head, to write at the partition start.</summary>
public sealed record BootTemplate(byte[] Gzipped, long HeadBytes, string Version, bool Bios);
