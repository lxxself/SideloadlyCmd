#!/usr/bin/env python3
"""Generate a tap formula for a supplied public release archive; does not publish."""
import argparse
import ast
import hashlib
import re
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent


def literal(value): return "'"+str(value).replace('\\','\\\\').replace("'","\\'")+"'"


def public_url(value):
    url=urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise argparse.ArgumentTypeError('use a stable public HTTPS URL without credentials, query or fragment')
    return value


def render(url,homepage,archive,version):
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    template='''# Generated for a maintainer-owned tap; not an official Homebrew listing.
class Sideloadlycmd < Formula
  desc "Native macOS IPA signing, installation and refresh using Xcode accounts"
  homepage HOMEPAGE
  url RELEASE_URL
  version VERSION
  sha256 DIGEST
  license "MIT"
  depends_on :macos
  depends_on "python@3.12"
  depends_on "libimobiledevice"

  def install
    # Runtime is stdlib-only; no pip downloads during Homebrew's build sandbox.
    (libexec/"src").install "src/sideloadlycmd"
    bin.mkpath
    (bin/"sideloadlycmd").write <<~SH
      #!/bin/sh
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" -I "#{libexec}/src/sideloadlycmd/_entry.py" "$@"
    SH
    (bin/"sideloadlycmd").chmod 0755
    pkgshare.install "README.md", "README.en.md", "docs"
    (pkgshare/"skills").install ".agents/skills/sideloadly-cli"
    prefix.install "LICENSE"
  end

  def caveats
    <<~EOS
      Install full Xcode and select it globally before signing.
      Sign in through Xcode Accounts and keep signing keys on this Mac.
      Stop native refresh scheduling before upgrading this formula or Python,
      then re-enable it after checking the new runtime.
    EOS
  end

  test do
    assert_match "sideload", shell_output("#{bin}/sideloadlycmd --help")
  end
end
'''
    values={'HOMEPAGE':homepage,'RELEASE_URL':url,'VERSION':version,'DIGEST':digest}
    return re.sub(r'\b(HOMEPAGE|RELEASE_URL|VERSION|DIGEST)\b',lambda match:literal(values[match.group(1)]),template)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,type=public_url)
    parser.add_argument('--homepage',required=True,type=public_url)
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    from check_release import check_archive
    check_archive(args.archive)
    with tarfile.open(args.archive) as archive:
        members=[member for member in archive.getmembers() if member.name.endswith('/src/sideloadlycmd/__init__.py') and member.isfile()]
        if len(members)!=1: parser.error('release must contain one identifiable version module')
        tree=ast.parse(archive.extractfile(members[0]).read().decode())
    version=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='__version__' for target in node.targets))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(render(args.url,args.homepage,args.archive,version))
    print(args.output)


if __name__=='__main__': main()
