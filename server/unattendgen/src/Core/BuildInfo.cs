using System.Reflection;
using System.Text.RegularExpressions;
using Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Version information baked in at build time. The .NET SDK appends "+&lt;git commit&gt;" to each
/// assembly's informational version when it builds from a git checkout; for the upstream
/// assembly that is the pinned submodule commit.
/// </summary>
public static partial class BuildInfo
{
    /// <summary>The upstream commit this build was compiled from, or null if the build had no git metadata.</summary>
    public static string? UpstreamCommit { get; } = CommitOf(typeof(UnattendGenerator).Assembly);

    public static string AppVersion { get; } = InformationalVersion(typeof(BuildInfo).Assembly) ?? "unknown";

    private static string? InformationalVersion(Assembly assembly)
    {
        return assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
    }

    private static string? CommitOf(Assembly assembly)
    {
        var match = CommitSuffix().Match(InformationalVersion(assembly) ?? "");
        return match.Success ? match.Groups[1].Value.ToLowerInvariant() : null;
    }

    [GeneratedRegex(@"\+([0-9a-f]{40})$", RegexOptions.IgnoreCase)]
    private static partial Regex CommitSuffix();
}
