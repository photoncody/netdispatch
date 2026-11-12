// Unattend generator: state and methods mixed into netdispatch() in templates/index.html.
// The markup is templates/unattend/*.html.
//
// The form edits gen.f, a working copy that keeps the fields of every mode, so switching a
// mode back and forth loses nothing. genSettings() builds the settings the server stores and
// the generator reads (HostConfig JSON, server/unattendgen/src/Core/HostConfig.cs). It leaves
// out the fields of modes that are not selected, because the generator rejects them.
//
// Keep Tailwind class names out of this file: the CSS build only scans app/templates.

/* exported unattendGenerator */
// eslint-disable-next-line no-unused-vars
function unattendGenerator() {
  let rowKey = 0;
  const key = () => ++rowKey;
  const num = (v) => (v === '' || v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
  const str = (v) => (typeof v === 'string' ? v : '');
  const emptySchema = {
    loaded: false, defaults: {}, newTemplate: {}, flags: [], imageLanguages: [], userLocales: [],
    keyboards: [], geoLocations: [], timeZones: [], windowsEditions: [], bloatware: [],
    desktopIcons: [], startFolders: [], components: [], visualEffects: [], stickyKeys: [],
  };
  // Windows' own layout, nothing pinned, or the layout typed into the form.
  const layout = (v) => ({ mode: (v && v.mode) || 'Default', content: str(v && v.content) });
  const lockKey = (v) => ({ on: !!(v && v.on), ignore: !!(v && v.ignore) });

  return {
    gen: {
      open: false, id: null, name: '', description: '', f: null, schema: emptySchema,
      preview: '', errors: [], warnings: [], busy: false, saving: false, seq: 0,
      stale: false, loadError: '', unavailable: '',
    },
    genSections: [
      ['region', 'Language'], ['setup', 'PE stage'], ['identity', 'Name and time zone'],
      ['accounts', 'Accounts'], ['oobe', 'Setup experience'], ['bloatware', 'Apps'],
      ['scripts', 'Scripts'], ['switches', 'Windows settings'], ['look', 'Appearance'],
      ['layout', 'Start and taskbar'], ['advanced', 'Advanced'],
    ],
    // Labels for names the generator lists (schema.visualEffects, schema.stickyKeys). A name
    // without a label here still shows, by its own name.
    genEffectLabels: {
      ControlAnimations: 'Animate controls and elements inside windows',
      AnimateMinMax: 'Animate windows when minimising and maximising',
      TaskbarAnimations: 'Animations in the taskbar',
      DWMAeroPeekEnabled: 'Peek at the desktop',
      MenuAnimation: 'Fade or slide menus into view',
      TooltipAnimation: 'Fade or slide tooltips into view',
      SelectionFade: 'Fade out menu items after clicking',
      DWMSaveThumbnailEnabled: 'Save taskbar thumbnail previews',
      CursorShadow: 'Shadow under the mouse pointer',
      ListviewShadow: 'Drop shadows for icon labels on the desktop',
      ThumbnailsOrIcon: 'Show thumbnails instead of icons',
      ListviewAlphaSelect: 'Translucent selection rectangle',
      DragFullWindows: 'Show window contents while dragging',
      ComboBoxAnimation: 'Slide open drop-down lists',
      FontSmoothing: 'Smooth the edges of screen fonts',
      ListBoxSmoothScrolling: 'Smooth-scroll list boxes',
      DropShadow: 'Shadows under windows',
    },
    genStickyLabels: {
      HotKeyActive: 'Pressing Shift five times turns Sticky Keys on',
      Indicator: 'Show the Sticky Keys icon on the taskbar',
      TriState: 'Pressing a modifier key twice locks it',
      TwoKeysOff: 'Turn Sticky Keys off when two keys are pressed at once',
      AudibleFeedback: 'Play a sound when a modifier key is pressed',
      HotKeySound: 'Play a sound when the shortcut turns Sticky Keys on or off',
    },
    // Flags that have a place of their own in the form ("Setup experience").
    genPlacedFlags: ['HidePowerShellWindows', 'UseNarrator'],
    // Labels for the generator's on/off settings, by group: [name, label, help]. The group
    // titles are in templates/unattend/_sec_switches.html. A flag the generator lists that is
    // missing here shows under "Other" with its own name, so a pin bump needs no change.
    genFlagGroups: {
      explorer: [
        ['ShowFileExtensions', 'Show file name extensions'],
        ['LaunchToThisPC', 'Open File Explorer at This PC'],
        ['ClassicContextMenu', 'Use the classic right-click menu', 'Windows 11.'],
        ['HideInfoTip', 'No pop-up descriptions for files and folders'],
      ],
      taskbar: [
        ['LeftTaskbar', 'Align the taskbar to the left'],
        ['HideTaskViewButton', 'Hide the Task View button'],
        ['ShowAllTrayIcons', 'Always show all tray icons'],
        ['ShowEndTask', 'Add "End task" to the taskbar right-click menu'],
        ['DisableWidgets', 'Turn off widgets'],
        ['DisableBingResults', 'No web results in Start menu search'],
        ['DisableAppSuggestions', 'No suggested or promoted apps'],
      ],
      system: [
        ['EnableRemoteDesktop', 'Turn on Remote Desktop'],
        ['EnableLongPaths', 'Allow paths longer than 260 characters'],
        ['AllowPowerShellScripts', 'Allow PowerShell scripts to run', 'Sets the execution policy to RemoteSigned.'],
        ['DisableFastStartup', 'Turn off fast startup'],
        ['DisableSystemRestore', 'Turn off System Restore'],
        ['DisableLastAccess', 'Don\'t record when files were last read'],
        ['TurnOffSystemSounds', 'Turn off system sounds'],
        ['DisablePointerPrecision', 'Turn off mouse acceleration'],
        ['DisableAutomaticRestartSignOn', 'Don\'t sign the user back in after an update restart'],
        ['PreventDeviceApps', 'Don\'t download device makers\' apps', 'Windows otherwise installs them with some drivers.'],
        ['DisableWpbt', 'Don\'t run software stored in the firmware', 'Blocks the Windows Platform Binary Table.'],
        ['DeleteJunctions', 'Delete the compatibility links', 'Such as C:\\Documents and Settings.'],
        ['DeleteWindowsOld', 'Delete C:\\Windows.old'],
      ],
      update: [
        ['PreventAutomaticReboot', 'Don\'t restart for updates while someone is signed in'],
        ['DisableWindowsUpdate', 'Keep Windows Update paused', 'A daily task extends the pause by a week.'],
      ],
      security: [
        ['DisableUac', 'Turn off User Account Control'],
        ['DisableSmartScreen', 'Turn off SmartScreen'],
        ['DisableSac', 'Turn off Smart App Control'],
        ['DisableCoreIsolation', 'Turn off core isolation', 'Memory integrity and virtualisation-based security.'],
        ['PreventDeviceEncryption', 'Don\'t encrypt the disk automatically', 'Stops BitLocker device encryption from starting by itself.'],
        ['HardenSystemDriveAcl', 'Stop ordinary users creating folders in C:\\', 'This one tightens security.'],
      ],
      edge: [
        ['HideEdgeFre', 'Skip Edge\'s first-run screens'],
        ['DisableEdgeStartupBoost', 'No Edge startup boost or background running'],
        ['DeleteEdgeDesktopIcon', 'Remove the Edge desktop shortcut'],
        ['MakeEdgeUninstallable', 'Allow Edge to be uninstalled'],
      ],
      vm: [
        ['VirtIoGuestTools', 'VirtIO guest tools and QEMU guest agent'],
        ['VBoxGuestAdditions', 'VirtualBox Guest Additions'],
        ['VMwareTools', 'VMware Tools'],
        ['ParallelsTools', 'Parallels Tools'],
      ],
    },

    async genLoadSchema() {
      if (this.gen.schema.loaded) return true;
      try {
        const schema = await this.api('GET', '/api/unattend/generator/schema');
        this.gen.schema = { ...emptySchema, ...schema, loaded: true };
        this.gen.unavailable = '';
        return true;
      } catch (e) {
        this.gen.unavailable = 'The unattend generator is not available: ' + e.message;
        this.toast(this.gen.unavailable, true);
        return false;
      }
    },

    async newGenerated() {
      if (!(await this.genLoadSchema())) return;
      this.ua = { open: false, id: null };
      Object.assign(this.gen, {
        open: true, id: null, name: 'New generated template', description: '', stale: false,
        loadError: '', preview: '', errors: [], warnings: [],
      });
      this.gen.f = this.genForm(this.gen.schema.newTemplate);
      this.genPreview();
    },

    // res: a generated template from GET /api/unattend/{id}
    async openGenerated(res) {
      if (!(await this.genLoadSchema())) return;
      this.ua = { open: false, id: null };
      Object.assign(this.gen, {
        open: true, id: res.id, name: res.name, description: res.description || '', stale: !!res.stale,
        loadError: '', preview: '', errors: [], warnings: [],
      });
      this.gen.f = this.genForm(res.settings || {});
      this.genPreview();
    },

    closeGenerator() { this.gen.open = false; },

    genJump(id) {
      const el = document.getElementById('gen-' + id);
      if (!el) return;
      el.open = true;
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    // ------------------------------------------------------------ settings <-> form
    // Stored settings -> form. Missing values take the library's defaults (what the
    // generator would use), except for sections the settings do not use at all, which
    // start from values that make sense when someone switches to them.
    genForm(s) {
      const m = s || {};
      const lang = m.language || {};
      const setup = m.setup || {};
      const std = setup.standard || {};
      const scr = setup.scripted;
      const sc = scr || {};
      const part = sc.partitioning || (scr ? {} : { mode: 'Unattended', layout: 'Automatic' });
      const disk = part.targetDisk || {};
      const paging = sc.pagingFile || {};
      const cn = m.computerName || {};
      const tz = m.timeZone || {};
      const acc = m.accounts || {};
      const pe = m.passwordExpiration || {};
      const lo = m.lockout || {};
      const scripts = m.scripts || {};
      const wifi = m.wifi || {};
      const wp = wifi.profile || {};
      const lk = m.lockKeys || {};
      const sticky = m.stickyKeys || {};
      const wall = m.wallpaper || {};
      const col = m.colors || {};
      const fx = m.visualEffects || {};
      const sch = this.gen.schema;
      // One on/off value per name the generator lists. With nothing stored, the form shows
      // what Windows does by itself.
      const checks = (names, stored, initial) => Object.fromEntries(names.map((n) => [n, stored ? !!stored(n) : initial(n)]));
      // The generator matches catalog ids without regard to case. The form compares them
      // exactly, so stored ids take the spelling the schema lists.
      const canon = (items, id) => (items.find((i) => i.id.toLowerCase() === String(id).toLowerCase()) || { id }).id;
      const icons = m.desktopIcons && Object.fromEntries(Object.entries(m.desktopIcons).map(([id, v]) => [canon(sch.desktopIcons, id), v]));
      const folders = m.startFolders && m.startFolders.map((id) => canon(sch.startFolders, id));
      // Custom without flags means no option is on. Every box is ticked only when the
      // stored mode is another one, as the starting point for switching to Custom.
      const stickyFlags = sticky.mode === 'Custom' ? (sticky.flags || []) : null;
      const arch = m.processorArchitectures || ['Amd64'];
      const locales = (lang.locales && lang.locales.length ? lang.locales : [{ locale: 'en-US', keyboard: '00000409' }]);
      const flags = {};
      for (const fl of this.gen.schema.flags) flags[fl.name] = (m.flags && fl.name in m.flags) ? !!m.flags[fl.name] : fl.default;

      return {
        arch: { X86: arch.includes('X86'), Amd64: arch.includes('Amd64'), Arm64: arch.includes('Arm64') },
        lang: {
          mode: lang.mode || 'Interactive',
          imageLanguage: lang.imageLanguage || 'en-US',
          geoLocation: lang.geoLocation || '244',
          locales: locales.map((p) => ({ locale: p.locale, keyboard: p.keyboard, _k: key() })),
        },
        setup: {
          mode: setup.mode || 'Standard',
          peScript: str(setup.peScript),
          activationKey: str(setup.activationKey),
          std: {
            editionMode: std.editionMode || 'Interactive',
            edition: std.edition || 'pro',
            productKey: str(std.productKey),
            bypassRequirementsCheck: std.bypassRequirementsCheck ?? true,
          },
          scr: {
            imageMode: sc.imageMode || (scr ? 'Interactive' : 'Edition'),
            edition: sc.edition || 'pro',
            imageIndex: sc.imageIndex ?? 1,
            imageName: str(sc.imageName),
            part: {
              mode: part.mode || 'Interactive',
              layout: part.layout || 'Gpt',
              recoveryPartition: part.recoveryPartition ?? true,
              systemPartitionSizeMiB: part.systemPartitionSizeMiB ?? 300,
              recoveryPartitionSizeMiB: part.recoveryPartitionSizeMiB ?? 1000,
              diskpartScript: str(part.diskpartScript),
              disk: {
                mode: disk.mode || 'Fixed',
                index: disk.mode === 'Auto' ? 0 : (disk.index ?? 0),
                // Like the sizes below: an absent index means the default (disk 0), and only an
                // explicit null means any disk.
                autoIndex: disk.mode === 'Auto' ? ('index' in disk ? (disk.index ?? '') : 0) : '',
                // An explicit null means "no limit" and must stay empty; only absent values
                // get the defaults.
                minSizeGiB: 'minSizeGiB' in disk ? (disk.minSizeGiB ?? '') : 100,
                maxSizeGiB: 'maxSizeGiB' in disk ? (disk.maxSizeGiB ?? '') : 4000,
                requireNoPartitions: disk.requireNoPartitions ?? true,
              },
            },
            paging: {
              mode: paging.mode || 'Automatic',
              initialSizeMiB: paging.initialSizeMiB ?? '',
              maxSizeMiB: paging.maxSizeMiB ?? '',
            },
            compactOs: !!sc.compactOs,
            disable8Dot3Names: !!sc.disable8Dot3Names,
            disableDefender: !!sc.disableDefender,
            skipIntegrityCheck: !!sc.skipIntegrityCheck,
            pauseBeforeFormatting: !!sc.pauseBeforeFormatting,
            pauseBeforeReboot: !!sc.pauseBeforeReboot,
          },
        },
        computerName: { mode: cn.mode || 'Random', name: str(cn.name) },
        timeZone: { mode: tz.mode || 'Implicit', id: tz.id || 'UTC' },
        accounts: {
          mode: acc.mode || 'InteractiveMicrosoft',
          users: (acc.users || []).map((u) => ({
            name: str(u.name), displayName: str(u.displayName), password: str(u.password),
            group: u.group || 'Administrators', _k: key(),
          })),
          autoLogon: acc.autoLogon || 'None',
          builtinAdministratorPassword: str(acc.builtinAdministratorPassword),
          obscurePasswords: !!acc.obscurePasswords,
        },
        passwordExpiration: { mode: pe.mode || 'Default', maxAgeDays: pe.maxAgeDays ?? 42 },
        lockout: {
          mode: lo.mode || 'Default',
          threshold: lo.threshold ?? 10,
          durationMinutes: lo.durationMinutes ?? 10,
          windowMinutes: lo.windowMinutes ?? 10,
        },
        wifi: {
          mode: wifi.mode || 'Interactive',
          ssid: str(wp.ssid),
          authentication: wp.authentication || 'Wpa2',
          password: str(wp.password),
          connectAutomatically: wp.connectAutomatically ?? true,
          hidden: !!wp.hidden,
          profileXml: str(wifi.profileXml),
        },
        processAudit: m.processAudit || 'Disabled',
        appLocker: { on: m.appLockerPolicy != null, xml: str(m.appLockerPolicy) },
        components: (m.components || []).map((c) => ({ component: canon(sch.components, c.component), pass: c.pass, xml: str(c.xml), _k: key() })),
        lockKeys: { on: !!m.lockKeys, capsLock: lockKey(lk.capsLock), numLock: lockKey(lk.numLock), scrollLock: lockKey(lk.scrollLock) },
        stickyKeys: {
          mode: sticky.mode || 'Default',
          flags: checks(sch.stickyKeys, stickyFlags && ((n) => stickyFlags.includes(n)), () => true),
        },
        wallpaper: { mode: wall.mode || 'Default', color: wall.color || '#000000', script: str(wall.script) },
        lockScreen: { on: m.lockScreenScript != null, script: str(m.lockScreenScript) },
        colors: {
          on: !!m.colors,
          systemTheme: col.systemTheme || 'Light',
          appsTheme: col.appsTheme || 'Light',
          transparency: col.transparency ?? true,
          accentOnStart: !!col.accentOnStart,
          accentOnBorders: !!col.accentOnBorders,
          accentColor: col.accentColor || '#0078D4',
        },
        startPins: layout(m.startPins),
        startTiles: layout(m.startTiles),
        taskbarIcons: layout(m.taskbarIcons),
        effects: {
          mode: fx.mode || 'Default',
          // An effect the stored settings leave out is left alone by the generator; the form
          // has no third state, so it shows as on.
          items: checks(sch.visualEffects, fx.effects && ((n) => fx.effects[n] ?? true), () => true),
        },
        desktopIcons: {
          on: !!m.desktopIcons,
          items: checks(sch.desktopIcons.map((i) => i.id), icons && ((n) => icons[n] ?? n === 'RecycleBin'), (n) => n === 'RecycleBin'),
        },
        startFolders: {
          on: m.startFolders != null,
          items: checks(sch.startFolders.map((i) => i.id), folders && ((n) => folders.includes(n)), () => false),
        },
        expressSettings: m.expressSettings || 'DisableAll',
        hiddenFiles: m.hiddenFiles || 'Default',
        taskbarSearch: m.taskbarSearch || 'Box',
        keepSensitiveFiles: !!m.keepSensitiveFiles,
        bypassNetworkCheck: !!m.bypassNetworkCheck,
        flags,
        bloatware: (m.bloatware || []).map((id) => canon(sch.bloatware, id)),
        scripts: {
          items: (scripts.items || []).map((x) => ({ phase: x.phase, type: x.type, content: str(x.content), _k: key() })),
          restartExplorer: !!scripts.restartExplorer,
        },
      };
    },

    // Form -> settings, with only the selected modes' fields.
    genSettings() {
      const f = this.gen.f;
      const s = { schemaVersion: 1 };
      s.processorArchitectures = ['Amd64', 'Arm64', 'X86'].filter((a) => f.arch[a]);

      s.language = { mode: f.lang.mode };
      if (f.lang.mode === 'Unattended') {
        Object.assign(s.language, {
          imageLanguage: f.lang.imageLanguage,
          locales: f.lang.locales.map((p) => ({ locale: p.locale, keyboard: p.keyboard })),
          geoLocation: f.lang.geoLocation,
        });
      }

      const setup = { mode: f.setup.mode };
      if (f.setup.mode === 'Standard') {
        const std = f.setup.std;
        setup.standard = { editionMode: std.editionMode, bypassRequirementsCheck: !!std.bypassRequirementsCheck };
        if (std.editionMode === 'Edition') setup.standard.edition = std.edition;
        if (std.editionMode === 'ProductKey') setup.standard.productKey = std.productKey.trim();
      } else if (f.setup.mode === 'Scripted') {
        const scr = f.setup.scr;
        const out = { imageMode: scr.imageMode };
        if (scr.imageMode === 'Edition') out.edition = scr.edition;
        if (scr.imageMode === 'Index') out.imageIndex = num(scr.imageIndex);
        if (scr.imageMode === 'Name') out.imageName = scr.imageName;
        const p = scr.part;
        out.partitioning = { mode: p.mode };
        if (p.mode === 'Unattended') {
          const d = p.disk;
          const disk = { mode: d.mode };
          if (d.mode === 'Fixed') disk.index = num(d.index);
          if (d.mode === 'Auto') {
            Object.assign(disk, {
              index: num(d.autoIndex), minSizeGiB: num(d.minSizeGiB), maxSizeGiB: num(d.maxSizeGiB),
              requireNoPartitions: !!d.requireNoPartitions,
            });
          }
          Object.assign(out.partitioning, {
            layout: p.layout, recoveryPartition: !!p.recoveryPartition,
            systemPartitionSizeMiB: num(p.systemPartitionSizeMiB) ?? 0,
            recoveryPartitionSizeMiB: num(p.recoveryPartitionSizeMiB) ?? 0,
            targetDisk: disk,
          });
        }
        if (p.mode === 'CustomScript') out.partitioning.diskpartScript = p.diskpartScript;
        out.pagingFile = { mode: scr.paging.mode };
        if (scr.paging.mode === 'Custom') {
          out.pagingFile.initialSizeMiB = num(scr.paging.initialSizeMiB);
          out.pagingFile.maxSizeMiB = num(scr.paging.maxSizeMiB);
        }
        for (const k of ['compactOs', 'disable8Dot3Names', 'disableDefender', 'skipIntegrityCheck', 'pauseBeforeFormatting', 'pauseBeforeReboot']) {
          if (scr[k]) out[k] = true;
        }
        setup.scripted = out;
      } else {
        setup.peScript = f.setup.peScript;
      }
      if (f.setup.activationKey.trim()) setup.activationKey = f.setup.activationKey.trim();
      s.setup = setup;

      s.computerName = { mode: f.computerName.mode };
      if (f.computerName.mode === 'Custom') s.computerName.name = f.computerName.name.trim();
      s.timeZone = { mode: f.timeZone.mode };
      if (f.timeZone.mode === 'Explicit') s.timeZone.id = f.timeZone.id;

      const acc = f.accounts;
      s.accounts = { mode: acc.mode };
      if (acc.mode === 'Unattended') {
        Object.assign(s.accounts, {
          users: acc.users.map((u) => ({ name: u.name.trim(), displayName: u.displayName, password: u.password, group: u.group })),
          autoLogon: acc.autoLogon,
          obscurePasswords: !!acc.obscurePasswords,
        });
        if (acc.autoLogon === 'BuiltinAdministrator') s.accounts.builtinAdministratorPassword = acc.builtinAdministratorPassword;
      }
      s.passwordExpiration = { mode: f.passwordExpiration.mode };
      if (f.passwordExpiration.mode === 'Custom') s.passwordExpiration.maxAgeDays = num(f.passwordExpiration.maxAgeDays);
      s.lockout = { mode: f.lockout.mode };
      if (f.lockout.mode === 'Custom') {
        Object.assign(s.lockout, {
          threshold: num(f.lockout.threshold), durationMinutes: num(f.lockout.durationMinutes), windowMinutes: num(f.lockout.windowMinutes),
        });
      }
      s.wifi = { mode: f.wifi.mode };
      if (f.wifi.mode === 'Profile') {
        s.wifi.profile = {
          ssid: f.wifi.ssid, authentication: f.wifi.authentication,
          connectAutomatically: !!f.wifi.connectAutomatically, hidden: !!f.wifi.hidden,
        };
        if (f.wifi.authentication !== 'Open') s.wifi.profile.password = f.wifi.password;
      }
      if (f.wifi.mode === 'ProfileXml') s.wifi.profileXml = f.wifi.profileXml;
      s.expressSettings = f.expressSettings;
      if (f.hiddenFiles !== 'Default') s.hiddenFiles = f.hiddenFiles;
      if (f.taskbarSearch !== 'Box') s.taskbarSearch = f.taskbarSearch;
      if (f.keepSensitiveFiles) s.keepSensitiveFiles = true;
      if (f.bypassNetworkCheck) s.bypassNetworkCheck = true;

      const flags = {};
      for (const fl of this.gen.schema.flags) {
        const v = !!f.flags[fl.name];
        if (v !== fl.default) flags[fl.name] = v;
      }
      if (Object.keys(flags).length) s.flags = flags;
      if (f.bloatware.length) s.bloatware = [...f.bloatware];
      const items = f.scripts.items.filter((x) => x.content.trim()).map((x) => ({ phase: x.phase, type: x.type, content: x.content }));
      if (items.length || f.scripts.restartExplorer) s.scripts = { items, restartExplorer: !!f.scripts.restartExplorer };

      const on = (o) => Object.keys(o).filter((n) => o[n]);
      if (f.wallpaper.mode === 'SolidColor') s.wallpaper = { mode: 'SolidColor', color: f.wallpaper.color.trim() };
      if (f.wallpaper.mode === 'Script') s.wallpaper = { mode: 'Script', script: f.wallpaper.script };
      if (f.lockScreen.on) s.lockScreenScript = f.lockScreen.script;
      if (f.colors.on) {
        const c = f.colors;
        s.colors = {
          systemTheme: c.systemTheme, appsTheme: c.appsTheme, transparency: !!c.transparency,
          accentOnStart: !!c.accentOnStart, accentOnBorders: !!c.accentOnBorders, accentColor: c.accentColor.trim(),
        };
      }
      if (f.effects.mode !== 'Default') {
        s.visualEffects = { mode: f.effects.mode };
        if (f.effects.mode === 'Custom') {
          s.visualEffects.effects = Object.fromEntries(Object.entries(f.effects.items).map(([n, v]) => [n, !!v]));
        }
      }
      if (f.desktopIcons.on) s.desktopIcons = Object.fromEntries(Object.entries(f.desktopIcons.items).map(([n, v]) => [n, !!v]));
      for (const k of ['startPins', 'startTiles', 'taskbarIcons']) {
        if (f[k].mode === 'Empty') s[k] = { mode: 'Empty' };
        if (f[k].mode === 'Custom') s[k] = { mode: 'Custom', content: f[k].content };
      }
      if (f.startFolders.on) s.startFolders = on(f.startFolders.items);
      if (f.lockKeys.on) {
        const k = f.lockKeys;
        s.lockKeys = {
          capsLock: { on: !!k.capsLock.on, ignore: !!k.capsLock.ignore },
          numLock: { on: !!k.numLock.on, ignore: !!k.numLock.ignore },
          scrollLock: { on: !!k.scrollLock.on, ignore: !!k.scrollLock.ignore },
        };
      }
      if (f.stickyKeys.mode === 'Disabled') s.stickyKeys = { mode: 'Disabled' };
      if (f.stickyKeys.mode === 'Custom') s.stickyKeys = { mode: 'Custom', flags: on(f.stickyKeys.flags) };
      if (f.processAudit !== 'Disabled') s.processAudit = f.processAudit;
      if (f.appLocker.on) s.appLockerPolicy = f.appLocker.xml;
      const components = f.components.filter((c) => c.xml.trim()).map((c) => ({ component: c.component, pass: c.pass, xml: c.xml }));
      if (components.length) s.components = components;
      return s;
    },

    // ------------------------------------------------------------ form helpers
    genLocaleChanged(pair) {
      const loc = this.gen.schema.userLocales.find((l) => l.id === pair.locale);
      if (loc && loc.keyboard) pair.keyboard = loc.keyboard;
    },
    genAddLocale() {
      this.gen.f.lang.locales.push({ locale: 'en-US', keyboard: '00000409', _k: key() });
      this.genChanged();
    },
    genAddUser() {
      const users = this.gen.f.accounts.users;
      users.push({ name: '', displayName: '', password: '', group: users.length ? 'Users' : 'Administrators', _k: key() });
    },
    genAddScript() {
      this.gen.f.scripts.items.push({ phase: 'FirstLogon', type: 'Ps1', content: '', _k: key() });
    },
    // Passes the generator allows for a component.
    genComponentPasses(id) {
      const c = this.gen.schema.components.find((x) => x.id === id);
      return c ? c.passes : [];
    },
    genComponentChanged(row) {
      const passes = this.genComponentPasses(row.component);
      if (!passes.includes(row.pass)) row.pass = passes.includes('specialize') ? 'specialize' : (passes[0] || '');
    },
    genAddComponent() {
      const row = { component: 'Microsoft-Windows-Shell-Setup', pass: 'specialize', xml: '', _k: key() };
      if (!this.genComponentPasses(row.component).length && this.gen.schema.components.length) row.component = this.gen.schema.components[0].id;
      this.genComponentChanged(row);
      this.gen.f.components.push(row);
    },
    // names: a list from the schema; labels: one of the label tables above.
    genNamed(names, labels) {
      return names.map((name) => ({ name, label: labels[name] || name.replace(/([a-z0-9])([A-Z])/g, '$1 $2') }));
    },
    genRemove(list, i) {
      list.splice(i, 1);
      this.genChanged();
    },
    // The flags of one group that this generator build has, as { name, label, help }.
    // "other" is every flag without a label here and without a place elsewhere in the form.
    genGroupFlags(group) {
      const have = new Set(this.gen.schema.flags.map((fl) => fl.name));
      if (group !== 'other') {
        return (this.genFlagGroups[group] || []).filter((x) => have.has(x[0]))
          .map(([name, label, help]) => ({ name, label, help: help || '' }));
      }
      const known = new Set(this.genPlacedFlags);
      for (const list of Object.values(this.genFlagGroups)) for (const x of list) known.add(x[0]);
      return this.gen.schema.flags.filter((fl) => !known.has(fl.name))
        .map((fl) => ({ name: fl.name, label: fl.name.replace(/([a-z0-9])([A-Z])/g, '$1 $2'), help: '' }));
    },
    // How many settings in the "Windows settings" section differ from the defaults.
    genFlagCount() {
      const f = this.gen.f;
      if (!f) return 0;
      const flags = this.gen.schema.flags
        .filter((fl) => !this.genPlacedFlags.includes(fl.name) && !!f.flags[fl.name] !== fl.default).length;
      return flags + (f.hiddenFiles !== 'Default' ? 1 : 0) + (f.taskbarSearch !== 'Box' ? 1 : 0);
    },

    // ------------------------------------------------------------ server
    genChanged() { this.genPreview(); },

    async genPreview() {
      if (!this.gen.f) return;
      const seq = ++this.gen.seq;
      this.gen.busy = true;
      try {
        const res = await this.api('POST', '/api/unattend/generator/preview', { settings: this.genSettings() });
        if (seq !== this.gen.seq) return;
        this.gen.preview = res.rendered;
        this.gen.errors = [];
        this.gen.warnings = res.warnings || [];
      } catch (e) {
        if (seq !== this.gen.seq) return;
        this.gen.errors = e.message.split('\n');
      } finally {
        if (seq === this.gen.seq) this.gen.busy = false;
      }
    },

    async saveGenerated() {
      this.gen.saving = true;
      const body = { name: this.gen.name, description: this.gen.description || null, settings: this.genSettings() };
      try {
        const res = this.gen.id
          ? await this.api('PUT', `/api/unattend/generator/${this.gen.id}`, body)
          : await this.api('POST', '/api/unattend/generator', body);
        this.gen.id = res.id;
        this.gen.stale = false;
        this.gen.errors = [];
        this.gen.warnings = res.warnings || [];
        this.toast('Template saved');
        this.loadUnattends();
      } catch (e) {
        this.gen.errors = e.message.split('\n');
        this.toast('Not saved: ' + e.message, true);
      } finally {
        this.gen.saving = false;
      }
    },

    // Copies the template as it was last saved; works for raw XML templates too.
    async duplicateUnattend(id) {
      try {
        const res = await this.api('POST', `/api/unattend/${id}/duplicate`);
        await this.loadUnattends();
        this.toast('Copied as ' + res.name);
        this.editUnattend(res);
      } catch (e) { this.toast(e.message, true); }
    },

    async detachGenerated() {
      if (!confirm(`Convert ${this.gen.name} to raw XML? Its XML stays as it is and you edit it by hand from now on. The generator settings are discarded.`)) return;
      try {
        const res = await this.api('POST', `/api/unattend/${this.gen.id}/detach`);
        this.gen.open = false;
        this.loadUnattends();
        this.editUnattend(res);
        this.toast('Converted to raw XML');
      } catch (e) { this.toast(e.message, true); }
    },

    async deleteGenerated() {
      if (!confirm(`Delete template ${this.gen.name}?`)) return;
      try {
        await this.api('DELETE', `/api/unattend/${this.gen.id}`);
        this.gen.open = false;
        this.loadUnattends();
      } catch (e) { this.toast(e.message, true); }
    },
  };
}
