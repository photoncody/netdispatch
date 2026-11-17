using System.IO.Compression;
using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Windows;

/// <summary>The two jobs USB Creator does, shared by the window and the command line.</summary>
public sealed class StickWriter(HubClient hub, string creatorKey, IProgress<string>? log, IProgress<SyncProgress>? progress)
{
    private HubAuth Auth => new(CreatorKey: creatorKey);

    /// <summary>Erase <paramref name="disk"/> and make a stick of <paramref name="profile"/>.</summary>
    public async Task<SyncResult> CreateAsync(UsbDisk disk, Profile profile, string name, bool copyImages, CancellationToken ct)
    {
        var bootMb = StickSizing.BootMegabytes(copyImages ? profile.BootBytes : 0);
        var needed = StickSizing.RequiredBytes(copyImages ? profile.DataBytes : 0, bootMb);
        if (disk.Size < needed)
        {
            throw new IOException($"{disk.Display} is too small: this profile needs {needed / 1_000_000_000.0:0.#} GB");
        }
        var mbr = await hub.GetMbrAsync(Auth, ct).ConfigureAwait(false);
        log?.Report($"Registering the stick \"{name}\" with the hub");
        var created = await hub.CreateStickAsync(Auth, name, profile.Id, ct).ConfigureAwait(false);
        var roots = await WindowsDisks.PrepareAsync(disk.Number, bootMb, mbr, Template, log, ct).ConfigureAwait(false);
        // The stick's own key, for its in-boot updater.
        var keyPath = roots.Resolve(StickRoots.DataPart, StickState.KeyOnData);
        Directory.CreateDirectory(Path.GetDirectoryName(keyPath)!);
        File.WriteAllText(keyPath, created.StickKey + "\n");
        return await SyncAsync(roots, created.Stick.Id, copyImages, ct).ConfigureAwait(false);
    }

    /// <summary>Bring an existing stick up to date with its profile.</summary>
    public async Task<SyncResult> UpdateAsync(UsbDisk disk, bool copyImages, CancellationToken ct)
    {
        var roots = disk.Roots ?? throw new IOException($"{disk.Display} is not a netdispatch stick");
        var stickId = StickState.ReadStickId(roots.Data) ?? throw new IOException("the stick has no netdispatch\\stick.json");
        var manifest = await hub.GetManifestAsync(Auth, stickId, ct).ConfigureAwait(false);
        if (SyncEngine.BootFilesStale(roots, manifest))
        {
            SyncEngine.ForgetImages(roots);
            await WindowsDisks.RewriteBootAsync(disk.Number, Template, log, ct).ConfigureAwait(false);
        }
        return await SyncAsync(roots, stickId, copyImages, ct, manifest).ConfigureAwait(false);
    }

    private async Task<SyncResult> SyncAsync(StickRoots roots, string stickId, bool copyImages, CancellationToken ct, Manifest? manifest = null)
    {
        manifest ??= await hub.GetManifestAsync(Auth, stickId, ct).ConfigureAwait(false);
        log?.Report(copyImages ? "Copying the images" : "Writing the boot scripts");
        return await new SyncEngine(hub, Auth, roots, progress).SyncAsync(manifest, copyImages, ct).ConfigureAwait(false);
    }

    private async Task<byte[]> Template(int sizeMb, long offset)
    {
        var t = await hub.GetBootTemplateAsync(Auth, sizeMb, offset).ConfigureAwait(false);
        if (!t.Bios)
        {
            log?.Report("Note: this hub makes UEFI-only sticks (no SYSLINUX on its CPU architecture)");
        }
        using var gz = new GZipStream(new MemoryStream(t.Gzipped), CompressionMode.Decompress);
        using var head = new MemoryStream();
        await gz.CopyToAsync(head).ConfigureAwait(false);
        if (head.Length != t.HeadBytes)
        {
            throw new HubException($"boot template: got {head.Length} bytes, expected {t.HeadBytes}");
        }
        return head.ToArray();
    }
}
