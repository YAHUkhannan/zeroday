#!/usr/bin/env python3
"""Run the generated installer with isolated home, system paths and service stubs."""
from pathlib import Path
import hashlib
import http.server
import os
import shutil
import shlex
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest

DIST = Path(__file__).resolve().parents[1]
INSTALLER = DIST / 'install.sh'
PAYLOAD = DIST / 'payload'
ELF = b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\x00'

class Install(unittest.TestCase):
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
        for name in ('steam', 'steamclient.so'):
            path = self.steam/'steamrt64'/name
            path.write_bytes(ELF); path.chmod(0o755)
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
        (self.steam/'package').mkdir()
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

if __name__ == '__main__': unittest.main(verbosity=2)
