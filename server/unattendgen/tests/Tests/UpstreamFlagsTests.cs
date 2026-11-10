using Netdispatch.UnattendGen;
using U = Schneegans.Unattend;

namespace Netdispatch.UnattendGen.Tests;

public class UpstreamFlagsTests
{
    [Fact]
    public void Defaults_MatchUpstreamDefaultConfiguration()
    {
        Assert.Contains(UpstreamFlags.Defaults, f => f.Key == nameof(U.Configuration.DisableUac) && !f.Value);
        Assert.DoesNotContain(UpstreamFlags.Defaults, f => UpstreamFlags.ModeledElsewhere.Contains(f.Key));
    }

    [Fact]
    public void Apply_SetsOnlyTheGivenFlags()
    {
        var result = UpstreamFlags.Apply(U.Configuration.Default, new Dictionary<string, bool> { ["DisableUac"] = true });

        Assert.True(result.DisableUac);
        Assert.False(result.DisableSac);
        Assert.Equal(U.HideModes.Hidden, result.HideFiles);
    }

    [Fact]
    public void Apply_KeepsNonFlagSettings()
    {
        var start = U.Configuration.Default with { KeepSensitiveFiles = true, ProcessorArchitectures = [U.ProcessorArchitecture.arm64] };

        var result = UpstreamFlags.Apply(start, new Dictionary<string, bool> { ["EnableLongPaths"] = true });

        Assert.True(result.KeepSensitiveFiles);
        Assert.Equal([U.ProcessorArchitecture.arm64], result.ProcessorArchitectures);
        Assert.True(result.EnableLongPaths);
    }

    [Fact]
    public void Apply_RejectsUnknownNames()
    {
        Assert.Throws<ArgumentException>(() => UpstreamFlags.Apply(U.Configuration.Default, new Dictionary<string, bool> { ["Nope"] = true }));
    }

    [Fact]
    public void FlagInSettings_ChangesOutput()
    {
        var config = new HostConfig { Flags = new Dictionary<string, bool> { ["EnableLongPaths"] = true } };

        string xml = System.Text.Encoding.ASCII.GetString(Shared.Generation.Generate(config).Xml);

        Assert.Contains("LongPathsEnabled", xml);
    }
}
