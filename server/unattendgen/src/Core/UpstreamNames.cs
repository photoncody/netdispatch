namespace Netdispatch.UnattendGen;

/// <summary>
/// Settings that take one of upstream's own names (visual effects, Sticky Keys options,
/// configuration passes). Like the on/off flags, they are not copied into enums of ours: the
/// CLI's "schema" command lists the names a build accepts, so a name upstream adds works
/// without a change here. Names are matched exactly; numbers are not accepted.
/// </summary>
public static class UpstreamNames
{
    public static IReadOnlyList<string> Of<T>() where T : struct, Enum => Enum.GetNames<T>();

    public static bool IsKnown<T>(string? name) where T : struct, Enum
    {
        return name != null && Array.IndexOf(Enum.GetNames<T>(), name) >= 0;
    }

    /// <exception cref="Schneegans.Unattend.ConfigurationException">The name is not one of T's.</exception>
    public static T Parse<T>(string name) where T : struct, Enum
    {
        return IsKnown<T>(name)
            ? Enum.Parse<T>(name)
            : throw new Schneegans.Unattend.ConfigurationException($"'{name}' is not a known {typeof(T).Name} value.");
    }

    /// <summary>True for "#rrggbb".</summary>
    public static bool IsHtmlColor(string? value)
    {
        return value is { Length: 7 } && value[0] == '#' && value.AsSpan(1).IndexOfAnyExcept("0123456789abcdefABCDEF") < 0;
    }
}
