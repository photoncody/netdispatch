using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;

namespace Netdispatch.UsbCreator.Core;

public sealed class HubException(string message, HttpStatusCode? status = null) : Exception(message)
{
    public HttpStatusCode? Status { get; } = status;
}

/// <summary>Who is asking: a paired USB Creator (any stick), or one stick's own key.</summary>
public sealed record HubAuth(string? CreatorKey = null, string? StickId = null, string? StickKey = null)
{
    internal void Apply(HttpRequestMessage req)
    {
        if (CreatorKey is not null)
        {
            req.Headers.Add("X-Creator-Key", CreatorKey);
        }
        if (StickKey is not null)
        {
            req.Headers.Add("X-Stick-Key", StickKey);
            req.Headers.Add("X-Stick-Id", StickId ?? "");
        }
    }
}

/// <summary>The hub's USB stick API (<c>/api/usb/app/*</c>) and file downloads.</summary>
public sealed class HubClient
{
    private const string App = "api/usb/app/";
    private readonly HttpClient _http;

    /// <param name="baseUrl">The hub URL as the admin reaches it, e.g. https://netdispatch.example.com.</param>
    public HubClient(HttpClient http, Uri baseUrl)
    {
        _http = http;
        BaseUrl = new Uri(baseUrl.ToString().TrimEnd('/') + "/");
    }

    public Uri BaseUrl { get; }

    public static Uri ParseHubUrl(string text)
    {
        text = text.Trim();
        if (!text.Contains("://", StringComparison.Ordinal))
        {
            text = "https://" + text;
        }
        if (!Uri.TryCreate(text, UriKind.Absolute, out var uri) || (uri.Scheme != "http" && uri.Scheme != "https"))
        {
            throw new HubException($"'{text}' is not an http(s) URL");
        }
        return uri;
    }

    public Task<PairStartResponse> StartPairingAsync(string clientName, CancellationToken ct = default) =>
        SendAsync<PairStartResponse>(HttpMethod.Post, "pair/start", new { name = clientName }, null, ct);

    public Task<PairPollResponse> PollPairingAsync(string pollToken, CancellationToken ct = default) =>
        SendAsync<PairPollResponse>(HttpMethod.Post, "pair/poll", new { poll_token = pollToken }, null, ct);

    public Task<ProfilesResponse> GetProfilesAsync(HubAuth auth, CancellationToken ct = default) =>
        SendAsync<ProfilesResponse>(HttpMethod.Get, "profiles", null, auth, ct);

    public Task<CreatedStick> CreateStickAsync(HubAuth auth, string name, int profileId, CancellationToken ct = default) =>
        SendAsync<CreatedStick>(HttpMethod.Post, "sticks", new { name, profile_id = profileId }, auth, ct);

    public Task<Manifest> GetManifestAsync(HubAuth auth, string stickId, CancellationToken ct = default) =>
        SendAsync<Manifest>(HttpMethod.Get, $"sticks/{Uri.EscapeDataString(stickId)}/manifest", null, auth, ct);

    /// <param name="have">ISO id to the etag of the complete copy the stick holds.</param>
    /// <param name="images">Whether the stick is meant to carry images (not online-only),
    /// even while it holds none: its boot scripts then still ask the hub for updates.</param>
    public Task<Layout> GetLayoutAsync(HubAuth auth, string stickId, IReadOnlyDictionary<int, string> have, bool images, CancellationToken ct = default) =>
        SendAsync<Layout>(
            HttpMethod.Post,
            $"sticks/{Uri.EscapeDataString(stickId)}/layout",
            new { have = have.ToDictionary(kv => kv.Key.ToString(System.Globalization.CultureInfo.InvariantCulture), kv => kv.Value), images },
            auth,
            ct);

    public Task ReportAsync(HubAuth auth, string stickId, IEnumerable<ReportItem> items, CancellationToken ct = default) =>
        SendAsync<object>(HttpMethod.Post, $"sticks/{Uri.EscapeDataString(stickId)}/report", new { items }, auth, ct);

    public async Task<BootTemplate> GetBootTemplateAsync(HubAuth auth, int sizeMb, long offsetSectors, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, new Uri(BaseUrl, $"{App}boot-template?size_mb={sizeMb}&offset={offsetSectors}"));
        auth.Apply(req);
        using var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        await EnsureOk(resp, ct).ConfigureAwait(false);
        var head = long.Parse(Header(resp, "X-ND-Head-Bytes") ?? "0", System.Globalization.CultureInfo.InvariantCulture);
        var gz = await resp.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
        // Hubs before the header always installed SYSLINUX.
        return new BootTemplate(gz, head, Header(resp, "X-ND-Boot-Files") ?? "", Header(resp, "X-ND-Bios") != "0");
    }

    public async Task<byte[]> GetMbrAsync(HubAuth auth, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, new Uri(BaseUrl, App + "mbr"));
        auth.Apply(req);
        using var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        await EnsureOk(resp, ct).ConfigureAwait(false);
        var mbr = await resp.Content.ReadAsByteArrayAsync(ct).ConfigureAwait(false);
        if (mbr.Length != 440)
        {
            throw new HubException($"the hub's MBR boot code is {mbr.Length} bytes, expected 440");
        }
        return mbr;
    }

    /// <summary>Where to download a file: the caller's relay cache when the manifest names one, else the hub.</summary>
    public Uri FileUrl(ManifestImage image, ManifestFile file) =>
        image.CacheUrl is { Length: > 0 } cache
            ? new Uri(cache.TrimEnd('/') + "/" + file.Cache.TrimStart('/'))
            : new Uri(BaseUrl, file.Src.TrimStart('/'));

    /// <summary>GET a file, from <paramref name="offset"/> on when resuming. Returns the response
    /// and whether it starts at <paramref name="offset"/> (206) or at 0 (200).</summary>
    public async Task<(HttpResponseMessage Response, bool Resumed)> GetFileAsync(Uri url, long offset, CancellationToken ct)
    {
        var req = new HttpRequestMessage(HttpMethod.Get, url);
        if (offset > 0)
        {
            req.Headers.Range = new RangeHeaderValue(offset, null);
        }
        var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);
        if (resp.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable)
        {
            resp.Dispose();
            return await GetFileAsync(url, 0, ct).ConfigureAwait(false);
        }
        await EnsureOk(resp, ct).ConfigureAwait(false);
        return (resp, offset > 0 && resp.StatusCode == HttpStatusCode.PartialContent);
    }

    private async Task<T> SendAsync<T>(HttpMethod method, string path, object? body, HubAuth? auth, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(method, new Uri(BaseUrl, App + path));
        auth?.Apply(req);
        if (body is not null)
        {
            req.Content = JsonContent.Create(body, options: HubJson.Options);
        }
        using var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        await EnsureOk(resp, ct).ConfigureAwait(false);
        var result = await resp.Content.ReadFromJsonAsync<T>(HubJson.Options, ct).ConfigureAwait(false);
        return result ?? throw new HubException($"{path}: empty answer from the hub");
    }

    private static string? Header(HttpResponseMessage resp, string name) =>
        resp.Headers.TryGetValues(name, out var v) || resp.Content.Headers.TryGetValues(name, out v) ? v.FirstOrDefault() : null;

    private static async Task EnsureOk(HttpResponseMessage resp, CancellationToken ct)
    {
        if (resp.IsSuccessStatusCode)
        {
            return;
        }
        var detail = "";
        try
        {
            var text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            using var doc = System.Text.Json.JsonDocument.Parse(text);
            detail = doc.RootElement.TryGetProperty("detail", out var d) ? d.ToString() : text;
        }
        catch (Exception e) when (e is System.Text.Json.JsonException or HttpRequestException)
        {
        }
        var path = resp.RequestMessage?.RequestUri?.AbsolutePath ?? "";
        throw new HubException(
            $"{path}: HTTP {(int)resp.StatusCode} {resp.ReasonPhrase}{(detail.Length > 0 ? " - " + detail : "")}", resp.StatusCode);
    }
}
