using System.Xml;
using System.Xml.Schema;
using Schneegans.Unattend;
using Netdispatch.UnattendGen.PostProcessing;

namespace Netdispatch.UnattendGen;

public sealed record GenerationResult(byte[] Xml, IReadOnlyList<string> Warnings);

/// <summary>The configuration cannot be turned into an answer file. Errors are meant for the user.</summary>
public sealed class InvalidHostConfigException(IReadOnlyList<string> errors)
    : Exception("The configuration is not valid: " + string.Join(" ", errors))
{
    public IReadOnlyList<string> Errors { get; } = errors;
}

/// <summary>
/// The one entry point for producing an answer file. The web UI, the API, the CLI and the
/// tests all call this, so they cannot drift apart.
///
/// Pipeline: validate → map to upstream Configuration → upstream GenerateXml →
/// post-processors → duplicate-component check → upstream Serialize.
/// </summary>
public sealed class GenerationService
{
    private readonly HostConfigValidator validator;
    private readonly ConfigurationMapper mapper;
    private readonly UnattendGenerator generator;
    private readonly IReadOnlyList<IXmlPostProcessor> postProcessors;

    public GenerationService(UnattendGenerator generator)
        : this(generator, PostProcessorChain.Default)
    {
    }

    public GenerationService(UnattendGenerator generator, IReadOnlyList<IXmlPostProcessor> postProcessors)
    {
        this.generator = generator;
        this.postProcessors = postProcessors;
        validator = new HostConfigValidator(generator);
        mapper = new ConfigurationMapper(generator);
    }

    /// <summary>
    /// Our own validation rules only. Some problems (for example an invalid computer name) are
    /// detected by upstream and only reported by Generate.
    /// </summary>
    public IReadOnlyList<string> Validate(HostConfig config)
    {
        return validator.Validate(config);
    }

    /// <exception cref="InvalidHostConfigException">The configuration is not valid.</exception>
    public GenerationResult Generate(HostConfig config)
    {
        var errors = validator.Validate(config);
        if (errors.Count > 0)
        {
            throw new InvalidHostConfigException(errors);
        }

        XmlDocument doc;
        try
        {
            doc = generator.GenerateXml(mapper.Map(config));
        }
        catch (ConfigurationException ex)
        {
            throw new InvalidHostConfigException([ex.Message]);
        }
        catch (XmlSchemaException ex)
        {
            throw new InvalidHostConfigException([$"Upstream produced XML that does not match the unattend schema: {ex.Message}"]);
        }

        foreach (var postProcessor in postProcessors)
        {
            if (postProcessor.AppliesTo(config))
            {
                postProcessor.Apply(doc, config);
            }
        }

        var duplicates = UnattendXml.FindDuplicateComponents(doc);
        if (duplicates.Count > 0)
        {
            // A post-processor appended a component instead of replacing it. This is a bug, not a user error.
            throw new InvalidOperationException($"Generated XML contains duplicate components: {string.Join(", ", duplicates)}.");
        }

        return new GenerationResult(UnattendGenerator.Serialize(doc), CollectWarnings(config));
    }

    private static List<string> CollectWarnings(HostConfig config)
    {
        var warnings = new List<string>();

        var accounts = config.Accounts;
        bool hasPasswords = accounts.Mode == AccountMode.Unattended
            && (accounts.Users.Any(u => u.Password.Length > 0) || !string.IsNullOrEmpty(accounts.BuiltinAdministratorPassword));
        if (hasPasswords)
        {
            warnings.Add(accounts.ObscurePasswords
                ? "The answer file contains account passwords. They are only base64-encoded, not encrypted; anyone who can read the file can recover them."
                : "The answer file contains account passwords in plain text.");
            if (config.KeepSensitiveFiles)
            {
                warnings.Add(@"Sensitive files are kept: the answer file with these passwords stays in C:\Windows\Panther on the installed system.");
            }
        }

        return warnings;
    }
}
