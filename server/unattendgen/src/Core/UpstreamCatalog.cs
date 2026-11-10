using Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

public sealed record CatalogItem(string Id, string DisplayName);

/// <summary>A user locale with the keyboard and home location upstream suggests for it.</summary>
public sealed record LocaleItem(string Id, string DisplayName, string? Keyboard, string? GeoLocation);

/// <summary>
/// The lists of choices the UI offers, taken from upstream's own data so they stay in sync
/// when upstream is bumped. Nothing here is hardcoded.
/// </summary>
public sealed class UpstreamCatalog(UnattendGenerator generator)
{
    public IReadOnlyList<CatalogItem> ImageLanguages { get; } =
        Sorted(generator.ImageLanguages.Values.Select(x => new CatalogItem(x.Id, x.DisplayName)));

    public IReadOnlyList<LocaleItem> UserLocales { get; } =
        [.. generator.UserLocales.Values
            .Select(x => new LocaleItem(x.Id, x.DisplayName, x.KeyboardLayout?.Id, x.GeoLocation?.Id))
            .OrderBy(x => x.DisplayName, StringComparer.OrdinalIgnoreCase)
            .ThenBy(x => x.Id, StringComparer.Ordinal)];

    public IReadOnlyList<CatalogItem> KeyboardIdentifiers { get; } =
        Sorted(generator.KeyboardIdentifiers.Values.Select(x => new CatalogItem(x.Id, x.DisplayName)));

    public IReadOnlyList<CatalogItem> GeoLocations { get; } =
        Sorted(generator.GeoLocations.Values.Select(x => new CatalogItem(x.Id, x.DisplayName)));

    /// <summary>Kept in upstream's order, which sorts by UTC offset.</summary>
    public IReadOnlyList<CatalogItem> TimeZones { get; } =
        [.. generator.TimeOffsets.Values.Select(x => new CatalogItem(x.Id, x.DisplayName))];

    /// <summary>Editions upstream marks as visible.</summary>
    public IReadOnlyList<CatalogItem> WindowsEditions { get; } =
        Sorted(generator.WindowsEditions.Values.Where(x => x.Visible).Select(x => new CatalogItem(x.Id, x.DisplayName)));

    public IReadOnlyList<CatalogItem> Bloatwares { get; } =
        Sorted(generator.Bloatwares.Values.Select(x => new CatalogItem(x.Id, x.DisplayName)));

    private static List<CatalogItem> Sorted(IEnumerable<CatalogItem> items)
    {
        return [.. items.OrderBy(x => x.DisplayName, StringComparer.OrdinalIgnoreCase).ThenBy(x => x.Id, StringComparer.Ordinal)];
    }
}
