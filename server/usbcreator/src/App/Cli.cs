using Netdispatch.UsbCreator.Core;
using Netdispatch.UsbCreator.Windows;

namespace Netdispatch.UsbCreator.App;

/// <summary>USB Creator without the window, for scripts and testing:
/// <code>
/// netdispatch-usb-creator.exe --cli pair --hub https://netdispatch.example.com
/// netdispatch-usb-creator.exe --cli list [--allow-virtual-disks]
/// netdispatch-usb-creator.exe --cli create --disk 3 --profile Lab --name "Lab stick" [--online-only] --yes
/// netdispatch-usb-creator.exe --cli update --disk 3
/// </code>
/// Run it from an elevated prompt: writing a disk needs administrator rights.</summary>
internal static class Cli
{
    public static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var opts = Parse(args);
            var command = args.FirstOrDefault() ?? "help";
            var allowVirtual = opts.ContainsKey("allow-virtual-disks");
            switch (command)
            {
                case "pair":
                    await PairAsync(Need(opts, "hub")).ConfigureAwait(false);
                    return 0;
                case "list":
                    foreach (var d in await WindowsDisks.ListAsync(allowVirtual).ConfigureAwait(false))
                    {
                        Console.WriteLine(d.Display + (d.Roots is not null ? "  [netdispatch stick]" : ""));
                    }
                    return 0;
                case "create":
                case "update":
                    return await WriteAsync(command, opts, allowVirtual).ConfigureAwait(false);
                default:
                    Console.WriteLine("usage: --cli pair --hub URL | list | create --disk N --profile NAME --name NAME [--online-only] --yes | update --disk N");
                    Console.WriteLine("       add --allow-virtual-disks to include VHD/VHDX disks");
                    return command == "help" ? 0 : 2;
            }
        }
        catch (Exception e) when (e is HubException or IOException or HttpRequestException or ArgumentException)
        {
            Console.Error.WriteLine("error: " + e.Message);
            return 1;
        }
    }

    private static async Task PairAsync(string hubText)
    {
        var url = HubClient.ParseHubUrl(hubText);
        var hub = new HubClient(Http.Client, url);
        var start = await hub.StartPairingAsync($"USB Creator on {Environment.MachineName}").ConfigureAwait(false);
        Console.WriteLine($"Approve code {start.Code} on the hub's dashboard (Boot media -> USB Creator).");
        var until = DateTime.UtcNow.AddSeconds(start.ExpiresIn);
        while (DateTime.UtcNow < until)
        {
            await Task.Delay(3000).ConfigureAwait(false);
            var poll = await hub.PollPairingAsync(start.PollToken).ConfigureAwait(false);
            if (poll is { Status: "approved", CreatorKey: { } key })
            {
                CreatorSettings.Load().WithKey(url.ToString(), key, poll.Name ?? "").Save();
                Console.WriteLine($"Paired as \"{poll.Name}\".");
                return;
            }
        }
        throw new IOException("the pairing code expired");
    }

    private static async Task<int> WriteAsync(string command, Dictionary<string, string> opts, bool allowVirtual)
    {
        var settings = CreatorSettings.Load();
        if (settings.HubUrl is null || settings.CreatorKey is not { } key)
        {
            throw new IOException("not paired with a hub yet: run --cli pair --hub URL first");
        }
        var hub = new HubClient(Http.Client, new Uri(settings.HubUrl));
        var number = int.Parse(Need(opts, "disk"), System.Globalization.CultureInfo.InvariantCulture);
        var disk = (await WindowsDisks.ListAsync(allowVirtual).ConfigureAwait(false)).FirstOrDefault(d => d.Number == number)
            ?? throw new IOException($"disk {number} is not a USB disk (add --allow-virtual-disks for a VHD)");
        var last = "";
        var writer = new StickWriter(
            hub,
            key,
            new ConsoleProgress<string>(Console.WriteLine),
            new ConsoleProgress<SyncProgress>(p =>
            {
                var line = p.BytesTotal > 0 ? $"  {p.Stage} {p.BytesDone * 100 / p.BytesTotal}%" : $"  {p.Stage}";
                if (line != last)
                {
                    Console.WriteLine(line);
                    last = line;
                }
            }));
        var copy = !opts.ContainsKey("online-only");
        SyncResult result;
        if (command == "create")
        {
            var profiles = await hub.GetProfilesAsync(new HubAuth(CreatorKey: key)).ConfigureAwait(false);
            var wanted = Need(opts, "profile");
            var profile = profiles.Profiles.FirstOrDefault(p => p.Name == wanted || p.Id.ToString(System.Globalization.CultureInfo.InvariantCulture) == wanted)
                ?? throw new ArgumentException($"no profile \"{wanted}\"");
            if (!opts.ContainsKey("yes"))
            {
                throw new ArgumentException($"this erases {disk.Display}: add --yes to go ahead");
            }
            result = await writer.CreateAsync(disk, profile, Need(opts, "name"), copy, CancellationToken.None).ConfigureAwait(false);
        }
        else
        {
            result = await writer.UpdateAsync(disk, copy, CancellationToken.None).ConfigureAwait(false);
        }
        foreach (var problem in result.Problems)
        {
            Console.WriteLine("warning: " + problem);
        }
        Console.WriteLine($"Done: {result.Have.Count} image(s) on the stick, {result.BytesDownloaded / 1_000_000} MB downloaded.");
        return result.Problems.Count == 0 ? 0 : 3;
    }

    private static Dictionary<string, string> Parse(string[] args)
    {
        var opts = new Dictionary<string, string>();
        for (var i = 1; i < args.Length; i++)
        {
            if (!args[i].StartsWith("--", StringComparison.Ordinal))
            {
                throw new ArgumentException($"unexpected '{args[i]}'");
            }
            var name = args[i][2..];
            opts[name] = i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal) ? args[++i] : "";
        }
        return opts;
    }

    private static string Need(Dictionary<string, string> opts, string name) =>
        opts.TryGetValue(name, out var v) && v.Length > 0 ? v : throw new ArgumentException($"--{name} is required");
}

/// <summary>Reports in order, on the reporting thread: a console has no UI thread to post to.</summary>
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

internal static class Http
{
    /// <summary>One client for the process. The timeout covers API calls and a download's
    /// headers; file bodies are streamed after that, so long downloads aren't cut off.</summary>
    public static readonly HttpClient Client = new(new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(20) })
    {
        Timeout = TimeSpan.FromMinutes(10),
    };
}
