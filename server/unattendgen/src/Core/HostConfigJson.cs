using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Reads and writes HostConfig as JSON (the preset format). Parsing is strict: unknown
/// properties, nulls in non-nullable properties, missing required properties and numeric
/// enum values are all errors, so a typo in a preset fails loudly instead of being ignored.
/// </summary>
public static class HostConfigJson
{
    public static JsonSerializerOptions Options { get; } = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            WriteIndented = true,
            IndentSize = 2,
            NewLine = "\n",
            RespectNullableAnnotations = true,
            UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            TypeInfoResolver = new DefaultJsonTypeInfoResolver(),
        };
        options.Converters.Add(new JsonStringEnumConverter(namingPolicy: null, allowIntegerValues: false));
        options.MakeReadOnly();
        return options;
    }

    /// <exception cref="JsonException">The JSON is malformed or does not match the schema.</exception>
    public static HostConfig Deserialize(string json)
    {
        var config = JsonSerializer.Deserialize<HostConfig>(json, Options)
            ?? throw new JsonException("The settings are empty (null).");
        RejectNullElements(config);
        return config;
    }

    // RespectNullableAnnotations checks properties but not the elements of collections, so
    // "items": [null] would otherwise get through and crash later code.
    private static void RejectNullElements(HostConfig config)
    {
        Check(config.Bloatware, "bloatware");
        Check(config.Language.Locales, "language.locales");
        Check(config.Accounts.Users, "accounts.users");
        Check(config.Scripts.Items, "scripts.items");

        static void Check<T>(IReadOnlyList<T> items, string path)
        {
            for (int i = 0; i < items.Count; i++)
            {
                if (items[i] is null)
                {
                    throw new JsonException($"{path}[{i}] must not be null.");
                }
            }
        }
    }

    public static string Serialize(HostConfig config)
    {
        return JsonSerializer.Serialize(config, Options) + "\n";
    }
}
