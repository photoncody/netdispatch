using Schneegans.Unattend;
using Netdispatch.UnattendGen;

namespace Netdispatch.UnattendGen.Tests;

public class UpstreamSmokeTests
{
    [Fact]
    public void Generator_LoadsEmbeddedResources()
    {
        var generator = new UnattendGenerator();

        Assert.NotEmpty(generator.Bloatwares);
        Assert.NotEmpty(generator.UserLocales);
        Assert.Equal("en-US", generator.Lookup<ImageLanguage>("en-US").Id);
        Assert.Equal("00000409", generator.Lookup<KeyboardIdentifier>("00000409").Id);
    }

    [Fact]
    public void DefaultConfiguration_Generates()
    {
        var doc = Shared.Generator.GenerateXml(Configuration.Default);

        Assert.Equal("unattend", doc.DocumentElement?.LocalName);
    }

    [Fact]
    public void DefaultHostConfig_ProducesUpstreamDefaultOutput()
    {
        byte[] upstream = UnattendGenerator.Serialize(Shared.Generator.GenerateXml(Configuration.Default));

        byte[] ours = Shared.Generation.Generate(new HostConfig()).Xml;

        Assert.Equal(upstream, ours);
    }

    [Fact]
    public void UpstreamCommit_MatchesUpstreamLock()
    {
        // fetch-upstream.sh records the commit as SourceRevisionId; the SDK stamps it into the assembly.
        Assert.NotNull(BuildInfo.UpstreamCommit);

        string lockFile = File.ReadAllText(Path.Combine(RepoPaths.Root, "upstream.lock"));
        Assert.Contains($"UPSTREAM_COMMIT={BuildInfo.UpstreamCommit}\n", lockFile);
    }
}
