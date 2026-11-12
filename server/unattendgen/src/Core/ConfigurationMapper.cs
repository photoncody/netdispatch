using System.Collections.Immutable;
using System.Drawing;
using U = Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Translates our HostConfig into upstream's Configuration. This is the only place where our
/// model meets upstream's types; every upstream type is written with the "U." prefix.
///
/// Settings that HostConfig does not expose keep the values from U.Configuration.Default.
/// Upstream constructors validate their own input and throw U.ConfigurationException.
/// </summary>
public sealed class ConfigurationMapper(U.UnattendGenerator generator)
{
    public U.Configuration Map(HostConfig config)
    {
        var mapped = U.Configuration.Default with
        {
            ProcessorArchitectures = [.. config.ProcessorArchitectures.Select(MapArchitecture)],
            LanguageSettings = MapLanguage(config.Language),
            PESettings = MapSetup(config.Setup),
            ActivationKey = config.Setup.ActivationKey is { } key ? new U.ProductKey(key) : null,
            ComputerNameSettings = MapComputerName(config.ComputerName),
            TimeZoneSettings = MapTimeZone(config.TimeZone),
            AccountSettings = MapAccounts(config.Accounts),
            PasswordExpirationSettings = MapPasswordExpiration(config.PasswordExpiration),
            LockoutSettings = MapLockout(config.Lockout),
            WifiSettings = MapWifi(config.Wifi),
            ExpressSettings = config.ExpressSettings switch
            {
                ExpressSettingsMode.Interactive => U.ExpressSettingsMode.Interactive,
                ExpressSettingsMode.EnableAll => U.ExpressSettingsMode.EnableAll,
                ExpressSettingsMode.DisableAll => U.ExpressSettingsMode.DisableAll,
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.ExpressSettings, null),
            },
            HideFiles = config.HiddenFiles switch
            {
                HiddenFilesMode.Default => U.HideModes.Hidden,
                HiddenFilesMode.ShowHidden => U.HideModes.HiddenSystem,
                HiddenFilesMode.ShowAll => U.HideModes.None,
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.HiddenFiles, null),
            },
            TaskbarSearch = config.TaskbarSearch switch
            {
                TaskbarSearchMode.Box => U.TaskbarSearchMode.Box,
                TaskbarSearchMode.Hide => U.TaskbarSearchMode.Hide,
                TaskbarSearchMode.Icon => U.TaskbarSearchMode.Icon,
                TaskbarSearchMode.Label => U.TaskbarSearchMode.Label,
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.TaskbarSearch, null),
            },
            KeepSensitiveFiles = config.KeepSensitiveFiles,
            BypassNetworkCheck = config.BypassNetworkCheck,
            Bloatwares = [.. config.Bloatware.Select(generator.Lookup<U.Bloatware>)],
            ScriptSettings = MapScripts(config.Scripts),
            ProcessAuditSettings = config.ProcessAudit switch
            {
                ProcessAuditMode.Disabled => new U.DisabledProcessAuditSettings(),
                ProcessAuditMode.Enabled => new U.EnabledProcessAuditSettings(IncludeCommandLine: false),
                ProcessAuditMode.EnabledWithCommandLine => new U.EnabledProcessAuditSettings(IncludeCommandLine: true),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.ProcessAudit, null),
            },
            AppLockerSettings = config.AppLockerPolicy is { } policy
                ? new U.ConfigureAppLockerSettings(PolicyXml: policy)
                : new U.SkipAppLockerSettings(),
            Components = MapComponents(config.Components),
            LockKeySettings = config.LockKeys is { } keys
                ? new U.ConfigureLockKeySettings(
                    CapsLock: MapLockKey(keys.CapsLock),
                    NumLock: MapLockKey(keys.NumLock),
                    ScrollLock: MapLockKey(keys.ScrollLock))
                : new U.SkipLockKeySettings(),
            StickyKeysSettings = config.StickyKeys.Mode switch
            {
                StickyKeyMode.Default => new U.DefaultStickyKeysSettings(),
                StickyKeyMode.Disabled => new U.DisabledStickyKeysSettings(),
                StickyKeyMode.Custom => new U.CustomStickyKeysSettings(
                    (config.StickyKeys.Flags ?? []).Select(UpstreamNames.Parse<U.StickyKeys>).ToHashSet()),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.StickyKeys.Mode, null),
            },
            WallpaperSettings = config.Wallpaper.Mode switch
            {
                WallpaperMode.Default => new U.DefaultWallpaperSettings(),
                WallpaperMode.SolidColor => new U.SolidWallpaperSettings(ParseColor(config.Wallpaper.Color, "wallpaper.color")),
                WallpaperMode.Script => new U.ScriptWallpaperSettings(Require(config.Wallpaper.Script, "wallpaper.script")),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.Wallpaper.Mode, null),
            },
            LockScreenSettings = config.LockScreenScript is { } lockScreen
                ? new U.ScriptLockScreenSettings(Script: lockScreen)
                : new U.DefaultLockScreenSettings(),
            ColorSettings = config.Colors is { } colors
                ? new U.CustomColorSettings(
                    SystemTheme: MapTheme(colors.SystemTheme),
                    AppsTheme: MapTheme(colors.AppsTheme),
                    EnableTransparency: colors.Transparency,
                    AccentColorOnStart: colors.AccentOnStart,
                    AccentColorOnBorders: colors.AccentOnBorders,
                    AccentColor: ParseColor(colors.AccentColor, "colors.accentColor"))
                : new U.DefaultColorSettings(),
            StartPinsSettings = config.StartPins.Mode switch
            {
                LayoutMode.Default => new U.DefaultStartPinsSettings(),
                LayoutMode.Empty => new U.EmptyStartPinsSettings(),
                LayoutMode.Custom => new U.CustomStartPinsSettings(Json: Require(config.StartPins.Content, "startPins.content")),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.StartPins.Mode, null),
            },
            StartTilesSettings = config.StartTiles.Mode switch
            {
                LayoutMode.Default => new U.DefaultStartTilesSettings(),
                LayoutMode.Empty => new U.EmptyStartTilesSettings(),
                LayoutMode.Custom => new U.CustomStartTilesSettings(Xml: Require(config.StartTiles.Content, "startTiles.content")),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.StartTiles.Mode, null),
            },
            TaskbarIcons = config.TaskbarIcons.Mode switch
            {
                LayoutMode.Default => new U.DefaultTaskbarIcons(),
                LayoutMode.Empty => new U.EmptyTaskbarIcons(),
                LayoutMode.Custom => new U.CustomTaskbarIcons(Xml: Require(config.TaskbarIcons.Content, "taskbarIcons.content")),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.TaskbarIcons.Mode, null),
            },
            Effects = config.VisualEffects.Mode switch
            {
                VisualEffectMode.Default => new U.DefaultEffects(),
                VisualEffectMode.BestAppearance => new U.BestAppearanceEffects(),
                VisualEffectMode.BestPerformance => new U.BestPerformanceEffects(),
                VisualEffectMode.Custom => new U.CustomEffects(
                    (config.VisualEffects.Effects ?? new Dictionary<string, bool>())
                        .ToImmutableDictionary(e => UpstreamNames.Parse<U.Effect>(e.Key), e => e.Value)),
                _ => throw new ArgumentOutOfRangeException(nameof(config), config.VisualEffects.Mode, null),
            },
            DesktopIcons = config.DesktopIcons is { } icons
                ? new U.CustomDesktopIconSettings(icons.ToDictionary(i => generator.Lookup<U.DesktopIcon>(i.Key), i => i.Value))
                : new U.DefaultDesktopIconSettings(),
            StartFolderSettings = config.StartFolders is { } folders
                ? new U.CustomStartFolderSettings(folders.ToDictionary(
                    f => generator.StartFolders.TryGetValue(f, out var folder) ? folder : throw new U.ConfigurationException($"startFolders: {f} is not a known upstream value."),
                    _ => true))
                : new U.DefaultStartFolderSettings(),
        };
        return UpstreamFlags.Apply(mapped, config.Flags);
    }

    private static U.ProcessorArchitecture MapArchitecture(ProcessorArchitecture architecture)
    {
        return architecture switch
        {
            ProcessorArchitecture.X86 => U.ProcessorArchitecture.x86,
            ProcessorArchitecture.Amd64 => U.ProcessorArchitecture.amd64,
            ProcessorArchitecture.Arm64 => U.ProcessorArchitecture.arm64,
            _ => throw new ArgumentOutOfRangeException(nameof(architecture), architecture, null),
        };
    }

    private U.ITimeZoneSettings MapTimeZone(TimeZoneOptions timeZone)
    {
        return timeZone.Mode switch
        {
            TimeZoneMode.Implicit => new U.ImplicitTimeZoneSettings(),
            TimeZoneMode.Explicit => new U.ExplicitTimeZoneSettings(
                TimeZone: generator.Lookup<U.TimeOffset>(Require(timeZone.Id, "timeZone.id"))),
            _ => throw new ArgumentOutOfRangeException(nameof(timeZone), timeZone.Mode, null),
        };
    }

    private static U.IPasswordExpirationSettings MapPasswordExpiration(PasswordExpirationOptions expiration)
    {
        return expiration.Mode switch
        {
            PasswordExpirationMode.Default => new U.DefaultPasswordExpirationSettings(),
            PasswordExpirationMode.Unlimited => new U.UnlimitedPasswordExpirationSettings(),
            PasswordExpirationMode.Custom => new U.CustomPasswordExpirationSettings(expiration.MaxAgeDays),
            _ => throw new ArgumentOutOfRangeException(nameof(expiration), expiration.Mode, null),
        };
    }

    private static U.ILockoutSettings MapLockout(LockoutOptions lockout)
    {
        return lockout.Mode switch
        {
            LockoutMode.Default => new U.DefaultLockoutSettings(),
            LockoutMode.Disabled => new U.DisableLockoutSettings(),
            LockoutMode.Custom => new U.CustomLockoutSettings(
                lockoutThreshold: lockout.Threshold,
                lockoutDuration: lockout.DurationMinutes,
                lockoutWindow: lockout.WindowMinutes),
            _ => throw new ArgumentOutOfRangeException(nameof(lockout), lockout.Mode, null),
        };
    }

    private static U.IWifiSettings MapWifi(WifiOptions wifi)
    {
        switch (wifi.Mode)
        {
            case WifiMode.Interactive:
                return new U.InteractiveWifiSettings();

            case WifiMode.Skip:
                return new U.SkipWifiSettings();

            case WifiMode.Profile:
                var profile = wifi.Profile ?? throw new U.ConfigurationException("wifi.profile is required.");
                return new U.ParameterizedWifiSettings(
                    Name: profile.Ssid,
                    Password: profile.Password,
                    ConnectAutomatically: profile.ConnectAutomatically,
                    Authentication: profile.Authentication switch
                    {
                        WifiAuthentication.Open => U.WifiAuthentications.Open,
                        WifiAuthentication.Wpa2 => U.WifiAuthentications.WPA2PSK,
                        WifiAuthentication.Wpa3 => U.WifiAuthentications.WPA3SAE,
                        _ => throw new ArgumentOutOfRangeException(nameof(wifi), profile.Authentication, null),
                    },
                    NonBroadcast: profile.Hidden);

            case WifiMode.ProfileXml:
                return new U.XmlWifiSettings(Xml: Require(wifi.ProfileXml, "wifi.profileXml"));

            default:
                throw new ArgumentOutOfRangeException(nameof(wifi), wifi.Mode, null);
        }
    }

    private ImmutableDictionary<U.ComponentAndPass, string> MapComponents(IReadOnlyList<CustomComponent> components)
    {
        var builder = ImmutableDictionary.CreateBuilder<U.ComponentAndPass, string>();
        foreach (var item in components)
        {
            // Upstream's own spelling of the ID: lookups ignore case, the answer file does not.
            var key = new U.ComponentAndPass(generator.Lookup<U.Component>(item.Component).Id, UpstreamNames.Parse<U.Pass>(item.Pass));
            if (!builder.TryAdd(key, item.Xml))
            {
                throw new U.ConfigurationException($"components lists '{key.Component}' in the '{key.Pass}' pass more than once.");
            }
        }
        return builder.ToImmutable();
    }

    private static U.LockKeySetting MapLockKey(LockKey key)
    {
        return new U.LockKeySetting(
            Initial: key.On ? U.LockKeyInitial.On : U.LockKeyInitial.Off,
            Behavior: key.Ignore ? U.LockKeyBehavior.Ignore : U.LockKeyBehavior.Toggle);
    }

    private static U.ColorTheme MapTheme(ThemeMode theme)
    {
        return theme switch
        {
            ThemeMode.Dark => U.ColorTheme.Dark,
            ThemeMode.Light => U.ColorTheme.Light,
            _ => throw new ArgumentOutOfRangeException(nameof(theme), theme, null),
        };
    }

    /// <summary>Reads "#rrggbb".</summary>
    private static Color ParseColor(string? value, string field)
    {
        if (!UpstreamNames.IsHtmlColor(value))
        {
            throw new U.ConfigurationException($"{field} must be a colour written as #rrggbb.");
        }
        return Color.FromArgb(
            Convert.ToInt32(value![1..3], 16),
            Convert.ToInt32(value[3..5], 16),
            Convert.ToInt32(value[5..7], 16));
    }

    private U.ILanguageSettings MapLanguage(LanguageOptions language)
    {
        switch (language.Mode)
        {
            case LanguageMode.Interactive:
                return new U.InteractiveLanguageSettings();

            case LanguageMode.Unattended:
                var pairs = language.Locales.Select(pair => new U.LocaleAndKeyboard(
                    Locale: generator.Lookup<U.UserLocale>(pair.Locale),
                    Keyboard: generator.Lookup<U.KeyboardIdentifier>(pair.Keyboard)
                )).ToList();
                if (pairs.Count is < 1 or > 3)
                {
                    throw new U.ConfigurationException("Between one and three locale/keyboard pairs are required.");
                }
                return new U.UnattendedLanguageSettings(
                    ImageLanguage: generator.Lookup<U.ImageLanguage>(Require(language.ImageLanguage, "language.imageLanguage")),
                    LocaleAndKeyboard: pairs[0],
                    LocaleAndKeyboard2: pairs.ElementAtOrDefault(1),
                    LocaleAndKeyboard3: pairs.ElementAtOrDefault(2),
                    GeoLocation: generator.Lookup<U.GeoLocation>(Require(language.GeoLocation, "language.geoLocation"))
                );

            default:
                throw new ArgumentOutOfRangeException(nameof(language), language.Mode, null);
        }
    }

    private U.IPESettings MapSetup(SetupOptions setup)
    {
        return setup.Mode switch
        {
            SetupMode.Standard => MapStandardSetup(setup.Standard ?? new()),
            SetupMode.Scripted => MapScriptedSetup(setup.Scripted ?? new()),
            SetupMode.CustomScript => new U.ScriptPESetttings(Script: Require(setup.PeScript, "setup.peScript")),
            _ => throw new ArgumentOutOfRangeException(nameof(setup), setup.Mode, null),
        };
    }

    private U.DefaultPESettings MapStandardSetup(StandardSetupOptions standard)
    {
        U.IEditionSettings edition = standard.EditionMode switch
        {
            EditionMode.Interactive => new U.InteractiveEditionSettings(),
            EditionMode.Firmware => new U.FirmwareEditionSettings(),
            EditionMode.Edition => new U.UnattendedEditionSettings(
                Edition: generator.Lookup<U.WindowsEdition>(Require(standard.Edition, "setup.standard.edition"))),
            EditionMode.ProductKey => new U.CustomEditionSettings(
                ProductKey: new U.ProductKey(Require(standard.ProductKey, "setup.standard.productKey"))),
            _ => throw new ArgumentOutOfRangeException(nameof(standard), standard.EditionMode, null),
        };

        return new U.DefaultPESettings(
            EditionSettings: edition,
            BypassRequirementsCheck: standard.BypassRequirementsCheck,
            // A configuration set would make Setup look for $OEM$ next to the answer file, which
            // netdispatch serves from X:\Windows\System32. Not offered.
            UseConfigurationSet: false
        );
    }

    private U.GeneratePESettings MapScriptedSetup(ScriptedSetupOptions scripted)
    {
        U.IInstallFromSettings installFrom = scripted.ImageMode switch
        {
            ImageSelectionMode.Interactive => new U.InteractiveInstallFromSettings(),
            ImageSelectionMode.Edition => new U.EditionInstallFromSettings(
                Edition: generator.Lookup<U.WindowsEdition>(Require(scripted.Edition, "setup.scripted.edition"))),
            ImageSelectionMode.Index => new U.IndexInstallFromSettings(
                Index: scripted.ImageIndex ?? throw new U.ConfigurationException("setup.scripted.imageIndex is required.")),
            ImageSelectionMode.Name => new U.NameInstallFromSettings(
                Name: Require(scripted.ImageName, "setup.scripted.imageName")),
            _ => throw new ArgumentOutOfRangeException(nameof(scripted), scripted.ImageMode, null),
        };

        return new U.GeneratePESettings(
            PartitionSettings: MapPartitioning(scripted.Partitioning),
            InstallFromSettings: installFrom,
            PagingFileSettings: scripted.PagingFile.Mode switch
            {
                PagingFileMode.Automatic => new U.AutomaticPagingFileSettings(),
                PagingFileMode.None => new U.NoPagingFileSettings(),
                PagingFileMode.Custom => new U.CustomPagingFileSettings(
                    InitialSizeMiB: scripted.PagingFile.InitialSizeMiB ?? throw new U.ConfigurationException("setup.scripted.pagingFile.initialSizeMiB is required."),
                    MaxSizeMiB: scripted.PagingFile.MaxSizeMiB ?? throw new U.ConfigurationException("setup.scripted.pagingFile.maxSizeMiB is required.")),
                _ => throw new ArgumentOutOfRangeException(nameof(scripted), scripted.PagingFile.Mode, null),
            },
            DisableDefender: scripted.DisableDefender,
            Disable8Dot3Names: scripted.Disable8Dot3Names,
            PauseBeforeFormatting: scripted.PauseBeforeFormatting,
            PauseBeforeReboot: scripted.PauseBeforeReboot,
            CompactOs: scripted.CompactOs,
            SkipIntegrityCheck: scripted.SkipIntegrityCheck
        );
    }

    private static U.IPartitionSettings MapPartitioning(PartitionOptions partitioning)
    {
        return partitioning.Mode switch
        {
            PartitionMode.Interactive => new U.InteractivePartitionSettings(),
            PartitionMode.CustomScript => new U.CustomPartitionSettings(
                Script: Require(partitioning.DiskpartScript, "setup.scripted.partitioning.diskpartScript")),
            PartitionMode.Unattended => new U.UnattendedPartitionSettings(
                TargetDiskSettings: MapTargetDisk(partitioning.TargetDisk),
                PartitionLayout: partitioning.Layout switch
                {
                    PartitionLayout.Gpt => U.PartitionLayout.GPT,
                    PartitionLayout.Mbr => U.PartitionLayout.MBR,
                    PartitionLayout.Automatic => U.PartitionLayout.Automatic,
                    _ => throw new ArgumentOutOfRangeException(nameof(partitioning), partitioning.Layout, null),
                },
                RecoveryMode: partitioning.RecoveryPartition ? U.RecoveryMode.Partition : U.RecoveryMode.None,
                SystemSize: partitioning.SystemPartitionSizeMiB,
                RecoverySize: partitioning.RecoveryPartitionSizeMiB
            ),
            _ => throw new ArgumentOutOfRangeException(nameof(partitioning), partitioning.Mode, null),
        };
    }

    private static U.ITargetDiskSettings MapTargetDisk(TargetDiskOptions disk)
    {
        return disk.Mode switch
        {
            TargetDiskMode.Fixed => new U.FixedTargetDiskSettings(
                Index: disk.Index ?? throw new U.ConfigurationException("setup.scripted.partitioning.targetDisk.index is required.")),
            TargetDiskMode.Auto => new U.GeneratedTargetDiskSettings(
                minSizeGiB: disk.MinSizeGiB,
                maxSizeGiB: disk.MaxSizeGiB,
                index: disk.Index,
                assertNoPartitions: disk.RequireNoPartitions
            ),
            TargetDiskMode.Interactive => new U.InteractiveTargetDiskSettings(),
            _ => throw new ArgumentOutOfRangeException(nameof(disk), disk.Mode, null),
        };
    }

    private static U.IComputerNameSettings MapComputerName(ComputerNameOptions computerName)
    {
        return computerName.Mode switch
        {
            ComputerNameMode.Random => new U.RandomComputerNameSettings(),
            ComputerNameMode.Custom => new U.CustomComputerNameSettings(computerName.Name),
            ComputerNameMode.Prompt => new U.PromptComputerNameSettings(),
            ComputerNameMode.Hostname => new U.CustomComputerNameSettings(ComputerNameOptions.HostnameToken),
            _ => throw new ArgumentOutOfRangeException(nameof(computerName), computerName.Mode, null),
        };
    }

    private static U.IAccountSettings MapAccounts(AccountOptions accounts)
    {
        switch (accounts.Mode)
        {
            case AccountMode.InteractiveMicrosoft:
                return new U.InteractiveMicrosoftAccountSettings();

            case AccountMode.InteractiveLocal:
                return new U.InteractiveLocalAccountSettings();

            case AccountMode.Unattended:
                U.IAutoLogonSettings autoLogon = accounts.AutoLogon switch
                {
                    AutoLogonMode.None => new U.NoneAutoLogonSettings(),
                    AutoLogonMode.BuiltinAdministrator => new U.BuiltinAutoLogonSettings(
                        password: accounts.BuiltinAdministratorPassword ?? throw new U.ConfigurationException("accounts.builtinAdministratorPassword is required.")),
                    AutoLogonMode.FirstAdministrator => new U.OwnAutoLogonSettings(),
                    _ => throw new ArgumentOutOfRangeException(nameof(accounts), accounts.AutoLogon, null),
                };
                return new U.UnattendedAccountSettings(
                    accounts: [.. accounts.Users.Select(MapAccount)],
                    autoLogonSettings: autoLogon,
                    obscurePasswords: accounts.ObscurePasswords
                );

            default:
                throw new ArgumentOutOfRangeException(nameof(accounts), accounts.Mode, null);
        }
    }

    private static U.Account MapAccount(UserAccount account)
    {
        return new U.Account(
            name: account.Name,
            displayName: account.DisplayName,
            password: account.Password,
            group: account.Group switch
            {
                AccountGroup.Administrators => U.Constants.AdministratorsGroup,
                AccountGroup.Users => U.Constants.UsersGroup,
                _ => throw new ArgumentOutOfRangeException(nameof(account), account.Group, null),
            }
        );
    }

    private static U.ScriptSettings MapScripts(ScriptOptions scripts)
    {
        return new U.ScriptSettings(
            Scripts: [.. scripts.Items.Select(MapScript)],
            RestartExplorer: scripts.RestartExplorer
        );
    }

    private static U.Script MapScript(CustomScript script)
    {
        return new U.Script(
            content: script.Content,
            phase: script.Phase switch
            {
                ScriptPhase.System => U.ScriptPhase.System,
                ScriptPhase.FirstLogon => U.ScriptPhase.FirstLogon,
                ScriptPhase.UserOnce => U.ScriptPhase.UserOnce,
                ScriptPhase.DefaultUser => U.ScriptPhase.DefaultUser,
                _ => throw new ArgumentOutOfRangeException(nameof(script), script.Phase, null),
            },
            type: script.Type switch
            {
                ScriptType.Cmd => U.ScriptType.Cmd,
                ScriptType.Ps1 => U.ScriptType.Ps1,
                ScriptType.Reg => U.ScriptType.Reg,
                ScriptType.Vbs => U.ScriptType.Vbs,
                _ => throw new ArgumentOutOfRangeException(nameof(script), script.Type, null),
            }
        );
    }

    private static string Require(string? value, string field)
    {
        return string.IsNullOrWhiteSpace(value)
            ? throw new U.ConfigurationException($"{field} is required.")
            : value;
    }
}
