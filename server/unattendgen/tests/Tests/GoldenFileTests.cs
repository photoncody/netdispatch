using System.Text;
using System.Xml;
using Netdispatch.UnattendGen;
using Netdispatch.UnattendGen.PostProcessing;

namespace Netdispatch.UnattendGen.Tests;

/// <summary>
/// Every preset in presets/ has a baseline in baselines/ with the exact expected XML.
/// The diff of baselines/ is the reviewable record of how output changes.
///
/// To rewrite the baselines on purpose, run the tests with UNATTENDHOST_UPDATE_BASELINES=1
/// and commit the result together with the change that caused it.
/// </summary>
public class GoldenFileTests
{
    private static bool UpdateBaselines => Environment.GetEnvironmentVariable("UNATTENDHOST_UPDATE_BASELINES") == "1";

    private static string BaselinePath(string preset) => Path.Combine(RepoPaths.Baselines, preset + ".xml");

    private static string GenerateNormalized(string preset)
    {
        byte[] xml = Shared.Generation.Generate(Presets.Load(preset)).Xml;
        return BaselineNormalizer.Normalize(Encoding.ASCII.GetString(xml));
    }

    [Fact]
    public void RepoHasPresets()
    {
        Assert.NotEmpty(Presets.List());
    }

    [Theory]
    [MemberData(nameof(Shared.PresetNames), MemberType = typeof(Shared))]
    public void Preset_MatchesBaseline(string preset)
    {
        string actual = GenerateNormalized(preset);
        string path = BaselinePath(preset);

        if (UpdateBaselines)
        {
            Directory.CreateDirectory(RepoPaths.Baselines);
            File.WriteAllBytes(path, Encoding.ASCII.GetBytes(actual));
            return;
        }

        Assert.True(File.Exists(path), $"Missing baseline {path}. Run the tests with UNATTENDHOST_UPDATE_BASELINES=1 to create it.");
        string expected = Encoding.ASCII.GetString(File.ReadAllBytes(path));
        Assert.Equal(expected, actual);
    }

    [Theory]
    [MemberData(nameof(Shared.PresetNames), MemberType = typeof(Shared))]
    public void Preset_IsWellFormedWithoutDuplicateComponents(string preset)
    {
        var doc = new XmlDocument();
        doc.LoadXml(GenerateNormalized(preset));

        Assert.Equal(UnattendXml.UnattendNamespace, doc.DocumentElement?.NamespaceURI);
        Assert.Empty(UnattendXml.FindDuplicateComponents(doc));
    }

    [Fact]
    public void EveryBaseline_HasAPreset()
    {
        var presets = Presets.List();
        var orphans = Directory.EnumerateFiles(RepoPaths.Baselines, "*.xml")
            .Select(Path.GetFileNameWithoutExtension)
            .Where(name => !presets.Contains(name!));

        Assert.Empty(orphans);
    }

    [Fact]
    public void Normalizer_ReplacesUpstreamCommit()
    {
        string xml = Encoding.ASCII.GetString(Shared.Generation.Generate(new HostConfig()).Xml);

        string normalized = BaselineNormalizer.Normalize(xml);

        Assert.Contains($"<Hash>{BaselineNormalizer.CommitPlaceholder}</Hash>", normalized);
        Assert.Contains($"<GitHubUrl>{BaselineNormalizer.CommitPlaceholder}</GitHubUrl>", normalized);
    }
}
