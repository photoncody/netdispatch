using System.Text.Json;
using Netdispatch.UnattendGen;

namespace Netdispatch.UnattendGen.Tests;

public class HostConfigJsonTests
{
    [Theory]
    [MemberData(nameof(Shared.PresetNames), MemberType = typeof(Shared))]
    public void Preset_RoundTripsThroughJson(string name)
    {
        var config = Presets.Load(name);

        string once = HostConfigJson.Serialize(config);
        string twice = HostConfigJson.Serialize(HostConfigJson.Deserialize(once));

        Assert.Equal(once, twice);
    }

    [Fact]
    public void EmptyObject_GivesDefaults()
    {
        var config = HostConfigJson.Deserialize("{}");

        Assert.Equal(HostConfigJson.Serialize(new HostConfig()), HostConfigJson.Serialize(config));
    }

    [Theory]
    [InlineData("""{ "bogus": true }""")]
    [InlineData("""{ "language": { "mode": "Unattended", "imagelanguage": "en-US", "bogus": 1 } }""")]
    public void UnknownProperty_IsRejected(string json)
    {
        Assert.Throws<JsonException>(() => HostConfigJson.Deserialize(json));
    }

    [Fact]
    public void NullForNonNullableProperty_IsRejected()
    {
        Assert.Throws<JsonException>(() => HostConfigJson.Deserialize("""{ "language": null }"""));
    }

    [Theory]
    [InlineData("""{ "bloatware": [ null ] }""", "bloatware[0]")]
    [InlineData("""{ "language": { "locales": [ null ] } }""", "language.locales[0]")]
    [InlineData("""{ "accounts": { "users": [ { "name": "a" }, null ] } }""", "accounts.users[1]")]
    [InlineData("""{ "scripts": { "items": [ null ] } }""", "scripts.items[0]")]
    public void NullCollectionElement_IsRejected(string json, string path)
    {
        var ex = Assert.Throws<JsonException>(() => HostConfigJson.Deserialize(json));

        Assert.Contains(path, ex.Message);
    }

    [Fact]
    public void NumericEnumValue_IsRejected()
    {
        Assert.Throws<JsonException>(() => HostConfigJson.Deserialize("""{ "expressSettings": 1 }"""));
    }

    [Fact]
    public void MissingRequiredProperty_IsRejected()
    {
        string json = """{ "language": { "mode": "Unattended", "locales": [ { "locale": "en-US" } ] } }""";

        Assert.Throws<JsonException>(() => HostConfigJson.Deserialize(json));
    }

    [Fact]
    public void EnumsAreWrittenByName()
    {
        string json = HostConfigJson.Serialize(new HostConfig { ExpressSettings = ExpressSettingsMode.EnableAll });

        Assert.Contains("\"expressSettings\": \"EnableAll\"", json);
    }
}
