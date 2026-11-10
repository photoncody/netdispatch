using System.Text;
using System.Xml;
using Netdispatch.UnattendGen;
using Netdispatch.UnattendGen.PostProcessing;

namespace Netdispatch.UnattendGen.Tests;

public class GenerationServiceTests
{
    private static string GenerateText(HostConfig config) => Encoding.ASCII.GetString(Shared.Generation.Generate(config).Xml);

    private static readonly AccountOptions OneAdmin = new()
    {
        Mode = AccountMode.Unattended,
        Users = [new UserAccount { Name = "labadmin", Password = "secret" }],
        AutoLogon = AutoLogonMode.FirstAdministrator,
    };

    [Fact]
    public void ValidationErrors_AreThrownTogether()
    {
        var ex = Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(new HostConfig { ProcessorArchitectures = [], Bloatware = ["x"] }));

        Assert.Equal(2, ex.Errors.Count);
    }

    [Theory]
    [InlineData("has space")]
    [InlineData("WAYTOOLONGCOMPUTERNAME")]
    [InlineData("12345")]
    public void InvalidComputerName_IsReportedFromUpstream(string name)
    {
        var config = new HostConfig { ComputerName = new() { Mode = ComputerNameMode.Custom, Name = name } };

        var ex = Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(config));

        Assert.Contains(ex.Errors, e => e.Contains("Computer name"));
    }

    [Fact]
    public void AccountsWithoutAdministrator_AreReportedFromUpstream()
    {
        var config = new HostConfig { Accounts = OneAdmin with { Users = [new UserAccount { Name = "user", Group = AccountGroup.Users }] } };

        var ex = Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(config));

        Assert.Contains(ex.Errors, e => e.Contains("administrator"));
    }

    [Fact]
    public void MalformedProductKey_IsReportedFromUpstream()
    {
        var config = new HostConfig { Setup = new() { ActivationKey = "not-a-key" } };

        var ex = Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(config));

        Assert.Contains(ex.Errors, e => e.Contains("ill-formed"));
    }

    [Fact]
    public void Passwords_ProduceAWarning()
    {
        Assert.Empty(Shared.Generation.Generate(new HostConfig()).Warnings);
        Assert.Single(Shared.Generation.Generate(new HostConfig { Accounts = OneAdmin }).Warnings);
        Assert.Equal(2, Shared.Generation.Generate(new HostConfig { Accounts = OneAdmin, KeepSensitiveFiles = true }).Warnings.Count);
    }

    [Fact]
    public void DeletingSensitiveFiles_RequiresAutoLogon()
    {
        var withoutAutoLogon = new HostConfig { Accounts = OneAdmin with { AutoLogon = AutoLogonMode.None } };

        var ex = Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(withoutAutoLogon));
        Assert.Contains(ex.Errors, e => e.Contains("sensitive files"));

        Shared.Generation.Generate(withoutAutoLogon with { KeepSensitiveFiles = true });
    }

    [Fact]
    public void ComputerNameAndAccounts_AppearInOutput()
    {
        string xml = GenerateText(new HostConfig
        {
            ComputerName = new() { Mode = ComputerNameMode.Custom, Name = "LAB-PC1" },
            Accounts = OneAdmin,
        });

        Assert.Contains("<ComputerName>LAB-PC1</ComputerName>", xml);
        Assert.Contains("<Name>labadmin</Name>", xml);
    }

    [Fact]
    public void ScriptedSetup_EmbedsDiskpartScript()
    {
        string xml = GenerateText(new HostConfig
        {
            Setup = new()
            {
                Mode = SetupMode.Scripted,
                Scripted = new() { Partitioning = new() { Mode = PartitionMode.Unattended, Layout = PartitionLayout.Gpt } },
            },
        });

        Assert.Contains("CONVERT GPT", xml);
        Assert.Contains("SELECT DISK=0", xml);
    }

    [Fact]
    public void RequirementBypass_FollowsTheSetting()
    {
        string withBypass = GenerateText(new HostConfig());
        string withoutBypass = GenerateText(new HostConfig { Setup = new() { Standard = new() { BypassRequirementsCheck = false } } });

        Assert.Contains("BypassTPMCheck", withBypass);
        Assert.DoesNotContain("BypassTPMCheck", withoutBypass);
    }

    [Fact]
    public void CustomScript_IsEmbedded()
    {
        string xml = GenerateText(new HostConfig
        {
            Scripts = new() { Items = [new CustomScript { Phase = ScriptPhase.System, Type = ScriptType.Cmd, Content = "echo marker-1234" }] },
        });

        Assert.Contains("echo marker-1234", xml);
    }

    [Fact]
    public void PostProcessors_RunOnlyWhenTheyApply()
    {
        var applies = new MarkerPostProcessor(applies: true, "applies");
        var skipped = new MarkerPostProcessor(applies: false, "skipped");
        var service = new GenerationService(Shared.Generator, [applies, skipped]);

        string xml = Encoding.ASCII.GetString(service.Generate(new HostConfig()).Xml);

        Assert.Contains("<!--applies-->", xml);
        Assert.DoesNotContain("<!--skipped-->", xml);
    }

    [Fact]
    public void DuplicateComponents_FromAPostProcessor_AreCaught()
    {
        var service = new GenerationService(Shared.Generator, [new DuplicatingPostProcessor()]);

        Assert.Throws<InvalidOperationException>(() => service.Generate(new HostConfig()));
    }

    private sealed class MarkerPostProcessor(bool applies, string marker) : IXmlPostProcessor
    {
        public bool AppliesTo(HostConfig config) => applies;

        public void Apply(XmlDocument doc, HostConfig config) => doc.DocumentElement!.AppendChild(doc.CreateComment(marker));
    }

    private sealed class DuplicatingPostProcessor : IXmlPostProcessor
    {
        public bool AppliesTo(HostConfig config) => true;

        public void Apply(XmlDocument doc, HostConfig config)
        {
            var ns = UnattendXml.CreateNamespaceManager(doc);
            var component = doc.SelectSingleNode("//u:component", ns)!;
            component.ParentNode!.AppendChild(component.CloneNode(deep: true));
        }
    }
}
