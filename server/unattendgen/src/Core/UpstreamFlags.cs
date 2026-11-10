using System.Reflection;
using U = Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Upstream's plain on/off settings: the bool parameters of U.Configuration's constructor.
///
/// HostConfig.Flags sets them by upstream's own name, so a switch that upstream adds shows up in
/// the CLI's schema and works without a change here. Names that HostConfig models as a property
/// of its own (because other rules depend on them) are not flags.
/// </summary>
public static class UpstreamFlags
{
    /// <summary>Bool settings that HostConfig exposes as properties instead.</summary>
    public static IReadOnlySet<string> ModeledElsewhere { get; } = new HashSet<string>(StringComparer.Ordinal)
    {
        nameof(U.Configuration.BypassNetworkCheck),
        nameof(U.Configuration.KeepSensitiveFiles),
    };

    private static readonly ConstructorInfo Constructor = typeof(U.Configuration)
        .GetConstructors()
        .Single(c => c.GetParameters() is var p && !(p.Length == 1 && p[0].ParameterType == typeof(U.Configuration)));

    private static readonly ParameterInfo[] Parameters = Constructor.GetParameters();

    /// <summary>Every flag name with upstream's default value, in upstream's declaration order.</summary>
    public static IReadOnlyList<KeyValuePair<string, bool>> Defaults { get; } = [.. Parameters
        .Where(p => p.ParameterType == typeof(bool) && !ModeledElsewhere.Contains(p.Name!))
        .Select(p => new KeyValuePair<string, bool>(p.Name!, (bool)Property(p.Name!).GetValue(U.Configuration.Default)!))];

    private static readonly HashSet<string> Names = [.. Defaults.Select(d => d.Key)];

    public static bool IsKnown(string name) => Names.Contains(name);

    /// <summary>Returns a copy of the configuration with the given flags set.</summary>
    /// <exception cref="ArgumentException">A name is not a known flag.</exception>
    public static U.Configuration Apply(U.Configuration configuration, IReadOnlyDictionary<string, bool> flags)
    {
        if (flags.Count == 0)
        {
            return configuration;
        }

        foreach (var name in flags.Keys)
        {
            if (!IsKnown(name))
            {
                throw new ArgumentException($"'{name}' is not a known flag.", nameof(flags));
            }
        }

        object?[] args = [.. Parameters.Select(p => flags.TryGetValue(p.Name!, out bool value)
            ? value
            : Property(p.Name!).GetValue(configuration))];
        return (U.Configuration)Constructor.Invoke(args);
    }

    private static PropertyInfo Property(string name)
    {
        return typeof(U.Configuration).GetProperty(name)
            ?? throw new InvalidOperationException($"Configuration has a constructor parameter '{name}' but no property of that name.");
    }
}
