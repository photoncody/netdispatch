using Schneegans.Unattend;

namespace Netdispatch.UnattendGen;

/// <summary>
/// Checks a HostConfig before it is mapped, and reports every problem at once.
///
/// Upstream validates some values itself (computer name rules, user names, product key format)
/// and throws on the first problem; GenerationService reports those. This validator covers our
/// own rules, unknown IDs (with clearer messages than upstream's), and settings that are filled
/// in but would be ignored because their mode is not selected.
/// </summary>
public sealed class HostConfigValidator(UnattendGenerator generator)
{
    public IReadOnlyList<string> Validate(HostConfig config)
    {
        var errors = new List<string>();

        if (config.SchemaVersion != HostConfig.CurrentSchemaVersion)
        {
            errors.Add($"schemaVersion is {config.SchemaVersion}, but this version only reads schemaVersion {HostConfig.CurrentSchemaVersion}.");
        }

        ValidateArchitectures(config.ProcessorArchitectures, errors);
        ValidateLanguage(config.Language, errors);
        ValidateSetup(config.Setup, errors);
        ValidateComputerName(config.ComputerName, errors);
        ValidateTimeZone(config.TimeZone, errors);
        ValidateAccounts(config.Accounts, errors);
        ValidatePasswordExpiration(config.PasswordExpiration, errors);
        ValidateLockout(config.Lockout, errors);
        ValidateFlags(config.Flags, errors);
        ValidateBloatware(config.Bloatware, errors);
        ValidateScripts(config.Scripts, errors);

        return errors;
    }

    private static void ValidateArchitectures(IReadOnlyList<ProcessorArchitecture> architectures, List<string> errors)
    {
        if (architectures.Count == 0)
        {
            errors.Add("processorArchitectures must list at least one architecture.");
        }
        if (architectures.Distinct().Count() != architectures.Count)
        {
            errors.Add("processorArchitectures lists an architecture more than once.");
        }
    }

    private void ValidateTimeZone(TimeZoneOptions timeZone, List<string> errors)
    {
        if (timeZone.Mode == TimeZoneMode.Explicit)
        {
            Known(generator.TimeOffsets, timeZone.Id, "timeZone.id", errors);
        }
        else
        {
            Unused(timeZone.Id, "timeZone.id", "timeZone.mode is Explicit", errors);
        }
    }

    private static void ValidatePasswordExpiration(PasswordExpirationOptions expiration, List<string> errors)
    {
        if (expiration.Mode == PasswordExpirationMode.Custom)
        {
            InRange(expiration.MaxAgeDays, 1, 999, "passwordExpiration.maxAgeDays", errors);
        }
        else if (expiration.MaxAgeDays != null)
        {
            errors.Add("passwordExpiration.maxAgeDays is only used when passwordExpiration.mode is Custom.");
        }
    }

    private static void ValidateLockout(LockoutOptions lockout, List<string> errors)
    {
        if (lockout.Mode != LockoutMode.Custom)
        {
            if (lockout.Threshold != null || lockout.DurationMinutes != null || lockout.WindowMinutes != null)
            {
                errors.Add("lockout.threshold, lockout.durationMinutes and lockout.windowMinutes are only used when lockout.mode is Custom.");
            }
            return;
        }

        InRange(lockout.Threshold, 0, 999, "lockout.threshold", errors);
        InRange(lockout.DurationMinutes, 1, 99_999, "lockout.durationMinutes", errors);
        InRange(lockout.WindowMinutes, 1, 99_999, "lockout.windowMinutes", errors);
        if (lockout.WindowMinutes > lockout.DurationMinutes)
        {
            errors.Add("lockout.windowMinutes must not be greater than lockout.durationMinutes.");
        }
    }

    private static void ValidateFlags(IReadOnlyDictionary<string, bool> flags, List<string> errors)
    {
        foreach (var name in flags.Keys.Order(StringComparer.Ordinal))
        {
            if (UpstreamFlags.ModeledElsewhere.Contains(name))
            {
                errors.Add($"flags.{name} is not a flag; use the property of the same name.");
            }
            else if (!UpstreamFlags.IsKnown(name))
            {
                errors.Add($"flags.{name} is not a known upstream setting.");
            }
        }
    }

    private static void InRange(int? value, int min, int max, string field, List<string> errors)
    {
        if (value == null)
        {
            errors.Add($"{field} is required.");
        }
        else if (value < min || value > max)
        {
            errors.Add($"{field} must be between {min} and {max}.");
        }
    }

    private void ValidateLanguage(LanguageOptions language, List<string> errors)
    {
        if (language.Mode == LanguageMode.Interactive)
        {
            Unused(language.ImageLanguage, "language.imageLanguage", "language.mode is Unattended", errors);
            Unused(language.GeoLocation, "language.geoLocation", "language.mode is Unattended", errors);
            if (language.Locales.Count > 0)
            {
                errors.Add("language.locales is only used when language.mode is Unattended.");
            }
            return;
        }

        Known(generator.ImageLanguages, language.ImageLanguage, "language.imageLanguage", errors);
        Known(generator.GeoLocations, language.GeoLocation, "language.geoLocation", errors);

        if (language.Locales.Count is < 1 or > 3)
        {
            errors.Add($"language.locales must have between 1 and 3 entries, but has {language.Locales.Count}.");
        }
        for (int i = 0; i < language.Locales.Count; i++)
        {
            Known(generator.UserLocales, language.Locales[i].Locale, $"language.locales[{i}].locale", errors);
            Known(generator.KeyboardIdentifiers, language.Locales[i].Keyboard, $"language.locales[{i}].keyboard", errors);
        }
    }

    private void ValidateSetup(SetupOptions setup, List<string> errors)
    {
        if (setup.Mode != SetupMode.Standard && setup.Standard != null)
        {
            errors.Add("setup.standard is only used when setup.mode is Standard.");
        }
        if (setup.Mode != SetupMode.Scripted && setup.Scripted != null)
        {
            errors.Add("setup.scripted is only used when setup.mode is Scripted.");
        }

        switch (setup.Mode)
        {
            case SetupMode.Standard:
                ValidateStandardSetup(setup.Standard ?? new(), errors);
                Unused(setup.PeScript, "setup.peScript", "setup.mode is CustomScript", errors);
                break;

            case SetupMode.Scripted:
                ValidateScriptedSetup(setup.Scripted ?? new(), errors);
                Unused(setup.PeScript, "setup.peScript", "setup.mode is CustomScript", errors);
                break;

            case SetupMode.CustomScript:
                Required(setup.PeScript, "setup.peScript", errors);
                break;
        }
    }

    private void ValidateStandardSetup(StandardSetupOptions standard, List<string> errors)
    {
        const string prefix = "setup.standard";

        if (standard.EditionMode == EditionMode.Edition)
        {
            Known(generator.WindowsEditions, standard.Edition, $"{prefix}.edition", errors);
        }
        else
        {
            Unused(standard.Edition, $"{prefix}.edition", $"{prefix}.editionMode is Edition", errors);
        }

        if (standard.EditionMode == EditionMode.ProductKey)
        {
            Required(standard.ProductKey, $"{prefix}.productKey", errors);
        }
        else
        {
            Unused(standard.ProductKey, $"{prefix}.productKey", $"{prefix}.editionMode is ProductKey", errors);
        }
    }

    private void ValidateScriptedSetup(ScriptedSetupOptions scripted, List<string> errors)
    {
        const string prefix = "setup.scripted";

        if (scripted.ImageMode == ImageSelectionMode.Edition)
        {
            Known(generator.WindowsEditions, scripted.Edition, $"{prefix}.edition", errors);
        }
        else
        {
            Unused(scripted.Edition, $"{prefix}.edition", $"{prefix}.imageMode is Edition", errors);
        }

        if (scripted.ImageMode == ImageSelectionMode.Index)
        {
            if (scripted.ImageIndex is not >= 1)
            {
                errors.Add($"{prefix}.imageIndex must be 1 or greater.");
            }
        }
        else if (scripted.ImageIndex != null)
        {
            errors.Add($"{prefix}.imageIndex is only used when {prefix}.imageMode is Index.");
        }

        if (scripted.ImageMode == ImageSelectionMode.Name)
        {
            Required(scripted.ImageName, $"{prefix}.imageName", errors);
            // The name ends up inside a double-quoted argument in pe.cmd.
            if (scripted.ImageName != null && scripted.ImageName.Contains('"'))
            {
                errors.Add($"{prefix}.imageName must not contain double quotes.");
            }
        }
        else
        {
            Unused(scripted.ImageName, $"{prefix}.imageName", $"{prefix}.imageMode is Name", errors);
        }

        var partitioning = scripted.Partitioning;
        if (partitioning.Mode == PartitionMode.CustomScript)
        {
            Required(partitioning.DiskpartScript, $"{prefix}.partitioning.diskpartScript", errors);
        }
        else
        {
            Unused(partitioning.DiskpartScript, $"{prefix}.partitioning.diskpartScript", $"{prefix}.partitioning.mode is CustomScript", errors);
        }

        if (partitioning.Mode == PartitionMode.Unattended)
        {
            if (partitioning.SystemPartitionSizeMiB < 1)
            {
                errors.Add($"{prefix}.partitioning.systemPartitionSizeMiB must be greater than 0.");
            }
            if (partitioning.RecoveryPartition && partitioning.RecoveryPartitionSizeMiB < 1)
            {
                errors.Add($"{prefix}.partitioning.recoveryPartitionSizeMiB must be greater than 0.");
            }

            var disk = partitioning.TargetDisk;
            if (disk.Mode == TargetDiskMode.Fixed && disk.Index == null)
            {
                errors.Add($"{prefix}.partitioning.targetDisk.index is required when targetDisk.mode is Fixed.");
            }
            if (disk.Index is < 0)
            {
                errors.Add($"{prefix}.partitioning.targetDisk.index must not be negative.");
            }
            if (disk.MinSizeGiB is < 0 || disk.MaxSizeGiB is < 0)
            {
                errors.Add($"{prefix}.partitioning.targetDisk sizes must not be negative.");
            }
        }

        var paging = scripted.PagingFile;
        if (paging.Mode == PagingFileMode.Custom)
        {
            if (paging.InitialSizeMiB is not >= 1 || paging.MaxSizeMiB is not >= 1)
            {
                errors.Add($"{prefix}.pagingFile.initialSizeMiB and maxSizeMiB must both be set and greater than 0.");
            }
            else if (paging.InitialSizeMiB > paging.MaxSizeMiB)
            {
                errors.Add($"{prefix}.pagingFile.initialSizeMiB must not be greater than maxSizeMiB.");
            }
        }
        else if (paging.InitialSizeMiB != null || paging.MaxSizeMiB != null)
        {
            errors.Add($"{prefix}.pagingFile sizes are only used when {prefix}.pagingFile.mode is Custom.");
        }
    }

    private static void ValidateComputerName(ComputerNameOptions computerName, List<string> errors)
    {
        if (computerName.Mode == ComputerNameMode.Custom)
        {
            Required(computerName.Name, "computerName.name", errors);
        }
        else
        {
            Unused(computerName.Name, "computerName.name", "computerName.mode is Custom", errors);
        }
    }

    private static void ValidateAccounts(AccountOptions accounts, List<string> errors)
    {
        if (accounts.Mode != AccountMode.Unattended)
        {
            if (accounts.Users.Count > 0)
            {
                errors.Add("accounts.users is only used when accounts.mode is Unattended.");
            }
            if (accounts.AutoLogon != AutoLogonMode.None)
            {
                errors.Add("accounts.autoLogon is only used when accounts.mode is Unattended.");
            }
            Unused(accounts.BuiltinAdministratorPassword, "accounts.builtinAdministratorPassword", "accounts.mode is Unattended", errors);
            return;
        }

        if (accounts.AutoLogon == AutoLogonMode.BuiltinAdministrator)
        {
            if (accounts.BuiltinAdministratorPassword == null)
            {
                errors.Add("accounts.builtinAdministratorPassword is required when accounts.autoLogon is BuiltinAdministrator.");
            }
        }
        else
        {
            Unused(accounts.BuiltinAdministratorPassword, "accounts.builtinAdministratorPassword", "accounts.autoLogon is BuiltinAdministrator", errors);
        }

        // Upstream checks the remaining account rules: valid names, no duplicates, at least one administrator.
    }

    private void ValidateBloatware(IReadOnlyList<string> bloatware, List<string> errors)
    {
        for (int i = 0; i < bloatware.Count; i++)
        {
            Known(generator.Bloatwares, bloatware[i], $"bloatware[{i}]", errors);
        }
        foreach (var duplicate in bloatware.GroupBy(x => x, StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1))
        {
            errors.Add($"bloatware lists '{duplicate.Key}' more than once.");
        }
    }

    private static void ValidateScripts(ScriptOptions scripts, List<string> errors)
    {
        for (int i = 0; i < scripts.Items.Count; i++)
        {
            if (string.IsNullOrWhiteSpace(scripts.Items[i].Content))
            {
                errors.Add($"scripts.items[{i}].content must not be empty.");
            }
        }
    }

    private static void Known<T>(IReadOnlyDictionary<string, T> collection, string? id, string field, List<string> errors)
    {
        if (string.IsNullOrEmpty(id))
        {
            errors.Add($"{field} is required.");
        }
        else if (!collection.ContainsKey(id))
        {
            errors.Add($"{field} '{id}' is not a known upstream value.");
        }
    }

    private static void Required(string? value, string field, List<string> errors)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add($"{field} is required.");
        }
    }

    private static void Unused(string? value, string field, string condition, List<string> errors)
    {
        if (value != null)
        {
            errors.Add($"{field} is only used when {condition}.");
        }
    }
}
