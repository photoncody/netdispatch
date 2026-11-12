using System.Text;
using System.Text.Json;
using Netdispatch.UnattendGen;

namespace Netdispatch.UnattendGen.Tests;

public class CliCommandsTests
{
    private static (int Exit, JsonElement Output, string Stderr) Run(string command, string stdin = "")
    {
        using var input = new MemoryStream(Encoding.UTF8.GetBytes(stdin));
        using var output = new MemoryStream();
        using var stderr = new StringWriter();

        int exit = CliCommands.Run([command], input, output, stderr);

        var json = output.Length > 0 ? JsonDocument.Parse(output.ToArray()).RootElement : default;
        return (exit, json, stderr.ToString());
    }

    [Fact]
    public void Generate_WritesXmlAndWarnings()
    {
        var (exit, output, _) = Run("generate", "{}");

        Assert.Equal(CliCommands.ExitOk, exit);
        Assert.StartsWith("<?xml", output.GetProperty("xml").GetString());
        Assert.Equal(JsonValueKind.Array, output.GetProperty("warnings").ValueKind);
    }

    [Fact]
    public void Generate_MatchesGenerationService()
    {
        var (_, output, _) = Run("generate", "{}");

        string expected = Encoding.ASCII.GetString(Shared.Generation.Generate(new HostConfig()).Xml);
        Assert.Equal(expected, output.GetProperty("xml").GetString());
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("""{ "bogus": 1 }""")]
    [InlineData("""{ "computerName": { "mode": "Custom", "name": "has space" } }""")]
    [InlineData("""{ "bloatware": [ "RemoveNothing" ] }""")]
    public void Generate_ReportsInvalidSettings(string json)
    {
        var (exit, output, _) = Run("generate", json);

        Assert.Equal(CliCommands.ExitInvalid, exit);
        Assert.NotEmpty(output.GetProperty("errors").EnumerateArray());
    }

    [Fact]
    public void Generate_RefusesOversizedInput()
    {
        var (exit, output, _) = Run("generate", new string(' ', CliCommands.MaxInputBytes + 1));

        Assert.Equal(CliCommands.ExitInvalid, exit);
        Assert.Contains("larger than", output.GetProperty("errors")[0].GetString());
    }

    [Fact]
    public void Generate_HostnameMode_WritesToken()
    {
        var (exit, output, _) = Run("generate", """{ "computerName": { "mode": "Hostname" } }""");

        Assert.Equal(CliCommands.ExitOk, exit);
        Assert.Contains($"<ComputerName>{ComputerNameOptions.HostnameToken}</ComputerName>", output.GetProperty("xml").GetString());
    }

    [Fact]
    public void Schema_ListsDefaultsFlagsAndChoices()
    {
        var (exit, output, _) = Run("schema");

        Assert.Equal(CliCommands.ExitOk, exit);
        Assert.Equal(HostConfig.CurrentSchemaVersion, output.GetProperty("schemaVersion").GetInt32());
        Assert.Equal(BuildInfo.UpstreamCommit, output.GetProperty("upstreamCommit").GetString());
        Assert.Equal(ComputerNameOptions.HostnameToken, output.GetProperty("hostnameToken").GetString());
        Assert.Equal("Random", output.GetProperty("defaults").GetProperty("computerName").GetProperty("mode").GetString());

        var flags = output.GetProperty("flags").EnumerateArray().Select(f => f.GetProperty("name").GetString()).ToList();
        Assert.Contains("DisableUac", flags);
        Assert.DoesNotContain("KeepSensitiveFiles", flags);

        foreach (var list in new[] { "imageLanguages", "userLocales", "keyboards", "geoLocations", "timeZones", "windowsEditions", "bloatware", "desktopIcons", "startFolders", "components", "visualEffects", "stickyKeys" })
        {
            Assert.NotEmpty(output.GetProperty(list).EnumerateArray());
        }
        var enUs = output.GetProperty("userLocales").EnumerateArray().Single(l => l.GetProperty("id").GetString() == "en-US");
        Assert.Equal("00000409", enUs.GetProperty("keyboard").GetString());
    }

    [Fact]
    public void UnknownCommand_PrintsUsage()
    {
        var (exit, _, stderr) = Run("frobnicate");

        Assert.Equal(CliCommands.ExitUsage, exit);
        Assert.Contains("usage", stderr);
    }
}
