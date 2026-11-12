using System.Text;
using System.Text.Json;
using System.Xml;
using Netdispatch.UnattendGen;
using Netdispatch.UnattendGen.PostProcessing;

namespace Netdispatch.UnattendGen.Tests;

/// <summary>Wi-Fi profiles, components, AppLocker, appearance, Start and taskbar layout, keyboard, auditing.</summary>
public class LongTailTests
{
    private const string WlanNamespace = "http://www.microsoft.com/networking/WLAN/profile/v1";

    private static string GenerateText(HostConfig config) => Encoding.ASCII.GetString(Shared.Generation.Generate(config).Xml);

    private static IReadOnlyList<string> Validate(HostConfig config) => Shared.Generation.Validate(config);

    private static IReadOnlyList<string> GenerateErrors(HostConfig config)
    {
        return Assert.Throws<InvalidHostConfigException>(() => Shared.Generation.Generate(config)).Errors;
    }

    /// <summary>The text of a file upstream embeds in the answer file, by the path it is extracted to.</summary>
    private static string EmbeddedFile(string xml, string pathEnding)
    {
        var doc = new XmlDocument();
        doc.LoadXml(xml);
        foreach (XmlElement file in doc.GetElementsByTagName("File"))
        {
            if (file.GetAttribute("path").EndsWith(pathEnding, StringComparison.OrdinalIgnoreCase))
            {
                return file.InnerText;
            }
        }
        throw new Xunit.Sdk.XunitException($"No embedded file ends with '{pathEnding}'.");
    }

    private static HostConfig WithWifi(WifiProfile profile) => new() { Wifi = new() { Mode = WifiMode.Profile, Profile = profile } };

    // ---- Wi-Fi ----

    [Theory]
    [InlineData(WifiAuthentication.Open, "", "open", "none")]
    [InlineData(WifiAuthentication.Wpa2, "correct horse", "WPA2PSK", "AES")]
    [InlineData(WifiAuthentication.Wpa3, "correct horse", "WPA3SAE", "AES")]
    public void WifiProfile_IsEmbeddedAndAdded(WifiAuthentication authentication, string password, string expectedAuth, string expectedEncryption)
    {
        string xml = GenerateText(WithWifi(new WifiProfile { Ssid = "Lab Net", Authentication = authentication, Password = password, Hidden = true }));

        Assert.DoesNotContain("HideWirelessSetupInOOBE", xml);
        Assert.Contains("netsh.exe wlan add profile", xml);
        Assert.Contains(@"netsh.exe wlan connect name=""Lab Net""", xml);

        var profile = new XmlDocument();
        profile.LoadXml(EmbeddedFile(xml, "Wifi.xml"));
        var ns = new XmlNamespaceManager(profile.NameTable);
        ns.AddNamespace("w", WlanNamespace);
        Assert.Equal("Lab Net", profile.SelectSingleNode("/w:WLANProfile/w:SSIDConfig/w:SSID/w:name", ns)!.InnerText);
        Assert.Equal("true", profile.SelectSingleNode("/w:WLANProfile/w:SSIDConfig/w:nonBroadcast", ns)!.InnerText);
        Assert.Equal(expectedAuth, profile.SelectSingleNode("//w:authEncryption/w:authentication", ns)!.InnerText);
        Assert.Equal(expectedEncryption, profile.SelectSingleNode("//w:authEncryption/w:encryption", ns)!.InnerText);
        Assert.Equal(password.Length > 0 ? password : null, profile.SelectSingleNode("//w:sharedKey/w:keyMaterial", ns)?.InnerText);
    }

    [Fact]
    public void WifiProfile_WithoutAutoConnect_IsOnlyAdded()
    {
        string xml = GenerateText(WithWifi(new WifiProfile { Ssid = "Lab", Password = "12345678", ConnectAutomatically = false }));

        Assert.Contains("netsh.exe wlan add profile", xml);
        Assert.DoesNotContain("netsh.exe wlan connect", xml);
    }

    [Fact]
    public void WifiPassword_ProducesAWarning()
    {
        var warnings = Shared.Generation.Generate(WithWifi(new WifiProfile { Ssid = "Lab", Password = "12345678" })).Warnings;

        Assert.Contains(warnings, w => w.Contains("Wi-Fi password"));
        Assert.Empty(Shared.Generation.Generate(WithWifi(new WifiProfile { Ssid = "Lab", Authentication = WifiAuthentication.Open })).Warnings);
    }

    [Theory]
    [InlineData("", WifiAuthentication.Wpa2, "12345678", "wifi.profile.ssid is required")]
    [InlineData("123456789012345678901234567890123", WifiAuthentication.Wpa2, "12345678", "32 bytes")]
    [InlineData("a\"b", WifiAuthentication.Wpa2, "12345678", "double quotes")]
    [InlineData("Lab", WifiAuthentication.Wpa2, "short", "8 to 63")]
    [InlineData("Lab", WifiAuthentication.Wpa3, "", "8 to 63")]
    [InlineData("Lab", WifiAuthentication.Wpa2, "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "64 hex digits")]
    [InlineData("Lab", WifiAuthentication.Open, "12345678", "only used")]
    public void WifiProfile_IsChecked(string ssid, WifiAuthentication authentication, string password, string expected)
    {
        var errors = Validate(WithWifi(new WifiProfile { Ssid = ssid, Authentication = authentication, Password = password }));

        Assert.Contains(errors, e => e.Contains(expected));
    }

    [Fact]
    public void WifiFields_MustMatchTheMode()
    {
        Assert.Contains(Validate(new HostConfig { Wifi = new() { Mode = WifiMode.Profile } }), e => e.Contains("wifi.profile is required"));
        Assert.Contains(Validate(new HostConfig { Wifi = new() { Mode = WifiMode.Skip, Profile = new WifiProfile { Ssid = "x" } } }), e => e.Contains("wifi.profile is only used"));
        Assert.Contains(Validate(new HostConfig { Wifi = new() { Mode = WifiMode.ProfileXml } }), e => e.Contains("wifi.profileXml is required"));
        Assert.Contains(Validate(new HostConfig { Wifi = new() { Mode = WifiMode.Skip, ProfileXml = "<x/>" } }), e => e.Contains("wifi.profileXml is only used"));
    }

    [Fact]
    public void WifiProfileXml_IsCheckedAgainstTheSchema_AndEmbedded()
    {
        // A profile upstream itself wrote is one its schema accepts.
        string exported = EmbeddedFile(GenerateText(WithWifi(new WifiProfile { Ssid = "Exported", Password = "12345678" })), "Wifi.xml");

        string xml = GenerateText(new HostConfig { Wifi = new() { Mode = WifiMode.ProfileXml, ProfileXml = exported } });
        Assert.Contains(@"netsh.exe wlan connect name=""Exported""", xml);
        Assert.Contains("12345678", EmbeddedFile(xml, "Wifi.xml"));

        Assert.Contains(GenerateErrors(new HostConfig { Wifi = new() { Mode = WifiMode.ProfileXml, ProfileXml = "<WLANProfile/>" } }), e => e.Contains("WLAN profile XML is invalid"));
        Assert.Contains(GenerateErrors(new HostConfig { Wifi = new() { Mode = WifiMode.ProfileXml, ProfileXml = "not xml" } }), e => e.Contains("WLAN profile XML is invalid"));
    }

    // ---- Components ----

    private const string Rdp = "Microsoft-Windows-TerminalServices-LocalSessionManager";

    [Fact]
    public void Component_IsWrittenIntoItsPass()
    {
        string xml = GenerateText(new HostConfig
        {
            Components = [new CustomComponent { Component = Rdp.ToLowerInvariant(), Pass = "specialize", Xml = "<fDenyTSConnections>false</fDenyTSConnections>" }],
        });

        var doc = new XmlDocument();
        doc.LoadXml(xml);
        var ns = new XmlNamespaceManager(doc.NameTable);
        ns.AddNamespace("u", UnattendXml.UnattendNamespace);
        var value = doc.SelectSingleNode($"/u:unattend/u:settings[@pass='specialize']/u:component[@name='{Rdp}']/u:fDenyTSConnections", ns);
        Assert.Equal("false", value?.InnerText);
    }

    [Fact]
    public void Component_ReplacesWhatUpstreamWroteForIt()
    {
        string xml = GenerateText(new HostConfig
        {
            Components = [new CustomComponent { Component = "Microsoft-Windows-Shell-Setup", Pass = "specialize", Xml = "<RegisteredOwner>Lab</RegisteredOwner>" }],
        });

        var doc = new XmlDocument();
        doc.LoadXml(xml);
        Assert.Empty(UnattendXml.FindDuplicateComponents(doc));
        Assert.Contains("<RegisteredOwner>Lab</RegisteredOwner>", xml);
    }

    [Fact]
    public void Components_AreChecked()
    {
        var errors = Validate(new HostConfig
        {
            Components =
            [
                new CustomComponent { Component = "Not-A-Component", Pass = "specialize", Xml = "<a/>" },
                new CustomComponent { Component = Rdp, Pass = "Specialize", Xml = "<a/>" },
                new CustomComponent { Component = Rdp, Pass = "windowsPE", Xml = " " },
                new CustomComponent { Component = Rdp, Pass = "specialize", Xml = "<a/>" },
                new CustomComponent { Component = Rdp.ToUpperInvariant(), Pass = "specialize", Xml = "<a/>" },
            ],
        });

        Assert.Contains(errors, e => e.Contains("components[0].component 'Not-A-Component' is not a known upstream value"));
        Assert.Contains(errors, e => e.Contains("components[1].pass 'Specialize' is not a configuration pass"));
        Assert.Contains(errors, e => e.Contains("components[2]") && e.Contains("cannot be used in the 'windowsPE' pass"));
        Assert.Contains(errors, e => e.Contains("components[2].xml must not be empty"));
        Assert.Contains(errors, e => e.Contains("more than once"));
        Assert.Equal(5, errors.Count);
    }

    [Fact]
    public void ComponentXml_MustBeWellFormed()
    {
        var errors = GenerateErrors(new HostConfig { Components = [new CustomComponent { Component = Rdp, Pass = "specialize", Xml = "<open>" }] });

        Assert.Contains(errors, e => e.Contains("not well-formed"));
    }

    [Theory]
    [InlineData(SetupMode.Scripted)]
    [InlineData(SetupMode.CustomScript)]
    public void WindowsPeComponents_AreRefusedWhenAScriptRunsThePeStage(SetupMode mode)
    {
        var config = new HostConfig
        {
            Setup = new() { Mode = mode, PeScript = mode == SetupMode.CustomScript ? "echo pe" : null },
            Components = [new CustomComponent { Component = "Microsoft-Windows-Setup", Pass = "windowsPE", Xml = "<UseConfigurationSet>false</UseConfigurationSet>" }],
        };

        Assert.Contains(GenerateErrors(config), e => e.Contains("windowsPE"));
        Assert.Contains("<UseConfigurationSet>false</UseConfigurationSet>", GenerateText(config with { Setup = new() }));
    }

    // ---- AppLocker, auditing ----

    private const string AppLockerPolicy = """
        <AppLockerPolicy Version="1">
          <RuleCollection Type="Exe" EnforcementMode="AuditOnly">
            <FilePathRule Id="921cc481-6e17-4653-8f75-050b80acca20" Name="All files" Description="" UserOrGroupSid="S-1-1-0" Action="Allow">
              <Conditions><FilePathCondition Path="*" /></Conditions>
            </FilePathRule>
          </RuleCollection>
        </AppLockerPolicy>
        """;

    [Fact]
    public void AppLockerPolicy_IsCheckedAndApplied()
    {
        string xml = GenerateText(new HostConfig { AppLockerPolicy = AppLockerPolicy });

        Assert.Contains("Set-AppLockerPolicy -XmlPolicy", xml);
        Assert.Contains("921cc481-6e17-4653-8f75-050b80acca20", EmbeddedFile(xml, "AppLockerPolicy.xml"));
        Assert.DoesNotContain("Set-AppLockerPolicy", GenerateText(new HostConfig()));

        Assert.Contains(GenerateErrors(new HostConfig { AppLockerPolicy = "<Nope/>" }), e => e.Contains("AppLocker policy XML is invalid"));
        Assert.Contains(Validate(new HostConfig { AppLockerPolicy = "  " }), e => e.Contains("appLockerPolicy is required"));
    }

    [Theory]
    [InlineData(ProcessAuditMode.Disabled, false, false)]
    [InlineData(ProcessAuditMode.Enabled, true, false)]
    [InlineData(ProcessAuditMode.EnabledWithCommandLine, true, true)]
    public void ProcessAudit_TurnsOnAuditingAndCommandLines(ProcessAuditMode mode, bool audit, bool commandLine)
    {
        string xml = GenerateText(new HostConfig { ProcessAudit = mode });

        Assert.Equal(audit, xml.Contains("auditpol.exe /set"));
        Assert.Equal(commandLine, xml.Contains("ProcessCreationIncludeCmdLine_Enabled"));
    }

    // ---- Keyboard ----

    [Fact]
    public void LockKeys_SetTheInitialStateAndIgnoredKeys()
    {
        string xml = GenerateText(new HostConfig
        {
            LockKeys = new() { NumLock = new() { On = true }, CapsLock = new() { Ignore = true } },
        });

        Assert.Contains("-Name 'InitialKeyboardIndicators' -Type 'String' -Value 2 -Force", xml);
        Assert.Contains("'Scancode Map'", xml);
        Assert.DoesNotContain("InitialKeyboardIndicators", GenerateText(new HostConfig()));
        Assert.DoesNotContain("Scancode Map", GenerateText(new HostConfig { LockKeys = new() }));
    }

    [Theory]
    [InlineData(StickyKeyMode.Default, null, null)]
    [InlineData(StickyKeyMode.Disabled, null, 10)]
    [InlineData(StickyKeyMode.Custom, "", 10)]
    [InlineData(StickyKeyMode.Custom, "HotKeyActive,Indicator", 46)]
    public void StickyKeys_WriteTheFlagsValue(StickyKeyMode mode, string? flags, int? value)
    {
        string xml = GenerateText(new HostConfig
        {
            StickyKeys = new() { Mode = mode, Flags = flags?.Split(',', StringSplitOptions.RemoveEmptyEntries) },
        });

        if (value is null)
        {
            Assert.DoesNotContain("StickyKeys", xml);
        }
        else
        {
            Assert.Contains($@"\StickyKeys"" /v Flags /t REG_SZ /d {value} /f", xml);
        }
    }

    [Fact]
    public void StickyKeyFlags_AreChecked()
    {
        Assert.Contains(Validate(new HostConfig { StickyKeys = new() { Mode = StickyKeyMode.Custom, Flags = ["Nope", "4"] } }), e => e.Contains("stickyKeys.flags[0] 'Nope'"));
        Assert.Contains(Validate(new HostConfig { StickyKeys = new() { Mode = StickyKeyMode.Custom, Flags = ["Nope", "4"] } }), e => e.Contains("stickyKeys.flags[1] '4'"));
        Assert.Contains(Validate(new HostConfig { StickyKeys = new() { Mode = StickyKeyMode.Custom, Flags = ["Indicator", "Indicator"] } }), e => e.Contains("more than once"));
        Assert.Contains(Validate(new HostConfig { StickyKeys = new() { Mode = StickyKeyMode.Disabled, Flags = [] } }), e => e.Contains("only used"));
    }

    // ---- Appearance ----

    [Fact]
    public void SolidWallpaper_WritesTheColour()
    {
        string xml = GenerateText(new HostConfig { Wallpaper = new() { Mode = WallpaperMode.SolidColor, Color = "#0a1B2c" } });

        Assert.Contains("Set-WallpaperColor -HtmlColor '#0A1B2C';", xml);
    }

    [Fact]
    public void WallpaperAndLockScreenScripts_AreEmbedded()
    {
        string xml = GenerateText(new HostConfig
        {
            Wallpaper = new() { Mode = WallpaperMode.Script, Script = "'wallpaper-marker'" },
            LockScreenScript = "'lockscreen-marker'",
        });

        Assert.Contains("wallpaper-marker", EmbeddedFile(xml, "GetWallpaper.ps1"));
        Assert.Contains("lockscreen-marker", EmbeddedFile(xml, "GetLockScreenImage.ps1"));
        Assert.Contains("Set-WallpaperImage -LiteralPath", xml);
        Assert.Contains("LockScreenImagePath", xml);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("red")]
    [InlineData("#12345")]
    [InlineData("#12345g")]
    [InlineData("0078D4F")]
    public void Colours_MustBeHashAndSixHexDigits(string? color)
    {
        Assert.Contains(Validate(new HostConfig { Wallpaper = new() { Mode = WallpaperMode.SolidColor, Color = color } }), e => e.Contains("wallpaper.color must be a colour"));
        if (color != null)
        {
            Assert.Contains(Validate(new HostConfig { Colors = new() { AccentColor = color } }), e => e.Contains("colors.accentColor must be a colour"));
        }
    }

    [Fact]
    public void WallpaperFields_MustMatchTheMode()
    {
        Assert.Contains(Validate(new HostConfig { Wallpaper = new() { Color = "#000000" } }), e => e.Contains("wallpaper.color is only used"));
        Assert.Contains(Validate(new HostConfig { Wallpaper = new() { Script = "x" } }), e => e.Contains("wallpaper.script is only used"));
        Assert.Contains(Validate(new HostConfig { Wallpaper = new() { Mode = WallpaperMode.Script } }), e => e.Contains("wallpaper.script is required"));
        Assert.Contains(Validate(new HostConfig { LockScreenScript = "" }), e => e.Contains("lockScreenScript is required"));
    }

    [Fact]
    public void Colours_WriteThemeAndAccent()
    {
        string xml = GenerateText(new HostConfig
        {
            Colors = new() { SystemTheme = ThemeMode.Dark, AppsTheme = ThemeMode.Light, Transparency = false, AccentOnStart = true, AccentOnBorders = true, AccentColor = "#ff8800" },
        });
        string script = EmbeddedFile(xml, "SetColorTheme.ps1");

        Assert.Contains("$lightThemeSystem = 0;", script);
        Assert.Contains("$lightThemeApps = 1;", script);
        Assert.Contains("$accentColorOnStart = 1;", script);
        Assert.Contains("$enableTransparency = 0;", script);
        Assert.Contains("$htmlAccentColor = '#FF8800';", script);
        Assert.Contains("/v ColorPrevalence /t REG_DWORD /d 1 /f", xml);
        Assert.DoesNotContain("SetColorTheme", GenerateText(new HostConfig()));
    }

    [Theory]
    [InlineData(VisualEffectMode.BestAppearance, 1)]
    [InlineData(VisualEffectMode.BestPerformance, 2)]
    public void VisualEffectPresets_SetEveryEffect(VisualEffectMode mode, int setting)
    {
        string xml = GenerateText(new HostConfig { VisualEffects = new() { Mode = mode } });

        Assert.Contains($"-Name 'VisualFXSetting' -Type 'DWord' -Value {setting} -Force", xml);
        foreach (string effect in UpstreamNames.Of<Schneegans.Unattend.Effect>())
        {
            Assert.Contains($@"\VisualEffects\{effect}"" -Name 'DefaultValue' -Value {(setting == 1 ? 1 : 0)} ", xml);
        }
    }

    [Fact]
    public void CustomVisualEffects_SetOnlyTheListedEffects()
    {
        string xml = GenerateText(new HostConfig
        {
            VisualEffects = new() { Mode = VisualEffectMode.Custom, Effects = new Dictionary<string, bool> { ["DragFullWindows"] = true, ["FontSmoothing"] = false } },
        });

        Assert.Contains("-Name 'VisualFXSetting' -Type 'DWord' -Value 3 -Force", xml);
        Assert.Contains(@"\VisualEffects\DragFullWindows"" -Name 'DefaultValue' -Value 1 ", xml);
        Assert.Contains(@"\VisualEffects\FontSmoothing"" -Name 'DefaultValue' -Value 0 ", xml);
        Assert.DoesNotContain(@"\VisualEffects\CursorShadow", xml);
    }

    [Fact]
    public void VisualEffects_AreChecked()
    {
        Assert.Contains(Validate(new HostConfig { VisualEffects = new() { Mode = VisualEffectMode.Custom } }), e => e.Contains("visualEffects.effects is required"));
        Assert.Contains(Validate(new HostConfig { VisualEffects = new() { Mode = VisualEffectMode.Custom, Effects = new Dictionary<string, bool> { ["Sparkles"] = true } } }), e => e.Contains("visualEffects.effects.Sparkles is not a known"));
        Assert.Contains(Validate(new HostConfig { VisualEffects = new() { Effects = new Dictionary<string, bool>() } }), e => e.Contains("only used"));
    }

    [Fact]
    public void DesktopIcons_ShowAndHideTheListedIcons()
    {
        string xml = GenerateText(new HostConfig { DesktopIcons = new Dictionary<string, bool> { ["ThisPC"] = true, ["RecycleBin"] = false } });

        Assert.Contains("-Name '{20d04fe0-3aea-1069-a2d8-08002b30309d}' -Value 0 -Type 'DWord'", xml);
        Assert.Contains("-Name '{645ff040-5081-101b-9f08-00aa002f954e}' -Value 1 -Type 'DWord'", xml);
        Assert.DoesNotContain("HideDesktopIcons", GenerateText(new HostConfig()));
        Assert.Contains(Validate(new HostConfig { DesktopIcons = new Dictionary<string, bool> { ["Nope"] = true } }), e => e.Contains("desktopIcons.Nope 'Nope' is not a known"));
    }

    // ---- Start menu and taskbar layout ----

    [Fact]
    public void StartFolders_WriteTheVisiblePlaces()
    {
        string settings = Convert.ToBase64String([.. Convert.FromBase64String("hghzUqpRQ0Kfeyd2WEZZ1A==")]);

        Assert.Contains($"-Name 'VisiblePlaces' -Value $( [convert]::FromBase64String('{settings}') )", GenerateText(new HostConfig { StartFolders = ["settings"] }));
        Assert.Contains("-Name 'VisiblePlaces' -Value $( [convert]::FromBase64String('') )", GenerateText(new HostConfig { StartFolders = [] }));
        Assert.DoesNotContain("VisiblePlaces", GenerateText(new HostConfig()));
        Assert.Contains(Validate(new HostConfig { StartFolders = ["Settings", "Nope", "settings"] }), e => e.Contains("startFolders[1] 'Nope'"));
        Assert.Contains(Validate(new HostConfig { StartFolders = ["Settings", "Nope", "settings"] }), e => e.Contains("more than once"));
    }

    [Fact]
    public void StartPins_EmptyAndCustom()
    {
        Assert.Contains(@"$json = '{""pinnedList"":[]}';", EmbeddedFile(GenerateText(new HostConfig { StartPins = new() { Mode = LayoutMode.Empty } }), "SetStartPins.ps1"));

        string custom = """{"pinnedList":[{"packagedAppId":"it's.an!App"}]}""";
        string script = EmbeddedFile(GenerateText(new HostConfig { StartPins = new() { Mode = LayoutMode.Custom, Content = custom } }), "SetStartPins.ps1");
        Assert.Contains("$json = '" + custom.Replace("'", "''") + "';", script);

        Assert.Contains(GenerateErrors(new HostConfig { StartPins = new() { Mode = LayoutMode.Custom, Content = "{ not json" } }), e => e.Contains("startPins.content is not valid JSON"));
        // Upstream alone accepts both of these.
        Assert.Contains(Validate(new HostConfig { StartPins = new() { Mode = LayoutMode.Custom, Content = "{" } }), e => e.Contains("startPins.content is not valid JSON"));
        Assert.Contains(Validate(new HostConfig { StartPins = new() { Mode = LayoutMode.Custom, Content = "[]" } }), e => e.Contains("must be a JSON object"));
        Assert.DoesNotContain("SetStartPins", GenerateText(new HostConfig()));
    }

    [Fact]
    public void StartTilesAndTaskbarIcons_EmptyAndCustom()
    {
        string empty = GenerateText(new HostConfig { StartTiles = new() { Mode = LayoutMode.Empty }, TaskbarIcons = new() { Mode = LayoutMode.Empty } });
        Assert.Contains("StartLayoutCollection", EmbeddedFile(empty, @"Shell\LayoutModification.xml"));
        Assert.Contains("#leaveempty", EmbeddedFile(empty, "TaskbarLayoutModification.xml"));

        string custom = GenerateText(new HostConfig
        {
            StartTiles = new() { Mode = LayoutMode.Custom, Content = "<Tiles marker='tiles-1'/>" },
            TaskbarIcons = new() { Mode = LayoutMode.Custom, Content = "<Taskbar marker='taskbar-1'/>" },
        });
        Assert.Contains("tiles-1", EmbeddedFile(custom, @"Shell\LayoutModification.xml"));
        Assert.Contains("taskbar-1", EmbeddedFile(custom, "TaskbarLayoutModification.xml"));

        Assert.Contains(GenerateErrors(new HostConfig { StartTiles = new() { Mode = LayoutMode.Custom, Content = "<open>" } }), e => e.Contains("Start menu XML is not well-formed"));
        Assert.Contains(GenerateErrors(new HostConfig { TaskbarIcons = new() { Mode = LayoutMode.Custom, Content = "<open>" } }), e => e.Contains("Taskbar XML is not well-formed"));
    }

    [Theory]
    [InlineData("startPins")]
    [InlineData("startTiles")]
    [InlineData("taskbarIcons")]
    public void LayoutContent_MustMatchTheMode(string field)
    {
        HostConfig With(LayoutOptions layout) => field switch
        {
            "startPins" => new HostConfig { StartPins = layout },
            "startTiles" => new HostConfig { StartTiles = layout },
            _ => new HostConfig { TaskbarIcons = layout },
        };

        Assert.Contains(Validate(With(new() { Mode = LayoutMode.Custom })), e => e.Contains($"{field}.content is required"));
        Assert.Contains(Validate(With(new() { Mode = LayoutMode.Empty, Content = "x" })), e => e.Contains($"{field}.content is only used"));
    }

    // ---- JSON and schema ----

    [Theory]
    [InlineData("""{ "components": [ null ] }""", "components[0]")]
    [InlineData("""{ "stickyKeys": { "mode": "Custom", "flags": [ null ] } }""", "stickyKeys.flags[0]")]
    [InlineData("""{ "startFolders": [ null ] }""", "startFolders[0]")]
    public void NullCollectionElement_IsRejected(string json, string path)
    {
        var ex = Assert.Throws<JsonException>(() => HostConfigJson.Deserialize(json));

        Assert.Contains(path, ex.Message);
    }

    [Fact]
    public void SettingsWrittenBeforeTheseExisted_StillMeanTheSame()
    {
        // No new property changes the output unless it is set.
        string before = GenerateText(HostConfigJson.Deserialize("""{ "schemaVersion": 1, "wifi": { "mode": "Skip" } }"""));
        string now = GenerateText(new HostConfig { Wifi = new() { Mode = WifiMode.Skip } });

        Assert.Equal(before, now);
        Assert.DoesNotContain("lockKeys", HostConfigJson.Serialize(new HostConfig()));
    }

    [Fact]
    public void UpstreamNames_AcceptOnlyExactNames()
    {
        Assert.True(UpstreamNames.IsKnown<Schneegans.Unattend.Pass>("oobeSystem"));
        Assert.False(UpstreamNames.IsKnown<Schneegans.Unattend.Pass>("OobeSystem"));
        Assert.False(UpstreamNames.IsKnown<Schneegans.Unattend.Pass>("3"));
        Assert.False(UpstreamNames.IsKnown<Schneegans.Unattend.Pass>(null));
        Assert.Throws<Schneegans.Unattend.ConfigurationException>(() => UpstreamNames.Parse<Schneegans.Unattend.Effect>("1"));
    }
}
