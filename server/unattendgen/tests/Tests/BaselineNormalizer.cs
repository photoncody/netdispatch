using System.Text.RegularExpressions;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Removes the parts of generated XML that do not depend on the configuration, so that
/// baselines only change when the output really changes.
///
/// Upstream's output is otherwise deterministic (no timestamps, GUIDs or random values). The one
/// exception is the upstream commit hash that upstream writes into &lt;Extensions&gt;&lt;Build&gt;:
/// it is empty when the build had no git metadata. The pinned commit is recorded by the
/// submodule pointer and reported by /healthz instead.
/// </summary>
public static partial class BaselineNormalizer
{
    public const string CommitPlaceholder = "UPSTREAM-COMMIT";

    public static string Normalize(string xml)
    {
        xml = HashElement().Replace(xml, $"<Hash>{CommitPlaceholder}</Hash>");
        xml = GitHubUrlElement().Replace(xml, $"<GitHubUrl>{CommitPlaceholder}</GitHubUrl>");
        return xml;
    }

    [GeneratedRegex("<Hash>(?:[0-9a-f]{40})?</Hash>")]
    private static partial Regex HashElement();

    [GeneratedRegex("<GitHubUrl>(?:https://github\\.com/cschneegans/unattend-generator/commit/[0-9a-f]{40})?</GitHubUrl>")]
    private static partial Regex GitHubUrlElement();
}
