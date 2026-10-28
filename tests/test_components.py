from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile
from sideloadlycmd.components import plan_components,apply_components
from sideloadlycmd.errors import CliError

class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.ipa=self.root/'app.ipa';self.appdir='Payload/Main.app'
    def source(self,watch=True,clips=False,extra=None):
        rows={'Info.plist':{'CFBundleIdentifier':'com.example.app'},'PlugIns/Live.appex/Info.plist':{'CFBundleIdentifier':'com.example.app.Live','CFBundlePackageType':'XPC!','CFBundleSupportedPlatforms':['iPhoneOS'],'NSExtension':{'NSExtensionPointIdentifier':'com.apple.widgetkit-extension'}},'Frameworks/Required.framework/Info.plist':{'CFBundlePackageType':'FMWK','CFBundleSupportedPlatforms':['MacOSX']}}
        if watch:rows['Watch/Companion.app/Info.plist']={'CFBundleIdentifier':'com.example.app.watch','CFBundleSupportedPlatforms':['WatchOS']}
        if clips:rows['AppClips/Clip.app/Info.plist']={'CFBundleIdentifier':'com.example.app.Clip','CFBundleSupportedPlatforms':['iPhoneOS']}
        rows.update(extra or {})
        with zipfile.ZipFile(self.ipa,'w') as archive:
            for path,metadata in rows.items():archive.writestr(self.appdir+'/'+path,plistlib.dumps(metadata))
        with zipfile.ZipFile(self.ipa) as archive:archive.extractall(self.root/'extracted')
        return self.root/'extracted'/self.appdir
    def test_default_removes_watch_but_preserves_live_extension_and_required_framework(self):
        app=self.source();plan=plan_components(self.ipa,self.appdir)
        self.assertEqual(plan['component_exclusions'],['Watch'])
        self.assertEqual(plan['removed_components'][0]['reason'],'unsupported')
        self.assertTrue(plan['warnings'])
        apply_components(app,plan)
        self.assertFalse((app/'Watch').exists())
        self.assertTrue((app/'PlugIns/Live.appex').is_dir())
        self.assertTrue((app/'Frameworks/Required.framework').is_dir())
    def test_explicit_extension_removal_is_exact_and_recorded(self):
        app=self.source(watch=False)
        plan=plan_components(self.ipa,self.appdir,strip_extensions=['com.example.app.Live'])
        self.assertEqual(plan['removed_components'][0]['reason'],'explicit')
        apply_components(app,plan);self.assertFalse((app/'PlugIns/Live.appex').exists())
    def test_unknown_extension_selector_never_removes_anything(self):
        app=self.source(watch=False)
        with self.assertRaises(CliError):plan_components(self.ipa,self.appdir,strip_extensions=['com.example.app'])
        self.assertTrue((app/'Info.plist').exists())
    def test_app_clip_is_omitted_without_losing_supported_extensions(self):
        self.source(watch=False,clips=True)
        plan=plan_components(self.ipa,self.appdir)
        self.assertEqual(plan['component_exclusions'],['AppClips'])
        self.assertEqual(plan['kept_components'][0]['bundle_id'],'com.example.app.Live')
    def test_strict_mode_stops_before_removal(self):
        app=self.source()
        with self.assertRaises(CliError) as raised:plan_components(self.ipa,self.appdir,strict_components=True)
        self.assertEqual(raised.exception.category,'unsupported_components')
        self.assertTrue((app/'Watch').exists())
    def test_explicit_watch_removal_allowed_even_in_strict_mode(self):
        self.source();plan=plan_components(self.ipa,self.appdir,strip_watch=True,strict_components=True)
        self.assertEqual(plan['removed_components'][0]['reason'],'explicit')
    def test_foreign_platform_optional_extension_is_omitted(self):
        self.source(watch=False,extra={'PlugIns/TV.appex/Info.plist':{'CFBundleIdentifier':'com.example.app.TV','CFBundleSupportedPlatforms':['AppleTVOS']}})
        self.assertEqual(plan_components(self.ipa,self.appdir)['component_exclusions'],['PlugIns/TV.appex'])
    def test_registered_exclusions_are_replayed_and_dependencies_rejected(self):
        self.source(watch=False)
        plan=plan_components(self.ipa,self.appdir,excluded_paths=['PlugIns/Live.appex'])
        self.assertEqual(plan['removed_components'][0]['reason'],'registered_policy')
        for path in ['.','../outside','Frameworks/Required.framework']:
            with self.subTest(path=path),self.assertRaises(CliError):plan_components(self.ipa,self.appdir,excluded_paths=[path])
