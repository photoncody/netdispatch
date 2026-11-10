namespace Netdispatch.UnattendGen;

// HostConfig is our own model and also the settings format netdispatch stores for a generated
// template (see HostConfigJson). It deliberately does not reuse upstream types or enums, so that
// upstream renames cannot silently change the meaning of saved settings. ConfigurationMapper
// translates it.
//
// Every property has a default. The defaults are chosen so that `new HostConfig()` maps to
// upstream's `Configuration.Default` (there is a test for this). Settings that are not
// exposed yet always take upstream's default value. netdispatch's own defaults for a new
// template live in the hub (server/app/unattend_generator.py), not here.
//
// Derived from UnattendHost (https://github.com/photoncody/unattend-generator), MIT licence.

public sealed record HostConfig
{
    public const int CurrentSchemaVersion = 1;

    public int SchemaVersion { get; init; } = CurrentSchemaVersion;

    /// <summary>Processor architectures to write components for. Upstream writes one copy of each component per entry.</summary>
    public IReadOnlyList<ProcessorArchitecture> ProcessorArchitectures { get; init; } = [ProcessorArchitecture.Amd64];

    public LanguageOptions Language { get; init; } = new();

    public SetupOptions Setup { get; init; } = new();

    public ComputerNameOptions ComputerName { get; init; } = new();

    public TimeZoneOptions TimeZone { get; init; } = new();

    public AccountOptions Accounts { get; init; } = new();

    public PasswordExpirationOptions PasswordExpiration { get; init; } = new();

    public LockoutOptions Lockout { get; init; } = new();

    public WifiOptions Wifi { get; init; } = new();

    public ExpressSettingsMode ExpressSettings { get; init; } = ExpressSettingsMode.DisableAll;

    /// <summary>
    /// Upstream's plain on/off settings (for example "DisableUac"), by upstream's own name.
    /// Settings not listed keep upstream's default. The names a build accepts are listed by
    /// the CLI's "schema" command, so a new upstream switch needs no code change here.
    /// </summary>
    public IReadOnlyDictionary<string, bool> Flags { get; init; } = new Dictionary<string, bool>();

    /// <summary>
    /// Keep the answer file (which may contain passwords) in C:\Windows\Panther after setup.
    /// When false, upstream deletes it at first logon, which requires automatic logon as an
    /// administrator if accounts are created unattended.
    /// </summary>
    public bool KeepSensitiveFiles { get; init; }

    /// <summary>Allow OOBE to finish without a network connection (BypassNRO).</summary>
    public bool BypassNetworkCheck { get; init; }

    /// <summary>Upstream bloatware IDs, e.g. "RemoveTeams".</summary>
    public IReadOnlyList<string> Bloatware { get; init; } = [];

    public ScriptOptions Scripts { get; init; } = new();
}

public enum ProcessorArchitecture { X86, Amd64, Arm64 }

public enum ExpressSettingsMode { Interactive, EnableAll, DisableAll }

// ---- Language, region, keyboard ----

public enum LanguageMode { Interactive, Unattended }

public sealed record LanguageOptions
{
    public LanguageMode Mode { get; init; } = LanguageMode.Interactive;

    /// <summary>Windows display language of the install image, e.g. "en-US". Unattended only.</summary>
    public string? ImageLanguage { get; init; }

    /// <summary>One to three locale/keyboard pairs; the first one is the default. Unattended only.</summary>
    public IReadOnlyList<LocaleKeyboard> Locales { get; init; } = [];

    /// <summary>Upstream GeoID, e.g. "244" for the United States. Unattended only.</summary>
    public string? GeoLocation { get; init; }
}

public sealed record LocaleKeyboard
{
    /// <summary>Upstream user locale ID, e.g. "en-US".</summary>
    public required string Locale { get; init; }

    /// <summary>Upstream keyboard identifier, e.g. "00000409".</summary>
    public required string Keyboard { get; init; }
}

// ---- Windows PE stage: edition, product key, partitioning ----

/// <summary>
/// Standard: Windows Setup (setup.exe) runs the PE stage; partitioning is interactive.
/// Scripted: upstream generates a pe.cmd script that partitions the disk and applies the image.
/// CustomScript: the given .cmd script replaces the whole PE stage.
/// </summary>
public enum SetupMode { Standard, Scripted, CustomScript }

public sealed record SetupOptions
{
    public SetupMode Mode { get; init; } = SetupMode.Standard;

    /// <summary>Only allowed when Mode is Standard. Null means defaults.</summary>
    public StandardSetupOptions? Standard { get; init; }

    /// <summary>Only allowed when Mode is Scripted. Null means defaults.</summary>
    public ScriptedSetupOptions? Scripted { get; init; }

    /// <summary>The .cmd script for the PE stage. Only with SetupMode.CustomScript.</summary>
    public string? PeScript { get; init; }

    /// <summary>Optional key that Windows uses for activation, written in the specialize pass.</summary>
    public string? ActivationKey { get; init; }
}

public enum EditionMode
{
    /// <summary>Setup asks for a product key or edition.</summary>
    Interactive,
    /// <summary>Use the key embedded in the device firmware.</summary>
    Firmware,
    /// <summary>Use the generic key upstream lists for the chosen edition.</summary>
    Edition,
    /// <summary>Use the given product key.</summary>
    ProductKey,
}

public sealed record StandardSetupOptions
{
    public EditionMode EditionMode { get; init; } = EditionMode.Interactive;

    /// <summary>Upstream edition ID, e.g. "pro". Only with EditionMode.Edition.</summary>
    public string? Edition { get; init; }

    /// <summary>Only with EditionMode.ProductKey.</summary>
    public string? ProductKey { get; init; }

    /// <summary>Skip the TPM, Secure Boot and RAM checks (LabConfig registry values).</summary>
    public bool BypassRequirementsCheck { get; init; } = true;
}

public enum ImageSelectionMode { Interactive, Edition, Index, Name }

public sealed record ScriptedSetupOptions
{
    public ImageSelectionMode ImageMode { get; init; } = ImageSelectionMode.Interactive;

    /// <summary>Upstream edition ID, e.g. "pro". Only with ImageMode.Edition.</summary>
    public string? Edition { get; init; }

    /// <summary>1-based index into install.wim. Only with ImageMode.Index.</summary>
    public int? ImageIndex { get; init; }

    /// <summary>Image name as shown by dism /Get-WimInfo. Only with ImageMode.Name.</summary>
    public string? ImageName { get; init; }

    public PartitionOptions Partitioning { get; init; } = new();

    public PagingFileOptions PagingFile { get; init; } = new();

    public bool DisableDefender { get; init; }

    public bool Disable8Dot3Names { get; init; }

    public bool PauseBeforeFormatting { get; init; }

    public bool PauseBeforeReboot { get; init; }

    public bool CompactOs { get; init; }

    public bool SkipIntegrityCheck { get; init; }
}

public enum PartitionMode
{
    /// <summary>The user partitions the disk by hand from a second console.</summary>
    Interactive,
    /// <summary>Wipe the target disk and create the standard layout.</summary>
    Unattended,
    /// <summary>Run the given diskpart script.</summary>
    CustomScript,
}

/// <summary>Automatic picks GPT or MBR at install time from the firmware type.</summary>
public enum PartitionLayout { Gpt, Mbr, Automatic }

public sealed record PartitionOptions
{
    public PartitionMode Mode { get; init; } = PartitionMode.Interactive;

    // The following apply to PartitionMode.Unattended.

    public PartitionLayout Layout { get; init; } = PartitionLayout.Gpt;

    public bool RecoveryPartition { get; init; } = true;

    public int SystemPartitionSizeMiB { get; init; } = Schneegans.Unattend.Constants.SystemPartitionSize;

    public int RecoveryPartitionSizeMiB { get; init; } = Schneegans.Unattend.Constants.RecoveryPartitionSize;

    public TargetDiskOptions TargetDisk { get; init; } = new();

    /// <summary>Only with PartitionMode.CustomScript.</summary>
    public string? DiskpartScript { get; init; }
}

public enum TargetDiskMode
{
    /// <summary>Always use the disk with the given index.</summary>
    Fixed,
    /// <summary>Use the single disk that matches all the given criteria; stop if none or several match.</summary>
    Auto,
    /// <summary>Ask for the disk index during setup.</summary>
    Interactive,
}

public sealed record TargetDiskOptions
{
    public TargetDiskMode Mode { get; init; } = TargetDiskMode.Fixed;

    /// <summary>Disk index. Required for Fixed; optional criterion for Auto.</summary>
    public int? Index { get; init; } = 0;

    // The following apply to TargetDiskMode.Auto. Null means "no constraint".

    public int? MinSizeGiB { get; init; } = Schneegans.Unattend.Constants.TargetDiskMinSizeGiB;

    public int? MaxSizeGiB { get; init; } = Schneegans.Unattend.Constants.TargetDiskMaxSizeGiB;

    public bool RequireNoPartitions { get; init; } = true;
}

public enum PagingFileMode
{
    /// <summary>Windows manages the paging file size.</summary>
    Automatic,
    /// <summary>Use the given initial and maximum size.</summary>
    Custom,
    /// <summary>No paging file.</summary>
    None,
}

public sealed record PagingFileOptions
{
    public PagingFileMode Mode { get; init; } = PagingFileMode.Automatic;

    /// <summary>Only with PagingFileMode.Custom.</summary>
    public int? InitialSizeMiB { get; init; }

    /// <summary>Only with PagingFileMode.Custom.</summary>
    public int? MaxSizeMiB { get; init; }
}

// ---- Computer name ----

public enum ComputerNameMode
{
    Random,
    Custom,
    Prompt,
    /// <summary>
    /// The netdispatch device's hostname. The answer file gets HostnameToken, which the hub
    /// replaces with its {{ hostname }} template variable.
    /// </summary>
    Hostname,
}

public sealed record ComputerNameOptions
{
    /// <summary>
    /// Placeholder written as the computer name in Hostname mode. Exactly 15 characters, so it
    /// passes upstream's computer name rules and is never shorter than the name it stands for.
    /// </summary>
    public const string HostnameToken = "__ND_HOSTNAME__";

    public ComputerNameMode Mode { get; init; } = ComputerNameMode.Random;

    /// <summary>Only with ComputerNameMode.Custom.</summary>
    public string? Name { get; init; }
}

// ---- Time zone ----

public enum TimeZoneMode
{
    /// <summary>Windows derives the time zone from the region settings.</summary>
    Implicit,
    /// <summary>Use the given Windows time zone ID.</summary>
    Explicit,
}

public sealed record TimeZoneOptions
{
    public TimeZoneMode Mode { get; init; } = TimeZoneMode.Implicit;

    /// <summary>Upstream time zone ID, e.g. "UTC" or "W. Europe Standard Time". Only with TimeZoneMode.Explicit.</summary>
    public string? Id { get; init; }
}

// ---- Password expiration and account lockout ----

public enum PasswordExpirationMode
{
    /// <summary>Windows' default: passwords expire after 42 days.</summary>
    Default,
    Unlimited,
    Custom,
}

public sealed record PasswordExpirationOptions
{
    public PasswordExpirationMode Mode { get; init; } = PasswordExpirationMode.Default;

    /// <summary>Days, 1 to 999. Only with PasswordExpirationMode.Custom.</summary>
    public int? MaxAgeDays { get; init; }
}

public enum LockoutMode
{
    /// <summary>Windows' default: 10 failed attempts lock the account for 10 minutes.</summary>
    Default,
    Disabled,
    Custom,
}

public sealed record LockoutOptions
{
    public LockoutMode Mode { get; init; } = LockoutMode.Default;

    // The following apply to LockoutMode.Custom.

    /// <summary>Failed attempts before lockout, 0 to 999.</summary>
    public int? Threshold { get; init; }

    /// <summary>Minutes an account stays locked, 1 to 99999.</summary>
    public int? DurationMinutes { get; init; }

    /// <summary>Minutes after which the failed-attempt counter resets, 1 to 99999 and not more than DurationMinutes.</summary>
    public int? WindowMinutes { get; init; }
}

// ---- Wi-Fi ----

public enum WifiMode
{
    /// <summary>Setup shows the Wi-Fi page.</summary>
    Interactive,
    /// <summary>Setup skips the Wi-Fi page.</summary>
    Skip,
}

public sealed record WifiOptions
{
    public WifiMode Mode { get; init; } = WifiMode.Interactive;
}

// ---- User accounts ----

public enum AccountMode { InteractiveMicrosoft, InteractiveLocal, Unattended }

public enum AccountGroup { Administrators, Users }

public enum AutoLogonMode
{
    None,
    /// <summary>Log on as the built-in Administrator account, which gets BuiltinAdministratorPassword.</summary>
    BuiltinAdministrator,
    /// <summary>Log on as the first account in the Administrators group.</summary>
    FirstAdministrator,
}

public sealed record AccountOptions
{
    public AccountMode Mode { get; init; } = AccountMode.InteractiveMicrosoft;

    // The following apply to AccountMode.Unattended.

    public IReadOnlyList<UserAccount> Users { get; init; } = [];

    public AutoLogonMode AutoLogon { get; init; } = AutoLogonMode.None;

    /// <summary>Only with AutoLogonMode.BuiltinAdministrator.</summary>
    public string? BuiltinAdministratorPassword { get; init; }

    /// <summary>Base64-encode passwords in the XML. This hides them from casual view; it is not encryption.</summary>
    public bool ObscurePasswords { get; init; }
}

public sealed record UserAccount
{
    public required string Name { get; init; }

    public string DisplayName { get; init; } = "";

    public string Password { get; init; } = "";

    public AccountGroup Group { get; init; } = AccountGroup.Administrators;
}

// ---- Custom scripts ----

public enum ScriptPhase
{
    /// <summary>System context, before user accounts are created.</summary>
    System,
    /// <summary>When the first user logs on.</summary>
    FirstLogon,
    /// <summary>Whenever a user logs on for the first time.</summary>
    UserOnce,
    /// <summary>Modifies the default user's registry hive.</summary>
    DefaultUser,
}

public enum ScriptType { Cmd, Ps1, Reg, Vbs }

public sealed record ScriptOptions
{
    public IReadOnlyList<CustomScript> Items { get; init; } = [];

    public bool RestartExplorer { get; init; }
}

public sealed record CustomScript
{
    public required ScriptPhase Phase { get; init; }

    public required ScriptType Type { get; init; }

    public required string Content { get; init; }
}
