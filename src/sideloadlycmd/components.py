"""Plan and apply removals of known optional components, never dependencies."""
from pathlib import Path,PurePosixPath
import json
import plistlib
import shutil
import time
import zipfile
from .errors import CliError,ExitCode


def plan_components(ipa,app_directory,strip_watch=False,strip_extensions=None,strict_components=False,excluded_paths=None,deadline=float('inf')):
    prefix=app_directory.rstrip('/')+'/'
    metadata={};containers=set()
    with zipfile.ZipFile(ipa) as archive:
        for item in archive.infolist():
            if time.monotonic()>=deadline: raise CliError('component inspection timed out',ExitCode.TIMEOUT,'timeout')
            if not item.filename.startswith(prefix): continue
            relative=PurePosixPath(item.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:
                raise CliError('invalid optional component path',ExitCode.INVALID_IPA,'component_path_invalid')
            if relative.parts and relative.parts[0] in {'Watch','AppClips'}:
                containers.add(relative.parts[0]);continue
            if relative.name!='Info.plist': continue
            parent=relative.parent
            if parent==PurePosixPath('.') or parent.suffix not in {'.app','.appex'}: continue
            if 'Frameworks' in parent.parts or 'Libraries' in parent.parts: continue
            if item.file_size>1024*1024: raise CliError('component metadata is too large',ExitCode.INVALID_IPA,'component_metadata_invalid')
            try: data=plistlib.loads(archive.read(item))
            except (ValueError,TypeError): raise CliError('component metadata is invalid',ExitCode.INVALID_IPA,'component_metadata_invalid')
            if not isinstance(data,dict): raise CliError('component metadata is not a dictionary',ExitCode.INVALID_IPA,'component_metadata_invalid')
            metadata[str(parent)]=data
    removals={}
    def remove(path,reason,impact):
        data=metadata.get(path,{})
        removals[path]={'path':path,'bundle_id':data.get('CFBundleIdentifier'),'reason':reason,'impact':impact}
    for container in containers:
        remove(container,'explicit' if container=='Watch' and strip_watch else 'unsupported',
               'Watch companion functionality unavailable' if container=='Watch' else 'App Clip functionality unavailable')
    for path,data in metadata.items():
        platforms=data.get('CFBundleSupportedPlatforms',[])
        platform=str(data.get('DTPlatformName','')).lower()
        point=data.get('NSExtension',{}).get('NSExtensionPointIdentifier','') if isinstance(data.get('NSExtension',{}),dict) else ''
        unsupported=(bool(platforms) and isinstance(platforms,list) and 'iPhoneOS' not in platforms) or (platform and platform!='iphoneos') or str(point).startswith('com.apple.watchkit')
        if unsupported: remove(path,'unsupported','Features supplied by this unsupported optional bundle unavailable')
    for identifier in strip_extensions or []:
        matches=[path for path,data in metadata.items() if path.endswith('.appex') and data.get('CFBundleIdentifier')==identifier]
        if len(matches)!=1: raise CliError('strip-extension must match one original extension Bundle ID',ExitCode.USAGE,'extension_selection_invalid')
        remove(matches[0],'explicit','Features supplied by the explicitly removed extension unavailable')
    allowed=set(metadata)|containers
    for path in excluded_paths or []:
        if not isinstance(path,str) or path not in allowed:
            raise CliError('saved component exclusion does not match an optional source component',ExitCode.PREFLIGHT,'component_exclusion_invalid')
        remove(path,'registered_policy','Previously excluded optional component remains excluded')
    if strict_components and any(row['reason']=='unsupported' for row in removals.values()):
        raise CliError('unsupported optional components found; strict-components requires preserving them',ExitCode.PREFLIGHT,'unsupported_components')
    # Only topmost removals are applied. Keep supported extension metadata intact.
    paths=sorted(removals,key=lambda value:(len(PurePosixPath(value).parts),value))
    top=[]
    for path in paths:
        if not any(PurePosixPath(path).is_relative_to(PurePosixPath(parent)) for parent in top): top.append(path)
    kept=[{'path':path,'bundle_id':data.get('CFBundleIdentifier')} for path,data in sorted(metadata.items()) if not any(PurePosixPath(path).is_relative_to(PurePosixPath(parent)) for parent in top)]
    return {'removed_components':[removals[path] for path in top],'kept_components':kept,
            'component_exclusions':top,'strip_watch':strip_watch or 'Watch' in top,
            'warnings':['Removed optional component '+json.dumps(path)+': '+removals[path]['impact'] for path in top]}


def apply_components(app,plan):
    root=app.resolve()
    for row in plan['removed_components']:
        path=app/row['path']
        resolved=path.resolve()
        if resolved==root or not resolved.is_relative_to(root):
            raise CliError('optional removal escapes app root',ExitCode.INVALID_IPA,'component_path_invalid')
        if path.is_symlink(): path.unlink();continue
        if any(part in {'Frameworks','Libraries'} for part in resolved.relative_to(root).parts):
            raise CliError('cannot remove a required library as an optional component',ExitCode.INVALID_IPA,'component_path_invalid')
        if path.is_dir(): shutil.rmtree(path)
        elif path.exists():
            raise CliError('optional component is not a bundle directory',ExitCode.INVALID_IPA,'component_path_invalid')
