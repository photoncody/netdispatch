using Schneegans.Unattend;
using Netdispatch.UnattendGen;

namespace Netdispatch.UnattendGen.Tests;

internal static class RepoPaths
{
    /// <summary>The server/unattendgen folder.</summary>
    public static string Root { get; } = FindRoot();

    public static string Presets => Path.Combine(Root, "tests", "Tests", "fixtures", "presets");

    public static string Baselines => Path.Combine(Root, "tests", "Tests", "fixtures", "baselines");

    private static string FindRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "Unattendgen.slnx")))
            {
                return dir.FullName;
            }
        }
        throw new InvalidOperationException("Could not find the folder with Unattendgen.slnx.");
    }
}

/// <summary>Settings files (HostConfig JSON) in fixtures/presets, each with a baseline in fixtures/baselines.</summary>
internal static class Presets
{
    public static IReadOnlyList<string> List()
    {
        return [.. Directory.EnumerateFiles(RepoPaths.Presets, "*.json")
            .Select(Path.GetFileNameWithoutExtension)
            .OfType<string>()
            .Order(StringComparer.Ordinal)];
    }

    public static HostConfig Load(string name)
    {
        return HostConfigJson.Deserialize(File.ReadAllText(Path.Combine(RepoPaths.Presets, name + ".json")));
    }
}

internal static class Shared
{
    // Creating the generator parses upstream's JSON resources, so share one instance.
    public static UnattendGenerator Generator { get; } = new();

    public static GenerationService Generation { get; } = new(Generator);

    public static TheoryData<string> PresetNames => [.. Presets.List()];
}
