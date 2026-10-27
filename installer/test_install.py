#!/usr/bin/env python3
"""Run the generated installer with isolated home, system paths and service stubs."""
from pathlib import Path
import hashlib
import http.server
import json
import fcntl
import os
import pty
import shutil
import shlex
import select
import subprocess
import struct
import tarfile
import tempfile
import termios
import threading
import time
import unittest

DIST = Path(__file__).resolve().parents[1]
INSTALLER = DIST / 'install.sh'
PAYLOAD = DIST / 'payload'
ELF = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\x00'

class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global INSTALLER
        cls.build_tmp = tempfile.TemporaryDirectory(prefix='zeroday-fixture-installer-')
        directory = Path(cls.build_tmp.name)
        policy = json.loads((DIST/'installer/steam-compatibility.json').read_text())
        policy['clients'] = [{'build': '1792618285',
                             'steamclient_sha256': hashlib.sha256(ELF).hexdigest(),
                             'steamui_sha256': hashlib.sha256(ELF).hexdigest()}]
        (directory/'policy.json').write_text(json.dumps(policy))
        INSTALLER = directory/'install.sh'
        subprocess.run(['python3', str(DIST/'installer-ui/build.py'), '--template',
                        str(DIST/'installer/install.sh.in'), '--compatibility',
                        str(directory/'policy.json'), '--output', str(INSTALLER)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='zeroday install test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'
        self.steam = self.home/'.local/share/Steam'
        self.zd = self.home/'.local/share/ZeroDay'
        self.config = self.home/'.config/ZeroDay/config.yaml'
        self.jup = self.root/'usr/bin/steam-jupiter'
        self.canon = self.root/'usr/local/share/zeroday/steam-jupiter.patched'
        self.binsteam = self.root/'usr/lib/steam/bin_steam.sh'
        self.plugins = self.home/'homebrew/plugins'
        for directory in (self.steam/'steamrt64', self.zd, self.config.parent, self.jup.parent,
                          self.canon.parent, self.binsteam.parent, self.plugins, self.root/'bin', self.home/'homebrew/services'):
            directory.mkdir(parents=True, exist_ok=True)
        self.stock_jup = b'#!/bin/bash\necho stock-jupiter\n'
        self.stock_bin = b'#!/bin/bash\necho stock-binsteam\n'
        self.jup.write_bytes(self.stock_jup); self.binsteam.write_bytes(self.stock_bin)
        for name in ('steam', 'steamclient.so', 'steamui.so'):
            path = self.steam/'steamrt64'/name
            path.write_bytes(ELF); path.chmod(0o755)
        (self.steam/'package').mkdir()
        self.manifest = self.steam/'package/steam_client_steamrt64.manifest'
        self.manifest.write_text('"steamrt64"\n{\n"version" "1792618285"\n}\n')
        self.config.write_text('SmartTickets: 0x2\nAdditionalApps:\n  - 900001\nCustomSetting: keep-me\nMrcServer: http://custom.example/\n')
        (self.plugins/'Wally-Decky').mkdir()
        (self.plugins/'Wally-Decky/keep').write_text('legacy')
        (self.plugins/'Unrelated').mkdir()
        (self.plugins/'Unrelated/keep').write_text('untouched')
        self.env = {'PATH':f'{self.root}/bin:/usr/bin:/bin', 'HOME':str(self.home), 'TERM':'dumb',
                    'ZERODAY_NO_SUDO':'1', 'ZERODAY_JUPITER':str(self.jup),
                    'ZERODAY_JUPITER_CANON':str(self.canon), 'ZERODAY_BIN_STEAM':str(self.binsteam),
                    'ZERODAY_REPATCH_DST':str(self.root/'usr/local/bin/zeroday-repatch.sh'),
                    'ZERODAY_HOOK_DST':str(self.root/'etc/pacman.d/hooks/zeroday-steam.hook'),
                    'ZERODAY_DECKY_PLUGINS':str(self.plugins), 'TEST_ROOT':str(self.root)}
        self.stub('pgrep', 'exit 1')
        self.stub('systemctl', 'printf "%s\\n" "$*" >> "$TEST_ROOT/services.log"\nexit 1')
        self.stub('curl', 'echo "unexpected network request" >&2\nexit 97')

    def stub(self, name, body):
        path = self.root/'bin'/name
        path.write_text('#!/bin/bash\n'+body+'\n'); path.chmod(0o755)

    def run_install(self, *flags, fail=False, download=False, restart=False):
        args = ['bash', str(INSTALLER), '--yes']
        if not restart: args += ['--no-restart']
        if not download and '--uninstall' not in flags: args += ['--local', str(DIST)]
        process = subprocess.run(args+list(flags), env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=35)
        (self.root/'last-run.log').write_text(process.stdout)
        if fail: self.assertNotEqual(process.returncode, 0, process.stdout)
        else: self.assertEqual(process.returncode, 0, process.stdout)
        return process.stdout

    def assert_clean(self):
        self.assertFalse(list(self.zd.glob('.transaction.*')))
        self.assertEqual((self.plugins/'Unrelated/keep').read_text(), 'untouched')

    def test_install_upgrade_and_clean_uninstall(self):
        self.run_install()
        self.assertEqual((self.zd/'ZeroDay.so').read_bytes(), (PAYLOAD/'ZeroDay.so').read_bytes())
        self.assertEqual((self.zd/'library-inject.so').read_bytes(), (PAYLOAD/'library-inject.so').read_bytes())
        self.assertEqual(self.jup.read_bytes(), (PAYLOAD/'steam-jupiter.patched').read_bytes())
        self.assertTrue((self.steam/'.steam-enable-steamrt64-client').exists())
        self.assertTrue((self.plugins/'ZeroDay/main.py').exists())
        self.assertFalse((self.plugins/'Wally-Decky').exists())
        self.assertIn('CustomSetting: keep-me', self.config.read_text())
        self.assertIn('SmartTickets: 0x2', self.config.read_text())
        inode = (self.zd/'ZeroDay.so').stat().st_ino
        self.run_install()
        self.assertNotEqual(inode, (self.zd/'ZeroDay.so').stat().st_ino)
        self.assertEqual(self.binsteam.read_text().count('export LD_AUDIT='), 1)
        # A newer backup can itself be modded; uninstall must select a clean one.
        (self.jup.parent/'steam-jupiter.bak-zeroday-newer').write_bytes(self.jup.read_bytes())
        self.run_install('--uninstall')
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertEqual(self.binsteam.read_bytes(), self.stock_bin)
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assertFalse((self.steam/'.steam-enable-steamrt64-client').exists())
        self.assertTrue(self.config.exists())
        self.assert_clean()

    def test_no_plugin_preserves_all_plugin_directories(self):
        self.run_install('--no-plugin')
        self.assertEqual((self.plugins/'Wally-Decky/keep').read_text(), 'legacy')
        self.assertFalse((self.plugins/'ZeroDay').exists())
        self.assert_clean()

    def test_fresh_config_enables_downloads(self):
        self.config.unlink()
        self.run_install('--no-plugin')
        self.assertIn('EnableDownloads: yes', self.config.read_text())
        self.assert_clean()

    def test_existing_runtime_opt_in_is_kept_on_uninstall(self):
        marker = self.steam/'.steam-enable-steamrt64-client'
        marker.touch()
        self.run_install('--no-plugin')
        self.run_install('--uninstall')
        self.assertTrue(marker.exists())

    def test_missing_x64_client_fails_before_changes(self):
        (self.steam/'steamrt64/steamclient.so').unlink()
        old = self.config.read_bytes()
        self.run_install('--no-plugin', fail=True)
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertEqual(self.config.read_bytes(), old)
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assert_clean()

    def test_failure_restores_existing_install_and_config(self):
        old = b'old-library-keep-open'
        (self.zd/'ZeroDay.so').write_bytes(old)
        (self.zd/'library-inject.so').write_bytes(b'old-inject')
        config_before = self.config.read_bytes()
        self.stub('install', 'case "${@: -1}" in *steam-jupiter.patched) exit 78;; esac\nexec /usr/bin/install "$@"')
        # Open readers still see the old inode while replacement happens.
        with (self.zd/'ZeroDay.so').open('rb') as reader:
            self.run_install('--no-plugin', fail=True)
            self.assertEqual(reader.read(), old)
        self.assertEqual((self.zd/'ZeroDay.so').read_bytes(), old)
        self.assertEqual((self.zd/'library-inject.so').read_bytes(), b'old-inject')
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertEqual(self.binsteam.read_bytes(), self.stock_bin)
        self.assertFalse((self.steam/'.steam-enable-steamrt64-client').exists())
        self.assert_clean()

    def test_failed_restore_keeps_recovery_copies(self):
        (self.zd/'ZeroDay.so').write_bytes(b'old-library')
        self.stub('install', 'case "${@: -1}" in *steam-jupiter.patched) exit 78;; esac\nexec /usr/bin/install "$@"')
        self.stub('cp', 'case "$3" in */.transaction.*/*) exit 79;; esac\nexec /usr/bin/cp "$@"')
        output = self.run_install('--no-plugin', fail=True)
        self.assertIn('Recovery copies are kept at:', output)
        backups = list(self.zd.glob('.transaction.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0]/'0').read_bytes(), b'old-library')

    def test_repair_upgrades_old_audit_line_once(self):
        self.run_install('--no-plugin')
        self.binsteam.write_text('#!/bin/bash\nexport LD_AUDIT="$HOME/.local/share/ZeroDay/ZeroDay.so"\necho stock-binsteam\n')
        script = ['bash', str(PAYLOAD/'zeroday-repatch.sh')]
        subprocess.run(script, env=self.env, check=True, stdout=subprocess.DEVNULL)
        repaired = self.binsteam.read_bytes()
        self.assertIn(b'export STEAM_FORCE_CLIENT=steamrt64', repaired)
        self.assertEqual(repaired.count(b'export LD_AUDIT='), 1)
        backups = list(self.binsteam.parent.glob('*.bak-syu-*'))
        subprocess.run(script, env=self.env, check=True, stdout=subprocess.DEVNULL)
        self.assertEqual(self.binsteam.read_bytes(), repaired)
        self.assertEqual(list(self.binsteam.parent.glob('*.bak-syu-*')), backups)

    def test_launcher_desktop_gamepad_and_zombie_paths(self):
        launcher = self.root/'launcher'
        target = self.root/'client'
        target.write_text('#!/bin/bash\nprintf "%s\\n" "$STEAM_FORCE_CLIENT" "$@" > "$HOME/launched"\n')
        target.chmod(0o755)
        launcher.write_text((PAYLOAD/'steam-jupiter.patched').read_text().replace('/usr/lib/steam/steam', shlex.quote(str(target))))
        log = self.home/'launched'
        def launch(*args):
            subprocess.run(['bash', str(launcher), *args], env=self.env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        launch('-silent')
        self.assertEqual(log.read_text(), 'steamrt64\n-steamdeck\n-silent\n')
        log.unlink()
        self.stub('pgrep', 'printf "%s\\n" "$*" > "$TEST_ROOT/pgrep-args"\necho 999999')
        self.stub('ps', 'echo Sl')
        launch()
        self.assertFalse(log.exists())
        self.assertIn('-A -u ', (self.root/'pgrep-args').read_text())
        self.stub('ps', 'echo Z')
        launch('-silent')
        self.assertTrue(log.exists())
        launch('-gamepadui', '-steampal', '-steamos3')
        self.assertEqual(log.read_text(), 'steamrt64\n-steamdeck\n-gamepadui\n-steamos3\n')

    def test_installer_restart_preserves_deck_identity(self):
        dmi = self.root/'dmi';dmi.mkdir()
        self.env['ZERODAY_DMI_ROOT'] = str(dmi)
        launcher = self.home/'.local/bin/steam-desktop'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/restart-args"\n')
        launcher.chmod(0o755)
        result = self.home/'restart-args'
        for vendor, product, expected in [('Valve','Galileo','-steamdeck\n-silent\n'),('Other','Desktop','-silent\n')]:
            with self.subTest(vendor=vendor,product=product):
                (dmi/'board_vendor').write_text(vendor+'\n')
                (dmi/'product_name').write_text(product+'\n')
                result.unlink(missing_ok=True)
                self.run_install('--no-plugin',restart=True)
                for attempt in range(100):
                    if result.exists(): break
                    time.sleep(.01)
                self.assertEqual(result.read_text(),expected)

    def test_proxy_update_restarts_running_service(self):
        self.stub('systemctl', 'printf "%s\\n" "$*" >> "$TEST_ROOT/services.log"\nexit 0')
        self.run_install('--no-plugin')
        self.assertIn('--user restart zeroday-mrc-proxy.service', (self.root/'services.log').read_text())

    def server(self, corrupt=False):
        directory=self.root/'web';directory.mkdir()
        archive=directory/'ZeroDay-payload.tar.gz'
        with tarfile.open(archive,'w:gz') as tar: tar.add(PAYLOAD, arcname='payload', filter=lambda t: None if '__pycache__' in t.name or '.before-' in t.name else t)
        digest='0'*64 if corrupt else hashlib.sha256(archive.read_bytes()).hexdigest()
        (directory/'SHA256SUMS').write_text(f'{digest}  ZeroDay-payload.tar.gz\n')
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(directory),**kwargs)
            def log_message(self,*args): pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.stub('curl','exec /usr/bin/curl "$@"')
        return f'http://127.0.0.1:{server.server_port}'

    def test_http_download_install_and_temporary_cleanup(self):
        temp=self.root/'downloads';temp.mkdir();self.env['TMPDIR']=str(temp)
        self.run_install('--no-plugin','--repo',self.server(),download=True)
        self.assertFalse(list(temp.iterdir()))
        self.assertTrue((self.zd/'ZeroDay.so').exists())
        self.assert_clean()

    def test_bad_checksum_leaves_install_untouched(self):
        temp=self.root/'downloads';temp.mkdir();self.env['TMPDIR']=str(temp)
        old=self.config.read_bytes()
        self.run_install('--no-plugin','--repo',self.server(corrupt=True),download=True,fail=True)
        self.assertEqual(self.jup.read_bytes(),self.stock_jup)
        self.assertEqual(self.config.read_bytes(),old)
        self.assertFalse(list(temp.iterdir()))
        self.assert_clean()

    def steam_update_server(self, version='1792618285', body=None):
        directory = self.root/'steam-web'; directory.mkdir()
        (directory/'steam_client_steamrt64').write_text(
            body if body is not None else f'"steamrt64"\n{{\n"version" "{version}"\n}}\n')
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(directory), **kwargs)
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.env['ZERODAY_STEAM_UPDATE_URL'] = f'http://127.0.0.1:{server.server_port}'
        self.stub('curl', 'exec /usr/bin/curl "$@"')
        return directory

    def old_steam(self):
        (self.steam/'steamrt64/steamclient.so').write_bytes(b'old client')
        self.manifest.write_text('"steamrt64"\n{\n"version" "1780000000"\n}\n')
        self.pin = 'CustomSteamSetting=keep\nBootStrapperInhibitAll=enable\nBootStrapperForceSelfUpdate=disable\n'
        (self.steam/'steam.cfg').write_text(self.pin)

    def fake_bootstrap(self, version='1792618285', fail=False, wrong_hash=False, status=0):
        self.env['TEST_ELF_HEX'] = ELF.hex()
        self.env['TEST_UPDATE_VERSION'] = version
        self.env['TEST_BAD_CLIENT'] = str(int(wrong_hash))
        self.env['TEST_UPDATE_FAIL'] = str(int(fail))
        self.env['TEST_UPDATE_STATUS'] = str(status)
        (self.steam/'steam.sh').write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$@" > "$TEST_ROOT/update-args"
printf '%s' "${LD_AUDIT-}" > "$TEST_ROOT/update-audit"
test "$STEAM_FORCE_CLIENT" = steamrt64
test -f .steam-enable-steamrt64-client
! rg -q '^BootStrapper' steam.cfg
grep -q 'CustomSteamSetting=keep' steam.cfg
test "$TEST_UPDATE_FAIL" = 0 || exit 37
python3 - <<'PY'
import os
from pathlib import Path
data = bytes.fromhex(os.environ['TEST_ELF_HEX'])
for name in ('steamclient.so', 'steamui.so'):
    Path('steamrt64', name).write_bytes(data if os.environ['TEST_BAD_CLIENT'] == '0' else data + b'unknown revision')
Path('package/steam_client_steamrt64.manifest').write_text('"steamrt64" { "version" "' + os.environ['TEST_UPDATE_VERSION'] + '" }')
PY
exit "$TEST_UPDATE_STATUS"
''')

    def test_compatible_steam_does_not_query_or_update_it(self):
        before = self.manifest.read_bytes()
        output = self.run_install('--no-plugin')
        self.assertIn('updating ZeroDay only', output)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((self.root/'update-args').exists())

    def test_supported_steam_update_is_automatic_and_restores_pin(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap()
        output = self.run_install('--no-plugin')
        self.assertIn('will be installed automatically', output)
        self.assertIn('ZeroDay is installed.', output)
        self.assertEqual((self.steam/'steam.cfg').read_text(), self.pin)
        self.assertIn('-exitsteam', (self.root/'update-args').read_text())
        self.assertEqual((self.root/'update-audit').read_text(), '')
        self.assertFalse((self.zd/'client-update-pending').exists())
        self.assertEqual((self.zd/'ZeroDay.so').read_bytes(), (PAYLOAD/'ZeroDay.so').read_bytes())
        self.assert_clean()

    def test_yes_does_not_accept_unsupported_update(self):
        self.old_steam(); self.steam_update_server(version='1890000000'); self.fake_bootstrap()
        old = self.config.read_bytes()
        output = self.run_install('--no-plugin', fail=True)
        self.assertIn('--yes does not accept unsupported updates', output)
        self.assertFalse((self.root/'update-args').exists())
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertEqual(self.config.read_bytes(), old)
        self.assertFalse((self.zd/'client-update-pending').exists())
        self.assert_clean()

    def test_explicit_unsupported_update_does_not_install_mod(self):
        self.old_steam(); self.steam_update_server(version='1890000000')
        self.fake_bootstrap(version='1890000000', wrong_hash=True)
        self.jup.write_text('#!/bin/bash\nexport LD_AUDIT="$HOME/.local/share/ZeroDay/ZeroDay.so"\necho stock\n')
        old = self.config.read_bytes()
        output = self.run_install('--no-plugin', '--update-steam-anyway')
        self.assertIn('ZeroDay was not installed and remains disabled', output)
        self.assertNotIn('ZeroDay is installed.', output)
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertTrue((self.steam/'.steam-enable-steamrt64-client').exists())
        self.assertIn('client-update-pending', self.jup.read_text())
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assertEqual(self.config.read_bytes(), old)
        self.assertEqual((self.steam/'steam.cfg').read_text(), self.pin)
        self.assert_clean()

    def test_update_failure_keeps_existing_mod_disabled_and_restores_cfg(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap(fail=True)
        self.jup.write_text('#!/bin/bash\nexport LD_AUDIT="$HOME/.local/share/ZeroDay/ZeroDay.so"\necho stock\n')
        repair = Path(self.env['ZERODAY_REPATCH_DST'])
        repair.parent.mkdir(parents=True, exist_ok=True)
        repair.write_text('#!/bin/bash\necho legacy unguarded repair\n')
        (self.zd/'ZeroDay.so').write_bytes(b'previous mod')
        old = self.config.read_bytes()
        output = self.run_install('--no-plugin', fail=True)
        self.assertIn('ZeroDay stays disabled', output)
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertIn('client-update-pending', self.jup.read_text())
        self.assertEqual((self.zd/'ZeroDay.so').read_bytes(), b'previous mod')
        self.assertEqual(self.config.read_bytes(), old)
        self.assertEqual((self.steam/'steam.cfg').read_text(), self.pin)
        self.assertIn('client-update-pending', repair.read_text())
        self.binsteam.write_bytes(self.stock_bin)
        subprocess.run(['bash', str(repair)], env=self.env, check=True, stdout=subprocess.DEVNULL)
        self.assertIn('client-update-pending', self.binsteam.read_text())
        self.assert_clean()

    def test_native_exitsteam_status_requires_verified_files(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap(status=254)
        self.run_install('--no-plugin')
        self.assertFalse((self.zd/'client-update-pending').exists())
        self.assertTrue((self.zd/'ZeroDay.so').exists())

    def test_native_exitsteam_status_cannot_accept_wrong_client(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap(status=254, wrong_hash=True)
        self.run_install('--no-plugin', fail=True)
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertFalse((self.zd/'ZeroDay.so').exists())

    def test_changed_client_after_update_cannot_install_mod(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap(wrong_hash=True)
        self.run_install('--no-plugin', fail=True)
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assertEqual((self.steam/'steam.cfg').read_text(), self.pin)
        self.assert_clean()

    def test_changed_available_build_during_update_cannot_install_mod(self):
        self.old_steam(); self.steam_update_server(); self.fake_bootstrap(version='1890000000')
        self.run_install('--no-plugin', fail=True)
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assert_clean()

    def test_invalid_remote_manifest_leaves_install_untouched(self):
        self.old_steam(); self.steam_update_server(body='<html>server error</html>')
        self.run_install('--no-plugin', fail=True)
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertFalse((self.zd/'client-update-pending').exists())
        self.assertFalse((self.zd/'ZeroDay.so').exists())
        self.assert_clean()

    def test_newer_installed_client_is_not_downgraded(self):
        self.old_steam(); self.steam_update_server()
        self.manifest.write_text('"steamrt64" { "version" "1890000000" }')
        output = self.run_install('--no-plugin', fail=True)
        self.assertIn('refusing to downgrade', output)
        self.assertFalse((self.root/'update-args').exists())
        self.assertFalse((self.zd/'client-update-pending').exists())

    def test_known_client_with_unknown_ui_is_not_compatible(self):
        (self.steam/'steamrt64/steamui.so').write_bytes(b'changed UI')
        self.run_install('--no-plugin', fail=True)
        self.assertFalse((self.zd/'ZeroDay.so').exists())

    def test_standalone_check_is_read_only_and_offline(self):
        before = self.config.read_bytes()
        result = subprocess.run(['bash', str(INSTALLER), '--check'], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['compatible'])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.zd/'install.log').exists())
        (self.steam/'steamrt64/steamui.so').unlink()
        result = subprocess.run(['bash', str(INSTALLER), '--check'], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(json.loads(result.stdout)['compatible'])

    def test_pending_update_marker_is_cleared_by_compatible_install(self):
        (self.zd/'client-update-pending').touch()
        self.run_install('--no-plugin')
        self.assertFalse((self.zd/'client-update-pending').exists())

    def test_guard_still_disables_mod_after_repatch(self):
        self.run_install('--no-plugin')
        (self.zd/'client-update-pending').touch()
        self.binsteam.write_bytes(self.stock_bin)
        subprocess.run(['bash', str(PAYLOAD/'zeroday-repatch.sh')], env=self.env,
                       check=True, stdout=subprocess.DEVNULL)
        # Source only the export line; no native library is loaded into the test.
        line = next(line for line in self.binsteam.read_text().splitlines() if line.startswith('export LD_AUDIT='))
        result = subprocess.run(['bash', '-c', line+'; printf "%s" "${LD_AUDIT-}"'],
                                env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, '')

    def interactive_update_choice(self, accept):
        self.old_steam(); self.steam_update_server(version='1890000000')
        self.fake_bootstrap(version='1890000000', wrong_hash=True)
        temp = self.root/'ui-temp'; temp.mkdir()
        env = {**self.env, 'TERM': 'xterm-256color', 'TMPDIR': str(temp), 'ZERODAY_UI_SOUND': '0'}
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 36, 120, 0, 0))
        before = termios.tcgetattr(slave)
        def terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        process = subprocess.Popen(['bash', str(INSTALLER), '--local', str(DIST), '--no-plugin', '--no-restart'],
                                   env=env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                                   pass_fds=(slave,), preexec_fn=terminal)
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        output = bytearray()
        disclaimer = gate = choice = closed = False
        deadline = time.monotonic() + 35
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    block = os.read(master, 1048576); output.extend(block)
                    if b'\x1b[c' in block: os.write(master, b'\x1b[?1;2c')
                    if b'\x1b[16t' in block: os.write(master, b'\x1b[6;18;9t')
                if not disclaimer and list(temp.glob('zeroday-ui.*/disclaimer-ready')):
                    os.write(master, b'\r'); disclaimer = True
                if not gate and list(temp.glob('zeroday-ui.*/gate-ready')):
                    os.write(master, b'\r'); gate = True
                if not choice and b'//  Steam compatibility' in output:
                    os.write(master, b'\x1b[B\r' if accept else b'\r'); choice = True
                if accept and not closed and b'A/Enter: close' in output:
                    os.write(master, b'\r'); closed = True
            self.assertIsNotNone(process.poll(), output[-2000:])
            self.assertTrue(choice, output[-2000:])
            self.assertEqual(process.returncode, 0 if accept else 1, output[-2000:])
            self.assertEqual(termios.tcgetattr(slave), before)
            self.assertFalse(list(temp.iterdir()))
        finally:
            if process.poll() is None: process.kill()
            process.wait()

    def test_interactive_unsupported_update_defaults_to_cancel(self):
        self.interactive_update_choice(accept=False)
        self.assertFalse((self.root/'update-args').exists())
        self.assertEqual(self.jup.read_bytes(), self.stock_jup)
        self.assertFalse((self.zd/'client-update-pending').exists())

    def test_interactive_unsupported_update_accepts_deck_arrow_and_enter(self):
        self.interactive_update_choice(accept=True)
        self.assertTrue((self.root/'update-args').exists())
        self.assertTrue((self.zd/'client-update-pending').exists())
        self.assertFalse((self.zd/'ZeroDay.so').exists())

if __name__ == '__main__': unittest.main(verbosity=2)
