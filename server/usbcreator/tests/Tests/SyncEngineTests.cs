using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Tests;

public class SyncEngineTests
{
    private static (FakeHub Hub, TempStick Stick, SyncEngine Engine) Setup()
    {
        var hub = new FakeHub();
        var stick = new TempStick();
        return (hub, stick, new SyncEngine(hub.Client(), FakeHub.Auth, stick.Roots));
    }

    private static ManifestImage Windows(FakeHub hub, string wim = "WIM-1", string install = "INSTALL-1") =>
        hub.AddImage(7, "Windows 11",
            ("boot", "boot.wim", wim), ("boot", "BCD", "bcd"),
            ("data", "setup.exe", "MZ"), ("data", "sources/install.wim", install), ("data", "sources/$OEM$/My Script.cmd", "echo"));

    [Fact]
    public async Task FreshSyncCopiesEveryFileToItsPartition()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        var image = Windows(hub);
        var result = await engine.SyncAsync(hub.Manifest(), copyImages: true, TestContext.Current.CancellationToken);

        Assert.Equal("WIM-1", stick.Read("boot", "netdispatch/boot/7/boot.wim"));
        Assert.Equal("INSTALL-1", stick.Read("data", "netdispatch/img/7/sources/install.wim"));
        Assert.Equal("echo", stick.Read("data", "netdispatch/img/7/sources/$OEM$/My Script.cmd"));
        var mtime = File.GetLastWriteTimeUtc(stick.Roots.Resolve("data", "netdispatch/img/7/setup.exe"));
        Assert.Equal(image.Files!.Single(f => f.Dest.EndsWith("setup.exe", StringComparison.Ordinal)).Mtime, new DateTimeOffset(mtime).ToUnixTimeSeconds());
        Assert.Equal(new Dictionary<int, string> { [7] = image.Etag! }, result.Have);
        Assert.Empty(result.Problems);
        // The menu: first without the image (it wasn't complete), then with it.
        Assert.Equal(2, hub.LayoutRequests.Count);
        Assert.Empty(hub.LayoutRequests[0]);
        Assert.Equal(image.Etag, hub.LayoutRequests[1]["7"]);
        Assert.Contains("item 7", stick.Read("boot", "EFI/BOOT/autoexec.ipxe"), StringComparison.Ordinal);
        Assert.Equal("@echo off\r\n", stick.Read("boot", "netdispatch/entry/1/netdispatch.cmd"));
        var state = StickState.Load(stick.Roots)!;
        Assert.Equal(FakeHub.StickId, state.StickId);
        Assert.Equal("bootv1", state.BootFiles);
        Assert.Equal(image.Etag, state.Images[7]);
        Assert.Equal(FakeHub.StickId, StickState.ReadStickId(stick.Roots.Data));
        var item = Assert.Single(Assert.Single(hub.Reports).GetProperty("items").EnumerateArray());
        Assert.Equal("ready", item.GetProperty("status").GetString());
        Assert.Equal(image.Etag, item.GetProperty("etag").GetString());
    }

    [Fact]
    public async Task SecondSyncDownloadsNothing()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        Windows(hub);
        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        hub.Downloads.Clear();
        hub.LayoutRequests.Clear();
        var again = await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        Assert.Empty(hub.Downloads);
        Assert.Equal(0, again.BytesDownloaded);
        // Nothing changes, so the image stays on the menu throughout.
        Assert.All(hub.LayoutRequests, have => Assert.True(have.ContainsKey("7")));
    }

    [Fact]
    public async Task InterruptedDownloadResumes()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        var image = Windows(hub, install: "0123456789");
        var file = image.Files!.Single(f => f.Dest.EndsWith("install.wim", StringComparison.Ordinal));
        var dest = stick.Roots.Resolve(file.Part, file.Dest);
        Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
        File.WriteAllText($"{dest}.nd-{file.Size}-{file.Mtime}.part", "01234");
        File.WriteAllText($"{dest}.nd-3-1.part", "old");  // another version's partial file

        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);

        Assert.Equal("0123456789", File.ReadAllText(dest));
        Assert.Contains(hub.Downloads, d => d.Url.EndsWith("install.wim", StringComparison.Ordinal) && d.From == 5);
        Assert.Empty(Directory.EnumerateFiles(Path.GetDirectoryName(dest)!, "*.part"));
    }

    [Fact]
    public async Task UpdateReplacesChangedFilesAndRemovesStaleOnes()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        Windows(hub);
        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        var updated = hub.AddImage(7, "Windows 11",
            ("boot", "boot.wim", "WIM-2"), ("boot", "BCD", "bcd"), ("data", "setup.exe", "MZ"), ("data", "sources/install.wim", "INSTALL-22"));
        hub.Downloads.Clear();
        hub.LayoutRequests.Clear();

        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);

        Assert.Equal("WIM-2", stick.Read("boot", "netdispatch/boot/7/boot.wim"));
        Assert.Equal("INSTALL-22", stick.Read("data", "netdispatch/img/7/sources/install.wim"));
        Assert.False(stick.Has("data", "netdispatch/img/7/sources/$OEM$/My Script.cmd"));
        Assert.False(Directory.Exists(stick.Roots.Resolve("data", "netdispatch/img/7/sources/$OEM$")));
        Assert.Equal(2, hub.Downloads.Count);  // only the two changed files
        // The old copy left the menu before its files were touched.
        Assert.Empty(hub.LayoutRequests[0]);
        Assert.Equal(updated.Etag, hub.LayoutRequests[1]["7"]);
    }

    [Fact]
    public async Task ImagesLeavingTheProfileAreDeleted()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        Windows(hub);
        hub.AddImage(9, "WinPE", ("boot", "boot.wim", "PE"));
        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        Assert.True(stick.Has("boot", "netdispatch/boot/9/boot.wim"));

        hub.Images.RemoveAll(i => i.IsoId == 9);
        var result = await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        Assert.False(Directory.Exists(stick.Roots.Resolve("boot", "netdispatch/boot/9")));
        Assert.Equal([7], result.Have.Keys);

        // An online-only stick keeps no images at all.
        result = await engine.SyncAsync(hub.Manifest(), copyImages: false, TestContext.Current.CancellationToken);
        Assert.Empty(result.Have);
        Assert.False(Directory.Exists(stick.Roots.Resolve("data", "netdispatch/img/7")));
        Assert.DoesNotContain("item", stick.Read("boot", "EFI/BOOT/autoexec.ipxe"), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ImageBeingPreparedOnTheHubIsKept()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        var image = Windows(hub);
        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        hub.Images[0] = new ManifestImage(7, "Windows 11", null, null, 0, 0, null, null, Keep: true, null);

        var result = await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);
        Assert.Equal(image.Etag, result.Have[7]);
        Assert.True(stick.Has("data", "netdispatch/img/7/setup.exe"));
    }

    [Fact]
    public async Task FailedImageIsReportedAndLeftOffTheMenu()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        Windows(hub);
        var pe = hub.AddImage(9, "WinPE", ("boot", "boot.wim", "PE"));
        hub.Broken.Add(pe.Files![0].Src);

        var result = await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);

        Assert.Equal([7], result.Have.Keys);
        Assert.Contains(result.Problems, p => p.StartsWith("WinPE:", StringComparison.Ordinal) && p.Contains("boom", StringComparison.Ordinal));
        var items = Assert.Single(hub.Reports).GetProperty("items").EnumerateArray().ToList();
        Assert.Equal("error", items.Single(i => i.GetProperty("iso_id").GetInt32() == 9).GetProperty("status").GetString());
    }

    [Fact]
    public async Task RelayCacheIsUsedWhenTheManifestNamesOne()
    {
        var (hub, stick, engine) = Setup();
        using var _ = stick;
        var image = Windows(hub);
        hub.Images[0] = image with { CacheUrl = "http://relay.test:8080" };

        await engine.SyncAsync(hub.Manifest(), true, TestContext.Current.CancellationToken);

        Assert.All(hub.Downloads, d => Assert.StartsWith("http://relay.test:8080/tree/7/", d.Url, StringComparison.Ordinal));
        Assert.Equal("echo", stick.Read("data", "netdispatch/img/7/sources/$OEM$/My Script.cmd"));
    }

    [Theory]
    [InlineData("../x")]
    [InlineData("/etc/passwd")]
    [InlineData("a/../../b")]
    [InlineData("C:/x")]
    [InlineData("a\\b")]
    public void HubPathsCannotLeaveThePartition(string path)
    {
        var roots = new StickRoots("/b", "/d");
        Assert.Throws<HubException>(() => roots.Resolve("data", path));
    }

    [Fact]
    public void BootPartitionHoldsTwoCopies()
    {
        Assert.Equal(2048, StickSizing.BootMegabytes(100 * StickSizing.MiB));
        Assert.Equal(2560, StickSizing.BootMegabytes(1024 * StickSizing.MiB));
        var needed = StickSizing.RequiredBytes(5L << 30, 2048);
        Assert.InRange(needed, (5L << 30) + (2048 + 256) * StickSizing.MiB, (6L << 30) + (2048 + 256) * StickSizing.MiB);
    }

    [Fact]
    public void HubUrlsAreNormalised()
    {
        Assert.Equal("https://hub.example.com/", HubClient.ParseHubUrl("hub.example.com").ToString());
        Assert.Equal("http://10.0.0.5:8000/", HubClient.ParseHubUrl(" http://10.0.0.5:8000/ ").ToString());
        Assert.Throws<HubException>(() => HubClient.ParseHubUrl("ftp://x"));
    }
}
