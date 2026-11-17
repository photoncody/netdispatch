namespace Netdispatch.UsbCreator.Core;

public sealed record SyncProgress(string Stage, string? Image, string? File, long BytesDone, long BytesTotal);

public sealed record SyncResult(
    IReadOnlyDictionary<int, string> Have,
    IReadOnlyList<string> Problems,
    long BytesDownloaded);

/// <summary>Brings a stick's files in line with the hub's manifest: the copy engine shared by
/// "make a stick" and "update a stick". Partitioning and the boot template are the disk
/// backend's job; this only works on the two partitions' directories.
///
/// <list type="number">
/// <item>Images being changed are dropped from the stick's menu first (a layout without
/// them), so an interrupted update never offers a half-copied image.</item>
/// <item>Every file is checked by size and mtime and downloaded only when it differs, to
/// <c>&lt;file&gt;.nd-&lt;size&gt;-&lt;mtime&gt;.part</c> with HTTP range resume, then renamed.</item>
/// <item>Files the manifest no longer lists, and images no longer in the profile, are deleted.</item>
/// <item>The final layout and <c>state.json</c> are written, and the result reported.</item>
/// </list>
/// </summary>
public sealed class SyncEngine
{
    // FAT32 keeps modification times to 2 seconds.
    private const long MtimeSlackSeconds = 2;
    private readonly HubClient _hub;
    private readonly HubAuth _auth;
    private readonly StickRoots _roots;
    private readonly IProgress<SyncProgress>? _progress;

    public SyncEngine(HubClient hub, HubAuth auth, StickRoots roots, IProgress<SyncProgress>? progress = null)
    {
        _hub = hub;
        _auth = auth;
        _roots = roots;
        _progress = progress;
    }

    /// <param name="copyImages">False for an online-only stick: images are removed, not copied.</param>
    public async Task<SyncResult> SyncAsync(Manifest manifest, bool copyImages, CancellationToken ct = default)
    {
        var stickId = manifest.Stick.Id;
        var state = StickState.Load(_roots) is { } s && s.StickId == stickId
            ? s
            : new StickState(stickId, _hub.BaseUrl.ToString(), null, []);
        var wanted = copyImages ? manifest.Images.Where(i => i.Copyable).ToList() : [];
        var kept = copyImages ? manifest.Images.Where(i => i.Keep).Select(i => i.IsoId).ToHashSet() : [];
        var problems = manifest.Images.Where(i => copyImages && i.Error is not null)
            .Select(i => $"{i.Name}: {i.Error}").ToList();

        // 0. NDBOOT's times as this OS sees them: see BootClockShift.
        var shift = BootClockShift();
        if (shift != 0)
        {
            NormaliseBootTimes(wanted, shift);
        }
        WriteClockProbe();

        // 1. Images that will change leave the menu until they are complete again.
        var have = state.Images
            .Where(kv => kept.Contains(kv.Key) || wanted.Any(w => w.IsoId == kv.Key && w.Etag == kv.Value))
            .ToDictionary();
        await WriteLayoutAsync(stickId, have, copyImages, problems, ct).ConfigureAwait(false);
        SaveState(state with { Images = have, HubUrl = _hub.BaseUrl.ToString() });

        // 2. Copy.
        var total = wanted.Sum(i => i.Files!.Sum(f => f.Size));
        long done = 0, downloaded = 0;
        var report = new List<ReportItem>();
        foreach (var image in wanted)
        {
            long imageBytes = image.Files!.Sum(f => f.Size), imageDone = 0;
            try
            {
                foreach (var file in image.Files!)
                {
                    ct.ThrowIfCancellationRequested();
                    var dest = _roots.Resolve(file.Part, file.Dest);
                    if (!IsCurrent(dest, file))
                    {
                        var before = done;
                        downloaded += await DownloadAsync(image, file, dest, b => Report("copy", image.Name, file.Dest, before + b, total), ct)
                            .ConfigureAwait(false);
                    }
                    done += file.Size;
                    imageDone += file.Size;
                    Report("copy", image.Name, file.Dest, done, total);
                }
                RemoveStale(image);
                have[image.IsoId] = image.Etag!;
                SaveState(state with { Images = have });
                report.Add(new ReportItem(image.IsoId, image.Etag, "ready", imageBytes, imageBytes, null));
            }
            catch (Exception e) when (e is HubException or HttpRequestException or IOException)
            {
                problems.Add($"{image.Name}: {e.Message}");
                report.Add(new ReportItem(image.IsoId, null, "error", imageDone, imageBytes, Trim(e.Message)));
                done += imageBytes - imageDone;
            }
        }

        // 3. Images no longer on the profile (or an online-only stick).
        var keepIds = wanted.Select(i => i.IsoId).Concat(kept).ToHashSet();
        RemoveImagesExcept(keepIds);
        foreach (var id in have.Keys.Where(id => !keepIds.Contains(id)).ToList())
        {
            have.Remove(id);
        }
        foreach (var id in kept.Where(have.ContainsKey))
        {
            report.Add(new ReportItem(id, have[id], "ready", 0, 0, "kept while the hub prepares a new version"));
        }

        // 4. The final menu and state.
        Report("layout", null, null, done, total);
        await WriteLayoutAsync(stickId, have, copyImages, problems, ct).ConfigureAwait(false);
        SaveState(state with { Images = have, BootFiles = manifest.BootFiles, HubUrl = _hub.BaseUrl.ToString() });
        await _hub.ReportAsync(_auth, stickId, report, ct).ConfigureAwait(false);
        Report("done", null, null, total, total);
        return new SyncResult(have, problems, downloaded);
    }

    /// <summary>The boot-partition template has to be rewritten when it differs from what
    /// the stick has (the hub's iPXE builds or wimboot changed).</summary>
    public static bool BootFilesStale(StickRoots roots, Manifest manifest) =>
        StickState.Load(roots)?.BootFiles != manifest.BootFiles;

    /// <summary>Call before the boot partition is rewritten: every image's boot files go with
    /// it, so no image counts as on the stick until a sync has copied its boot files again.
    /// An image that fails, or that the hub is still preparing, then stays off the menu.</summary>
    public static void ForgetImages(StickRoots roots)
    {
        if (StickState.Load(roots) is { } state)
        {
            (state with { Images = [] }).Save(roots);
        }
    }

    private void SaveState(StickState state) => state.Save(_roots);

    private async Task WriteLayoutAsync(string stickId, Dictionary<int, string> have, bool images, List<string> problems, CancellationToken ct)
    {
        var layout = await _hub.GetLayoutAsync(_auth, stickId, have, images, ct).ConfigureAwait(false);
        foreach (var dir in layout.ReplaceDirs)
        {
            var path = _roots.Resolve(dir.Part, dir.Path);
            if (Directory.Exists(path))
            {
                Directory.Delete(path, recursive: true);
            }
        }
        foreach (var file in layout.Files)
        {
            AtomicFile.WriteAllText(_roots.Resolve(file.Part, file.Path), file.Content);
        }
        foreach (var p in layout.Problems.Where(p => !problems.Contains(p)))
        {
            problems.Add(p);
        }
    }

    private static bool IsCurrent(string path, ManifestFile file)
    {
        var info = new FileInfo(path);
        if (!info.Exists || info.Length != file.Size)
        {
            return false;
        }
        var mtime = new DateTimeOffset(info.LastWriteTimeUtc).ToUnixTimeSeconds();
        return Math.Abs(mtime - file.Mtime) <= MtimeSlackSeconds;
    }

    /// <summary>An empty file on NDBOOT whose time every sync sets to <see cref="ClockProbeTime"/>.</summary>
    internal const string ClockProbe = "netdispatch/clock.probe";
    internal const long ClockProbeTime = 1_600_000_000;

    /// <summary>How far NDBOOT's times are off as this OS reads them. FAT keeps local time
    /// without a zone: a stick last written on Windows in UTC-5 and read by the Linux updater
    /// (UTC) shows every time 5 hours early, and back. The probe the last writer left measures
    /// that one shift; files on exFAT (NDDATA) carry their zone and are never shifted.</summary>
    private long BootClockShift()
    {
        var probe = new FileInfo(_roots.Resolve(StickRoots.BootPart, ClockProbe));
        if (!probe.Exists)
        {
            return 0;
        }
        var shift = new DateTimeOffset(probe.LastWriteTimeUtc).ToUnixTimeSeconds() - ClockProbeTime;
        return Math.Abs(shift) <= 14 * 3600 + MtimeSlackSeconds ? shift : 0;
    }

    /// <summary>Give NDBOOT files that are current except for the measured shift their
    /// hub time as this OS writes it, so the stick is consistent with the new probe.</summary>
    private void NormaliseBootTimes(IEnumerable<ManifestImage> images, long shift)
    {
        foreach (var file in images.SelectMany(i => i.Files!).Where(f => f.Part == StickRoots.BootPart))
        {
            var info = new FileInfo(_roots.Resolve(file.Part, file.Dest));
            if (info.Exists && info.Length == file.Size
                && Math.Abs(new DateTimeOffset(info.LastWriteTimeUtc).ToUnixTimeSeconds() - shift - file.Mtime) <= MtimeSlackSeconds)
            {
                File.SetLastWriteTimeUtc(info.FullName, DateTimeOffset.FromUnixTimeSeconds(file.Mtime).UtcDateTime);
            }
        }
    }

    private void WriteClockProbe()
    {
        var path = _roots.Resolve(StickRoots.BootPart, ClockProbe);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        if (!File.Exists(path))
        {
            File.WriteAllBytes(path, []);
        }
        File.SetLastWriteTimeUtc(path, DateTimeOffset.FromUnixTimeSeconds(ClockProbeTime).UtcDateTime);
    }

    private async Task<long> DownloadAsync(ManifestImage image, ManifestFile file, string dest, Action<long> progress, CancellationToken ct)
    {
        var dir = Path.GetDirectoryName(dest)!;
        Directory.CreateDirectory(dir);
        var part = $"{dest}.nd-{file.Size}-{file.Mtime}.part";
        // A partial download of another version of this file never resumes into this one.
        foreach (var stale in Directory.EnumerateFiles(dir, Path.GetFileName(dest) + ".nd-*.part"))
        {
            if (!string.Equals(stale, part, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(stale);
            }
        }
        var offset = File.Exists(part) ? new FileInfo(part).Length : 0;
        if (offset > file.Size)
        {
            File.Delete(part);
            offset = 0;
        }
        long received = 0;
        if (offset < file.Size || file.Size == 0)
        {
            var (resp, resumed) = await _hub.GetFileAsync(_hub.FileUrl(image, file), offset, ct).ConfigureAwait(false);
            using (resp)
            {
                if (!resumed)
                {
                    offset = 0;
                }
                await using var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
                await using var dst = new FileStream(part, resumed ? FileMode.Append : FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20);
                var buffer = new byte[1 << 20];
                int n;
                while ((n = await src.ReadAsync(buffer, ct).ConfigureAwait(false)) > 0)
                {
                    await dst.WriteAsync(buffer.AsMemory(0, n), ct).ConfigureAwait(false);
                    received += n;
                    progress(offset + received);
                }
            }
        }
        var size = new FileInfo(part).Length;
        if (size != file.Size)
        {
            File.Delete(part);
            throw new HubException($"{file.Dest}: got {size} bytes, expected {file.Size}");
        }
        File.Move(part, dest, overwrite: true);
        File.SetLastWriteTimeUtc(dest, DateTimeOffset.FromUnixTimeSeconds(file.Mtime).UtcDateTime);
        return received;
    }

    /// <summary>Files below the image's own directories that the manifest doesn't list.</summary>
    private void RemoveStale(ManifestImage image)
    {
        var expected = image.Files!
            .Select(f => Path.GetFullPath(_roots.Resolve(f.Part, f.Dest)))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var dir in ImageDirs(image.IsoId))
        {
            if (!Directory.Exists(dir))
            {
                continue;
            }
            foreach (var path in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories))
            {
                if (!expected.Contains(Path.GetFullPath(path)))
                {
                    File.Delete(path);
                }
            }
            foreach (var sub in Directory.EnumerateDirectories(dir, "*", SearchOption.AllDirectories).OrderByDescending(d => d.Length))
            {
                if (!Directory.EnumerateFileSystemEntries(sub).Any())
                {
                    Directory.Delete(sub);
                }
            }
        }
    }

    private void RemoveImagesExcept(HashSet<int> keep)
    {
        foreach (var (part, parent) in new[] { (StickRoots.DataPart, "netdispatch/img"), (StickRoots.BootPart, "netdispatch/boot") })
        {
            var root = _roots.Resolve(part, parent);
            if (!Directory.Exists(root))
            {
                continue;
            }
            foreach (var dir in Directory.EnumerateDirectories(root))
            {
                if (!int.TryParse(Path.GetFileName(dir), out var id) || !keep.Contains(id))
                {
                    Directory.Delete(dir, recursive: true);
                }
            }
        }
    }

    private IEnumerable<string> ImageDirs(int isoId)
    {
        yield return _roots.Resolve(StickRoots.DataPart, $"netdispatch/img/{isoId}");
        yield return _roots.Resolve(StickRoots.BootPart, $"netdispatch/boot/{isoId}");
    }

    private void Report(string stage, string? image, string? file, long done, long total) =>
        _progress?.Report(new SyncProgress(stage, image, file, done, total));

    private static string Trim(string message) => message.Length <= 250 ? message : message[..250];
}
