using System.Diagnostics;
using System.Globalization;
using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Updater;

/// <summary>Brings a netdispatch stick up to date from inside the updater's Linux image:
/// <code>
/// netdispatch-stick-update --hub URL --data /mnt/data --boot /mnt/boot --boot-device /dev/sdb2
/// </code>
/// The init script (usb/updater/init) has mounted NDDATA and NDBOOT. The stick's own key
/// (netdispatch/stick.key) authenticates it. When the hub's boot files changed, the boot
/// partition is rewritten from the hub's template first, as USB Creator does.
/// Exit codes: 0 up to date, 3 up to date except for the images listed, 1 failed.</summary>
internal static class Program
{
    private static readonly HttpClient Http = new(new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(20) })
    {
        Timeout = TimeSpan.FromMinutes(10),
    };

    public static async Task<int> Main(string[] args)
    {
        try
        {
            var opts = Parse(args);
            var roots = new StickRoots(Need(opts, "boot"), Need(opts, "data"));
            var stickId = StickState.ReadStickId(roots.Data) ?? throw new IOException("this stick has no netdispatch/stick.json");
            var keyPath = roots.Resolve(StickRoots.DataPart, StickState.KeyOnData);
            var key = File.Exists(keyPath) ? File.ReadAllText(keyPath).Trim() : throw new IOException("this stick has no key (netdispatch/stick.key): update it with USB Creator");
            var hub = new HubClient(Http, HubClient.ParseHubUrl(Need(opts, "hub")));
            var auth = new HubAuth(StickId: stickId, StickKey: key);
            using var cts = new CancellationTokenSource();
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };

            Console.WriteLine($"netdispatch :: updating stick {stickId} from {hub.BaseUrl}");
            var manifest = await hub.GetManifestAsync(auth, stickId, cts.Token).ConfigureAwait(false);
            if (SyncEngine.BootFilesStale(roots, manifest))
            {
                SyncEngine.ForgetImages(roots);
                await RewriteBootAsync(hub, auth, roots.Boot, Need(opts, "boot-device"), cts.Token).ConfigureAwait(false);
            }
            var last = "";
            var progress = new ConsoleProgress<SyncProgress>(p =>
            {
                // One line per percent: a serial console or a slow screen can't keep up with more.
                var copying = p.Stage == "copy" && p.BytesTotal > 0;
                var key = copying ? $"{p.Image}: {p.BytesDone * 100 / p.BytesTotal}%" : p.Stage;
                if (key != last)
                {
                    Console.WriteLine(copying ? $"  {key} ({p.BytesDone / 1_000_000} of {p.BytesTotal / 1_000_000} MB)" : $"  {key}");
                    last = key;
                }
            });
            var result = await new SyncEngine(hub, auth, roots, progress).SyncAsync(manifest, copyImages: true, cts.Token).ConfigureAwait(false);
            foreach (var problem in result.Problems)
            {
                Console.WriteLine("netdispatch :: warning: " + problem);
            }
            Console.WriteLine($"netdispatch :: done: {result.Have.Count} image(s) on the stick, {result.BytesDownloaded / 1_000_000} MB downloaded");
            return result.Problems.Count == 0 ? 0 : 3;
        }
        catch (Exception e) when (e is HubException or IOException or HttpRequestException or ArgumentException or UnauthorizedAccessException or OperationCanceledException)
        {
            Console.WriteLine("netdispatch :: update failed: " + e.Message);
            return 1;
        }
    }

    /// <summary>Write the hub's boot template over NDBOOT, as USB Creator's "Update stick" does.
    /// The template's head holds the FAT tables, so the old files beyond it are simply free space.</summary>
    private static async Task RewriteBootAsync(HubClient hub, HubAuth auth, string mountPoint, string device, CancellationToken ct)
    {
        var name = Path.GetFileName(device);
        var sectors = long.Parse(File.ReadAllText($"/sys/class/block/{name}/size").Trim(), CultureInfo.InvariantCulture);
        var start = long.Parse(File.ReadAllText($"/sys/class/block/{name}/start").Trim(), CultureInfo.InvariantCulture);
        var sizeMb = (int)(sectors * 512 / StickSizing.MiB);
        Console.WriteLine($"netdispatch :: the hub's boot files changed: rewriting the boot partition ({sizeMb} MiB)");
        var head = BootTemplates.Unpack(await hub.GetBootTemplateAsync(auth, sizeMb, start, ct).ConfigureAwait(false));
        await RunAsync("umount", mountPoint, ct).ConfigureAwait(false);
        await using (var disk = new FileStream(device, FileMode.Open, FileAccess.Write, FileShare.ReadWrite))
        {
            await disk.WriteAsync(head, ct).ConfigureAwait(false);
            disk.Flush(flushToDisk: true);
        }
        await RunAsync("mount", $"-t vfat -o {BootMountOptions} {device} {mountPoint}", ct).ConfigureAwait(false);
    }

    /// <summary>Same options as the init script's mount of NDBOOT.</summary>
    internal const string BootMountOptions = "rw,shortname=mixed,utf8";

    private static async Task RunAsync(string command, string arguments, CancellationToken ct)
    {
        using var proc = Process.Start(new ProcessStartInfo(command, arguments) { UseShellExecute = false })
            ?? throw new IOException($"could not run {command}");
        await proc.WaitForExitAsync(ct).ConfigureAwait(false);
        if (proc.ExitCode != 0)
        {
            throw new IOException($"{command} {arguments} failed (exit code {proc.ExitCode})");
        }
    }

    private static Dictionary<string, string> Parse(string[] args)
    {
        var opts = new Dictionary<string, string>();
        for (var i = 0; i < args.Length; i++)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal))
            {
                throw new ArgumentException($"unexpected '{args[i]}'");
            }
            opts[args[i][2..]] = i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal) ? args[++i] : "";
        }
        return opts;
    }

    private static string Need(Dictionary<string, string> opts, string name) =>
        opts.TryGetValue(name, out var v) && v.Length > 0 ? v : throw new ArgumentException($"--{name} is required");
}

/// <summary>Prints in order: a console has no UI thread to post to.</summary>
internal sealed class ConsoleProgress<T>(Action<T> report) : IProgress<T>
{
    private readonly Lock _lock = new();

    public void Report(T value)
    {
        lock (_lock)
        {
            report(value);
        }
    }
}
