using System.Text.RegularExpressions;
using System.Xml;

namespace Netdispatch.UnattendGen.PostProcessing;

/// <summary>
/// Helpers for working with the unattend namespace in post-processors and tests.
/// </summary>
public static partial class UnattendXml
{
    public const string UnattendNamespace = "urn:schemas-microsoft-com:unattend";

    public const string WcmNamespace = "http://schemas.microsoft.com/WMIConfig/2002/State";

    // Attribute values that upstream writes on every component (see upstream Util.GetOrCreateElement).
    private const string PublicKeyToken = "31bf3856ad364e35";
    private const string Language = "neutral";
    private const string VersionScope = "nonSxS";

    public static XmlNamespaceManager CreateNamespaceManager(XmlDocument doc)
    {
        var ns = new XmlNamespaceManager(doc.NameTable);
        ns.AddNamespace("u", UnattendNamespace);
        ns.AddNamespace("wcm", WcmNamespace);
        return ns;
    }

    /// <summary>
    /// The processor architectures the document's components are written for, in document order.
    /// Upstream writes one copy of each component per selected architecture.
    /// </summary>
    public static IReadOnlyList<string> ProcessorArchitectures(XmlDocument doc)
    {
        var ns = CreateNamespaceManager(doc);
        return [.. doc.SelectNodes("/u:unattend/u:settings/u:component/@processorArchitecture", ns)!
            .Cast<XmlAttribute>()
            .Select(a => a.Value)
            .Distinct(StringComparer.Ordinal)];
    }

    /// <summary>
    /// Returns the component with the given name and architecture in the given pass, creating it
    /// (with the same attributes upstream uses) if it does not exist yet. Throws if the document
    /// already contains it more than once.
    /// </summary>
    public static XmlElement FindOrCreateComponent(XmlDocument doc, string pass, string componentName, string processorArchitecture)
    {
        CheckToken(pass, nameof(pass));
        CheckToken(componentName, nameof(componentName));
        CheckToken(processorArchitecture, nameof(processorArchitecture));

        var ns = CreateNamespaceManager(doc);
        var settings = (XmlElement?)doc.SelectSingleNode($"/u:unattend/u:settings[@pass='{pass}']", ns)
            ?? throw new InvalidOperationException($"The document has no <settings pass=\"{pass}\"> element.");

        var matches = settings
            .SelectNodes($"u:component[@name='{componentName}' and @processorArchitecture='{processorArchitecture}']", ns)!
            .Cast<XmlElement>()
            .ToList();

        switch (matches.Count)
        {
            case 0:
                var component = doc.CreateElement("component", UnattendNamespace);
                component.SetAttribute("name", componentName);
                component.SetAttribute("processorArchitecture", processorArchitecture);
                component.SetAttribute("publicKeyToken", PublicKeyToken);
                component.SetAttribute("language", Language);
                component.SetAttribute("versionScope", VersionScope);
                settings.AppendChild(component);
                return component;
            case 1:
                return matches[0];
            default:
                throw new InvalidOperationException($"Component '{componentName}' ({processorArchitecture}) occurs {matches.Count} times in pass '{pass}'.");
        }
    }

    /// <summary>
    /// Lists every (pass, component, architecture) combination that occurs more than once.
    /// A non-empty result means a post-processor appended where it should have replaced.
    /// </summary>
    public static IReadOnlyList<string> FindDuplicateComponents(XmlDocument doc)
    {
        var ns = CreateNamespaceManager(doc);
        return [.. doc.SelectNodes("/u:unattend/u:settings/u:component", ns)!
            .Cast<XmlElement>()
            .GroupBy(c => $"{((XmlElement)c.ParentNode!).GetAttribute("pass")}/{c.GetAttribute("name")}/{c.GetAttribute("processorArchitecture")}")
            .Where(g => g.Count() > 1)
            .Select(g => g.Key)];
    }

    private static void CheckToken(string value, string parameter)
    {
        // These values are interpolated into XPath expressions, so only allow plain names.
        if (!TokenPattern().IsMatch(value))
        {
            throw new ArgumentException($"'{value}' is not a valid name.", parameter);
        }
    }

    [GeneratedRegex("^[A-Za-z0-9-]+$")]
    private static partial Regex TokenPattern();
}
