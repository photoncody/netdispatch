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
  };

  return {
    gen: {
      open: false, id: null, name: '', description: '', f: null, schema: emptySchema,
      preview: '', errors: [], warnings: [], busy: false, saving: false, seq: 0,
      stale: false, loadError: '', unavailable: '',
    },
    genSections: [
      ['region', 'Language'], ['setup', 'PE stage'], ['identity', 'Name and time zone'],
      ['accounts', 'Accounts'], ['oobe', 'Setup experience'], ['bloatware', 'Apps'],
      ['scripts', 'Scripts'], ['switches', 'More settings'],
    ],
    // Flags that have a place of their own in the form ("Setup experience").
    genPlacedFlags: ['HidePowerShellWindows', 'UseNarrator'],

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
                autoIndex: disk.mode === 'Auto' ? (disk.index ?? '') : '',
                minSizeGiB: disk.minSizeGiB ?? 100,
                maxSizeGiB: disk.maxSizeGiB ?? 4000,
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
        wifi: { mode: (m.wifi && m.wifi.mode) || 'Interactive' },
        expressSettings: m.expressSettings || 'DisableAll',
        keepSensitiveFiles: !!m.keepSensitiveFiles,
        bypassNetworkCheck: !!m.bypassNetworkCheck,
        flags,
        bloatware: [...(m.bloatware || [])],
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
      s.expressSettings = f.expressSettings;
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
    genRemove(list, i) {
      list.splice(i, 1);
      this.genChanged();
    },
    genOtherFlags() {
      return this.gen.schema.flags.filter((fl) => !this.genPlacedFlags.includes(fl.name));
    },
    genFlagLabel(name) {
      return name.replace(/([a-z0-9])([A-Z])/g, '$1 $2');
    },
    genFlagCount() {
      if (!this.gen.f) return 0;
      return this.genOtherFlags().filter((fl) => !!this.gen.f.flags[fl.name] !== fl.default).length;
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
