using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Windows;

/// <summary>Where USB Creator remembers its hub and creator key:
/// <c>%APPDATA%\netdispatch\USB Creator\settings.json</c>. The key is encrypted with DPAPI
/// for the current Windows user.</summary>
public sealed record CreatorSettings(string? HubUrl, string? ProtectedKey, string? CreatorName)
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("netdispatch USB Creator");

    public static string Folder =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "netdispatch", "USB Creator");

    private static string FilePath => Path.Combine(Folder, "settings.json");

    public static CreatorSettings Load()
    {
        try
        {
            return JsonSerializer.Deserialize<CreatorSettings>(File.ReadAllText(FilePath), HubJson.Options) ?? new(null, null, null);
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            return new(null, null, null);
        }
    }

    public void Save()
    {
        Directory.CreateDirectory(Folder);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(this, HubJson.Options));
    }

    public string? CreatorKey
    {
        get
        {
            if (ProtectedKey is null)
            {
                return null;
            }
            try
            {
                return Encoding.UTF8.GetString(
                    ProtectedData.Unprotect(Convert.FromBase64String(ProtectedKey), Entropy, DataProtectionScope.CurrentUser));
            }
            catch (Exception e) when (e is CryptographicException or FormatException)
            {
                return null;
            }
        }
    }

    public CreatorSettings WithKey(string hubUrl, string key, string name) =>
        new(hubUrl, Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(key), Entropy, DataProtectionScope.CurrentUser)), name);
}
