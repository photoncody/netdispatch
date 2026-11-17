using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Threading;
using Netdispatch.UsbCreator.Core;
using Netdispatch.UsbCreator.Windows;

namespace Netdispatch.UsbCreator.App;

/// <summary>The one window: connect to a hub, then make or update sticks. Built in code
/// (no XAML) to keep the build simple.</summary>
internal sealed class MainWindow : Window
{
    private readonly bool _allowVirtual;
    private readonly TextBox _hubUrl = new() { PlaceholderText = "https://netdispatch.example.com", MinWidth = 360 };
    private readonly Button _pair = new() { Content = "Pair with hub" };
    private readonly Button _forget = new() { Content = "Forget hub", IsVisible = false };
    private readonly TextBlock _hubStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _code = new() { FontSize = 32, FontWeight = FontWeight.Bold, IsVisible = false };
    private readonly ComboBox _profiles = new() { MinWidth = 360, PlaceholderText = "Profile" };
    private readonly ListBox _disks = new() { Height = 120 };
    private readonly Button _refresh = new() { Content = "Refresh" };
    private readonly TextBox _name = new() { PlaceholderText = "Stick name, e.g. Service desk stick 1", MinWidth = 360 };
    private readonly CheckBox _copy = new() { Content = "Copy the profile's images (for machines without a network)", IsChecked = true };
    private readonly TextBlock _sizeInfo = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.Gray };
    private readonly Button _create = new() { Content = "Create stick" };
    private readonly Button _update = new() { Content = "Update stick" };
    private readonly ProgressBar _progress = new() { Minimum = 0, Maximum = 100, Height = 18 };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBox _log = new() { IsReadOnly = true, AcceptsReturn = true, Height = 140, TextWrapping = TextWrapping.Wrap };
    private readonly StackPanel _stickPanel;
    private CreatorSettings _settings = CreatorSettings.Load();
    private CancellationTokenSource? _busy;

    public MainWindow(string[] args)
    {
        _allowVirtual = args.Contains("--allow-virtual-disks");
        Title = "netdispatch USB Creator";
        Width = 640;
        Height = 760;
        _stickPanel = new StackPanel
        {
            Spacing = 8,
            Children =
            {
                Heading("2. Make or update a stick"),
                _profiles,
                new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { new TextBlock { Text = "USB drive", VerticalAlignment = VerticalAlignment.Center }, _refresh } },
                _disks,
                _name,
                _copy,
                _sizeInfo,
                new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _create, _update } },
                _progress,
                _status,
                _log,
            },
        };
        Content = new ScrollViewer
        {
            Content = new StackPanel
            {
                Margin = new Thickness(20),
                Spacing = 8,
                Children =
                {
                    new TextBlock { Text = "netdispatch USB Creator", FontSize = 22, FontWeight = FontWeight.SemiBold },
                    new TextBlock
                    {
                        Text = "Makes netdispatch USB sticks: they boot machines into netdispatch over the network, "
                            + "and carry Windows images for machines that can't reach the hub.",
                        TextWrapping = TextWrapping.Wrap,
                    },
                    Heading("1. Hub"),
                    new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _hubUrl, _pair, _forget } },
                    _code,
                    _hubStatus,
                    _stickPanel,
                },
            },
        };
        _pair.Click += async (_, _) => await PairAsync();
        _forget.Click += (_, _) => { _settings = new CreatorSettings(null, null, null); _settings.Save(); ShowHub(); };
        _refresh.Click += async (_, _) => await LoadAsync();
        _profiles.SelectionChanged += (_, _) => UpdateButtons();
        _disks.SelectionChanged += (_, _) => UpdateButtons();
        _copy.IsCheckedChanged += (_, _) => UpdateButtons();
        _name.TextChanged += (_, _) => UpdateButtons();
        _create.Click += async (_, _) => await RunAsync(create: true);
        _update.Click += async (_, _) => await RunAsync(create: false);
        Opened += async (_, _) => { ShowHub(); await LoadAsync(); };
    }

    private static TextBlock Heading(string text) =>
        new() { Text = text, FontSize = 16, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 12, 0, 0) };

    private HubClient? Hub => _settings.HubUrl is { } url ? new HubClient(Http.Client, new Uri(url)) : null;

    private Profile? SelectedProfile => (_profiles.SelectedItem as ComboBoxItem)?.Tag as Profile;

    private UsbDisk? SelectedDisk => (_disks.SelectedItem as ListBoxItem)?.Tag as UsbDisk;

    private void ShowHub()
    {
        var paired = _settings.HubUrl is not null && _settings.CreatorKey is not null;
        _hubUrl.Text = _settings.HubUrl ?? _hubUrl.Text;
        _hubUrl.IsEnabled = !paired;
        _pair.IsVisible = !paired;
        _forget.IsVisible = paired;
        _hubStatus.Text = paired
            ? $"Paired with {_settings.HubUrl} as \"{_settings.CreatorName}\"."
            : "Enter the hub's address and pair: an administrator approves the code under Boot media → USB Creator.";
        _stickPanel.IsEnabled = paired;
    }

    private async Task PairAsync()
    {
        try
        {
            var url = HubClient.ParseHubUrl(_hubUrl.Text ?? "");
            var hub = new HubClient(Http.Client, url);
            var start = await hub.StartPairingAsync($"USB Creator on {Environment.MachineName}");
            _code.Text = start.Code;
            _code.IsVisible = true;
            _pair.IsEnabled = false;
            _hubStatus.Text = "Ask an administrator to approve this code on the hub: Boot media → USB Creator.";
            var until = DateTime.UtcNow.AddSeconds(start.ExpiresIn);
            while (DateTime.UtcNow < until)
            {
                await Task.Delay(3000);
                var poll = await hub.PollPairingAsync(start.PollToken);
                if (poll is { Status: "approved", CreatorKey: { } key })
                {
                    _settings = _settings.WithKey(url.ToString(), key, poll.Name ?? "");
                    _settings.Save();
                    _code.IsVisible = false;
                    ShowHub();
                    await LoadAsync();
                    return;
                }
            }
            _hubStatus.Text = "The code expired. Pair again.";
        }
        catch (Exception e) when (e is HubException or HttpRequestException or IOException)
        {
            _hubStatus.Text = "Pairing failed: " + e.Message;
        }
        finally
        {
            _code.IsVisible = false;
            _pair.IsEnabled = true;
        }
    }

    private async Task LoadAsync()
    {
        if (Hub is not { } hub || _settings.CreatorKey is not { } key)
        {
            return;
        }
        try
        {
            var profiles = await hub.GetProfilesAsync(new HubAuth(CreatorKey: key));
            _profiles.ItemsSource = profiles.Profiles
                .Select(p => new ComboBoxItem { Content = $"{p.Name} ({p.Entries.Count} image(s))", Tag = p }).ToList();
            if (profiles.Profiles.Count > 0)
            {
                _profiles.SelectedIndex = 0;
            }
        }
        catch (Exception e) when (e is HubException or HttpRequestException)
        {
            _status.Text = "Could not load the profiles: " + e.Message;
        }
        try
        {
            var disks = await WindowsDisks.ListAsync(_allowVirtual);
            _disks.ItemsSource = disks.Select(d => new ListBoxItem
            {
                Content = d.Display + (d.Roots is not null ? "  — netdispatch stick" : ""),
                Tag = d,
            }).ToList();
            if (disks.Count == 0)
            {
                _status.Text = "No USB drive found. Plug one in and press Refresh.";
            }
        }
        catch (IOException e)
        {
            _status.Text = "Could not list the drives: " + e.Message;
        }
        UpdateButtons();
    }

    private void UpdateButtons()
    {
        var profile = SelectedProfile;
        var disk = SelectedDisk;
        var copy = _copy.IsChecked == true;
        var idle = _busy is null;
        if (profile is not null)
        {
            var bootMb = StickSizing.BootMegabytes(copy ? profile.BootBytes : 0);
            var needed = StickSizing.RequiredBytes(copy ? profile.DataBytes : 0, bootMb);
            var problems = profile.Entries.Where(e => e.Error is not null).Select(e => $"{e.Label}: {e.Error}");
            _sizeInfo.Text = $"Needs a drive of at least {needed / 1_000_000_000.0:0.#} GB."
                + (disk is not null && disk.Size < needed ? $" {disk.Name} is too small." : "")
                + string.Concat(problems.Select(p => "\n" + p));
        }
        _create.IsEnabled = idle && profile is not null && disk is not null && !string.IsNullOrWhiteSpace(_name.Text);
        _update.IsEnabled = idle && disk?.Roots is not null;
    }

    private async Task RunAsync(bool create)
    {
        if (Hub is not { } hub || _settings.CreatorKey is not { } key || SelectedDisk is not { } disk)
        {
            return;
        }
        // Read the controls here, on the UI thread: Avalonia refuses access from the worker.
        var profile = SelectedProfile;
        var name = (_name.Text ?? "").Trim();
        var copy = _copy.IsChecked == true;
        if (create && (profile is null || name.Length == 0))
        {
            return;
        }
        if (create && !await Confirm.AskAsync(this, $"Erase {disk.Display}?",
                $"Everything on {disk.Display} is deleted, and it becomes a netdispatch stick for the profile {profile!.Name}."))
        {
            return;
        }
        _busy = new CancellationTokenSource();
        var token = _busy.Token;
        UpdateButtons();
        _log.Text = "";
        var log = new Progress<string>(line => { _log.Text += line + Environment.NewLine; _status.Text = line; });
        var progress = new Progress<SyncProgress>(p =>
        {
            _progress.Value = p.BytesTotal > 0 ? p.BytesDone * 100.0 / p.BytesTotal : 0;
            _status.Text = p.Stage == "copy"
                ? $"{p.Image}: {p.File} ({p.BytesDone / 1_000_000} of {p.BytesTotal / 1_000_000} MB)"
                : p.Stage;
        });
        try
        {
            var writer = new StickWriter(hub, key, log, progress);
            var result = create
                ? await Task.Run(() => writer.CreateAsync(disk, profile!, name, copy, token))
                : await Task.Run(() => writer.UpdateAsync(disk, copy, token));
            foreach (var problem in result.Problems)
            {
                ((IProgress<string>)log).Report("Warning: " + problem);
            }
            _status.Text = $"Done: {result.Have.Count} image(s) on the stick. You can remove it.";
            _progress.Value = 100;
        }
        catch (Exception e) when (e is HubException or HttpRequestException or IOException or UnauthorizedAccessException or OperationCanceledException)
        {
            _status.Text = "Failed: " + e.Message;
            _log.Text += "Failed: " + e.Message + Environment.NewLine;
        }
        finally
        {
            _busy = null;
            await LoadAsync();
        }
    }
}

/// <summary>A yes/no question.</summary>
internal static class Confirm
{
    public static async Task<bool> AskAsync(Window owner, string title, string text)
    {
        var yes = new Button { Content = "Erase and continue" };
        var no = new Button { Content = "Cancel", IsDefault = true };
        var dialog = new Window
        {
            Title = title,
            Width = 440,
            SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            CanResize = false,
            Content = new StackPanel
            {
                Margin = new Thickness(20),
                Spacing = 16,
                Children =
                {
                    new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap },
                    new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right, Children = { no, yes } },
                },
            },
        };
        yes.Click += (_, _) => dialog.Close(true);
        no.Click += (_, _) => dialog.Close(false);
        return await Dispatcher.UIThread.InvokeAsync(() => dialog.ShowDialog<bool>(owner));
    }
}
