using System.Text;
using System.Text.Json;
using Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

/// <summary>
/// The command line the netdispatch hub calls (server/app/unattend_generator.py). Kept in the
/// library so tests can run it with in-memory streams.
///
///   netdispatch-unattendgen generate   stdin: settings JSON (HostConfig)
///                                      stdout: {"xml": "...", "warnings": [...]}, exit 0
///                                           or {"errors": ["..."]}, exit 2
///   netdispatch-unattendgen schema     stdout: defaults, flags and the lists of choices
///
/// Exit 1 means a bug; the message goes to stderr.
/// </summary>
public static class CliCommands
{
    public const int ExitOk = 0;
    public const int ExitCrash = 1;
    public const int ExitInvalid = 2;
    public const int ExitUsage = 64;

    /// <summary>Settings larger than this are refused before parsing.</summary>
    public const int MaxInputBytes = 4 * 1024 * 1024;

    private static readonly JsonSerializerOptions Output = new(JsonSerializerDefaults.Web);

    public static int Run(string[] args, Stream stdin, Stream stdout, TextWriter stderr)
    {
        try
        {
            return args switch
            {
                ["generate"] => Generate(stdin, stdout),
                ["schema"] => Schema(stdout),
                _ => Usage(stderr),
            };
        }
        catch (Exception ex)
        {
            stderr.WriteLine(ex.ToString());
            return ExitCrash;
        }
    }

    private static int Usage(TextWriter stderr)
    {
        stderr.WriteLine("usage: netdispatch-unattendgen generate|schema");
        return ExitUsage;
    }

    private static int Generate(Stream stdin, Stream stdout)
    {
        string json;
        using (var buffer = new MemoryStream())
        {
            var chunk = new byte[81920];
            int read;
            while ((read = stdin.Read(chunk, 0, chunk.Length)) > 0)
            {
                if (buffer.Length + read > MaxInputBytes)
                {
                    return Invalid(stdout, [$"The settings are larger than {MaxInputBytes} bytes."]);
                }
                buffer.Write(chunk, 0, read);
            }
            json = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true)
                .GetString(buffer.GetBuffer(), 0, (int)buffer.Length);
        }

        HostConfig config;
        try
        {
            config = HostConfigJson.Deserialize(json);
        }
        catch (JsonException ex)
        {
            return Invalid(stdout, [ex.Message]);
        }

        GenerationResult result;
        try
        {
            result = Shared.Generation.Generate(config);
        }
        catch (InvalidHostConfigException ex)
        {
            return Invalid(stdout, ex.Errors);
        }
        catch (ArgumentException ex)
        {
            // Upstream throws ArgumentException for some content it cannot embed (for example in scripts).
            return Invalid(stdout, [ex.Message]);
        }

        // Upstream serializes to ASCII (non-ASCII characters become character references).
        Write(stdout, new { xml = Encoding.ASCII.GetString(result.Xml), warnings = result.Warnings });
        return ExitOk;
    }

    private static int Schema(Stream stdout)
    {
        var catalog = new UpstreamCatalog(Shared.Generator);
        Write(stdout, new
        {
            schemaVersion = HostConfig.CurrentSchemaVersion,
            upstreamCommit = BuildInfo.UpstreamCommit,
            hostnameToken = ComputerNameOptions.HostnameToken,
            defaults = JsonDocument.Parse(HostConfigJson.Serialize(new HostConfig())).RootElement,
            flags = UpstreamFlags.Defaults.Select(f => new { name = f.Key, @default = f.Value }),
            imageLanguages = catalog.ImageLanguages,
            userLocales = catalog.UserLocales,
            keyboards = catalog.KeyboardIdentifiers,
            geoLocations = catalog.GeoLocations,
            timeZones = catalog.TimeZones,
            windowsEditions = catalog.WindowsEditions,
            bloatware = catalog.Bloatwares,
        });
        return ExitOk;
    }

    private static int Invalid(Stream stdout, IReadOnlyList<string> errors)
    {
        Write(stdout, new { errors });
        return ExitInvalid;
    }

    private static void Write<T>(Stream stdout, T value)
    {
        JsonSerializer.Serialize(stdout, value, Output);
        stdout.Flush();
    }

    private static class Shared
    {
        // Creating the generator parses upstream's JSON resources; do it once per process.
        public static UnattendGenerator Generator { get; } = new();

        public static GenerationService Generation { get; } = new(Generator);
    }
}
