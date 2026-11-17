using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Tests;

/// <summary>An in-memory hub: the manifest, layouts, reports and file downloads of one stick.</summary>
internal sealed class FakeHub : HttpMessageHandler
{
    public const string Hub = "http://hub.test:8000/";
    public const string StickId = "a1b2c3d4e5f6";

    public Dictionary<string, byte[]> Files { get; } = [];
    public List<ManifestImage> Images { get; } = [];
    public string BootFiles { get; set; } = "bootv1";
    public List<Dictionary<string, string>> LayoutRequests { get; } = [];
    public List<bool> LayoutImages { get; } = [];
    public List<JsonElement> Reports { get; } = [];
    public List<(string Url, long? From)> Downloads { get; } = [];
    public HashSet<string> Broken { get; } = [];

    public HubClient Client() => new(new HttpClient(this), new Uri(Hub));

    public static HubAuth Auth => new(StickId: StickId, StickKey: "nds_test");

    /// <summary>Add an image whose files are served at /files/iso/{id}/tree/{rel}.</summary>
    public ManifestImage AddImage(int isoId, string name, params (string Part, string Rel, string Content)[] files)
    {
        var entries = new List<ManifestFile>();
        foreach (var (part, rel, content) in files)
        {
            var dest = part == StickRoots.BootPart ? $"netdispatch/boot/{isoId}/{rel}" : $"netdispatch/img/{isoId}/{rel}";
            var src = $"/files/iso/{isoId}/tree/{Uri.EscapeDataString(rel).Replace("%2F", "/", StringComparison.Ordinal)}";
            Files[src] = Encoding.UTF8.GetBytes(content);
            var cache = $"tree/{isoId}/{Uri.EscapeDataString(rel.ToLowerInvariant()).Replace("%2F", "/", StringComparison.Ordinal)}";
            entries.Add(new ManifestFile(part, dest, src, cache, content.Length, 1_700_000_000 + content.Length * 1000 + content.Sum(c => c) % 1000 * 10));
        }
        var etag = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(
            Encoding.UTF8.GetBytes(string.Join("|", entries.Select(e => $"{e.Dest}:{e.Size}:{e.Mtime}")))));
        var image = new ManifestImage(isoId, name, "windows_setup", etag,
            entries.Where(e => e.Part == "boot").Sum(e => e.Size), entries.Where(e => e.Part == "data").Sum(e => e.Size),
            null, entries, false, null);
        Images.RemoveAll(i => i.IsoId == isoId);
        Images.Add(image);
        return image;
    }

    public Manifest Manifest() =>
        new(new StickInfo(StickId, "Test stick", 1, "Lab", "test"), Hub, BootFiles, [.. Images]);

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var path = request.RequestUri!.AbsolutePath;
        if (path.StartsWith("/api/usb/app/", StringComparison.Ordinal) && request.Headers.TryGetValues("X-Stick-Key", out var k)
            && k.Single() != "nds_test")
        {
            return new HttpResponseMessage(HttpStatusCode.Unauthorized);
        }
        if (path == $"/api/usb/app/sticks/{StickId}/layout")
        {
            var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct)).RootElement;
            var have = body.GetProperty("have").EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString()!);
            LayoutRequests.Add(have);
            LayoutImages.Add(body.GetProperty("images").GetBoolean());
            var menu = string.Join("\n", have.Keys.Order().Select(id => $"item {id}"));
            return Json(new
            {
                files = new object[]
                {
                    new { part = "boot", path = "EFI/BOOT/autoexec.ipxe", content = "#!ipxe\n" + menu + "\n" },
                    new { part = "data", path = "netdispatch/stick.json", content = $"{{\"id\": \"{StickId}\"}}" },
                    new { part = "boot", path = "netdispatch/entry/1/netdispatch.cmd", content = "@echo off\r\n" },
                },
                replace_dirs = new object[] { new { part = "boot", path = "netdispatch/entry" } },
                problems = Array.Empty<string>(),
            });
        }
        if (path == $"/api/usb/app/sticks/{StickId}/report")
        {
            Reports.Add(JsonDocument.Parse(await request.Content!.ReadAsStringAsync(ct)).RootElement.Clone());
            return Json(new { ok = true });
        }
        if (path == $"/api/usb/app/sticks/{StickId}/manifest")
        {
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(JsonSerializer.Serialize(Manifest(), HubJson.Options), Encoding.UTF8, "application/json"),
            };
        }
        var lookup = path.StartsWith("/tree/", StringComparison.Ordinal)
            ? RelayLookup(path)
            : Files.Keys.FirstOrDefault(f => Uri.UnescapeDataString(f) == Uri.UnescapeDataString(path));
        long? from = request.Headers.Range?.Ranges.Single().From;
        Downloads.Add((request.RequestUri.ToString(), from));
        if (lookup is null || Broken.Contains(lookup))
        {
            return new HttpResponseMessage(HttpStatusCode.InternalServerError) { Content = new StringContent("{\"detail\": \"boom\"}") };
        }
        var bytes = Files[lookup];
        if (from is long start && start > 0)
        {
            var resp = new HttpResponseMessage(HttpStatusCode.PartialContent) { Content = new ByteArrayContent(bytes[(int)start..]) };
            resp.Content.Headers.ContentRange = new ContentRangeHeaderValue(start, bytes.Length - 1, bytes.Length);
            return resp;
        }
        return new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) };
    }

    private string? RelayLookup(string path)
    {
        // /tree/{iso}/{lower rel}: the relay serves the hub's files case-insensitively.
        var parts = path.Split('/', 3, StringSplitOptions.RemoveEmptyEntries);
        var rel = Uri.UnescapeDataString(parts[2]);
        return Files.Keys.FirstOrDefault(f =>
            f.StartsWith($"/files/iso/{parts[1]}/tree/", StringComparison.Ordinal)
            && Uri.UnescapeDataString(f[$"/files/iso/{parts[1]}/tree/".Length..]).Equals(rel, StringComparison.OrdinalIgnoreCase));
    }

    private static HttpResponseMessage Json(object body) =>
        new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json") };
}

/// <summary>Two temporary directories standing in for the stick's partitions.</summary>
internal sealed class TempStick : IDisposable
{
    public TempStick()
    {
        Root = Directory.CreateTempSubdirectory("nd-stick-").FullName;
        Roots = new StickRoots(Path.Combine(Root, "boot"), Path.Combine(Root, "data"));
        Directory.CreateDirectory(Roots.Boot);
        Directory.CreateDirectory(Roots.Data);
    }

    public string Root { get; }

    public StickRoots Roots { get; }

    public string Read(string part, string rel) => File.ReadAllText(Roots.Resolve(part, rel));

    public bool Has(string part, string rel) => File.Exists(Roots.Resolve(part, rel));

    public void Dispose() => Directory.Delete(Root, recursive: true);
}
