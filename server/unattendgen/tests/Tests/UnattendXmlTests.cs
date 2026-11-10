using System.Xml;
using Schneegans.Unattend;
using Netdispatch.UnattendGen.PostProcessing;

namespace Netdispatch.UnattendGen.Tests;

public class UnattendXmlTests
{
    // Upstream's default output, as a fixed input for the helpers.
    private static XmlDocument UpstreamDefault() => Shared.Generator.GenerateXml(Configuration.Default);

    [Fact]
    public void ProcessorArchitectures_ReadsUpstreamOutput()
    {
        Assert.Equal(["amd64"], UnattendXml.ProcessorArchitectures(UpstreamDefault()));
    }

    [Fact]
    public void FindOrCreateComponent_ReturnsExistingComponent()
    {
        var doc = UpstreamDefault();
        var ns = UnattendXml.CreateNamespaceManager(doc);
        var existing = doc.SelectSingleNode("/u:unattend/u:settings[@pass='windowsPE']/u:component[@name='Microsoft-Windows-Setup']", ns);

        var found = UnattendXml.FindOrCreateComponent(doc, "windowsPE", "Microsoft-Windows-Setup", "amd64");

        Assert.Same(existing, found);
    }

    [Fact]
    public void FindOrCreateComponent_CreatesComponentWithUpstreamAttributes()
    {
        var doc = UpstreamDefault();
        var ns = UnattendXml.CreateNamespaceManager(doc);
        var upstreamMade = (XmlElement)doc.SelectSingleNode("/u:unattend/u:settings[@pass='windowsPE']/u:component", ns)!;

        var created = UnattendXml.FindOrCreateComponent(doc, "specialize", "Microsoft-Windows-ServerManager-SvrMgrNc", "amd64");

        Assert.Equal(UnattendXml.UnattendNamespace, created.NamespaceURI);
        Assert.Equal("specialize", ((XmlElement)created.ParentNode!).GetAttribute("pass"));
        Assert.Equal("Microsoft-Windows-ServerManager-SvrMgrNc", created.GetAttribute("name"));
        foreach (string attribute in new[] { "processorArchitecture", "publicKeyToken", "language", "versionScope" })
        {
            Assert.Equal(upstreamMade.GetAttribute(attribute), created.GetAttribute(attribute));
        }
        Assert.Same(created, UnattendXml.FindOrCreateComponent(doc, "specialize", "Microsoft-Windows-ServerManager-SvrMgrNc", "amd64"));
    }

    [Fact]
    public void FindOrCreateComponent_ThrowsOnDuplicates()
    {
        var doc = UpstreamDefault();
        var component = UnattendXml.FindOrCreateComponent(doc, "windowsPE", "Microsoft-Windows-Setup", "amd64");
        component.ParentNode!.AppendChild(component.CloneNode(deep: true));

        Assert.Throws<InvalidOperationException>(() => UnattendXml.FindOrCreateComponent(doc, "windowsPE", "Microsoft-Windows-Setup", "amd64"));
    }

    [Theory]
    [InlineData("windowsPE']/../x", "Microsoft-Windows-Setup")]
    [InlineData("windowsPE", "Microsoft-Windows-Setup' or '1'='1")]
    public void FindOrCreateComponent_RejectsNamesThatAreNotPlainTokens(string pass, string name)
    {
        Assert.Throws<ArgumentException>(() => UnattendXml.FindOrCreateComponent(UpstreamDefault(), pass, name, "amd64"));
    }

    [Fact]
    public void FindDuplicateComponents_IsEmptyForUpstreamOutput_AndFindsDuplicates()
    {
        var doc = UpstreamDefault();
        Assert.Empty(UnattendXml.FindDuplicateComponents(doc));

        var component = UnattendXml.FindOrCreateComponent(doc, "windowsPE", "Microsoft-Windows-Setup", "amd64");
        component.ParentNode!.AppendChild(component.CloneNode(deep: true));

        Assert.Equal(["windowsPE/Microsoft-Windows-Setup/amd64"], UnattendXml.FindDuplicateComponents(doc));
    }
}
