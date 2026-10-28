import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('homebrew_generator',Path(__file__).parents[1]/'scripts/generate_homebrew_formula.py')
formula=importlib.util.module_from_spec(spec);spec.loader.exec_module(formula)

class HomebrewTests(unittest.TestCase):
    def test_real_hash_and_url_are_preserved_and_ruby_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'archive.tar.gz';archive.write_bytes(b'fixture release bytes')
            text=formula.render('https://example.org/VERSION.tar.gz','https://example.org/project',archive,'0.2.0')
            self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(),text)
            self.assertIn('https://example.org/VERSION.tar.gz',text)
            ruby=Path(tmp)/'sideloadlycmd.rb';ruby.write_text(text)
            subprocess.run(['/usr/bin/ruby','-c',str(ruby)],check=True,capture_output=True)
    def test_rejects_credentials_and_ephemeral_signed_urls(self):
        for url in ['http://example.org/app','https://user:pass@example.org/app','https://example.org/app?token=secret']:
            with self.subTest(url=url),self.assertRaises(Exception): formula.public_url(url)
