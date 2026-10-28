"""Explicit one-command preparation and installation with a shared deadline."""
from pathlib import Path
import tempfile
import time
from .progress import stage
from .errors import CliError,ExitCode
from .ipa import inspect_ipa,IPAError
from .components import plan_components
from .teams import select_team
from .headless import resolve_device
from .xcode_backend import prepare,install_prepared,_remaining,require_global_xcode


def sideload(ipa,device=None,team=None,account='xcode',output=None,bundle_id=None,
             bundle_id_mode='automatic',strip_watch=False,offline=False,timeout=900,dry_run=False,strip_extensions=None,strict_components=False):
    deadline=time.monotonic()+timeout
    stage("device_selection", "Checking the USB target")
    target=device if device is not None else resolve_device(None,deadline)
    device_reason="explicit" if device is not None else "unique_usb"
    stage("team_selection", "Checking Xcode and selecting the signing team")
    if not offline: require_global_xcode(deadline)
    selected,reason=select_team(team,account,offline)
    stage("ipa_inspection", "Inspecting the source IPA")
    try: info=inspect_ipa(ipa.expanduser(),timeout_seconds=_remaining(deadline))
    except IPAError as error: raise CliError(str(error),ExitCode.INVALID_IPA,'invalid_ipa') from error
    original=bundle_id or info.bundle_id
    if original!=info.bundle_id:
        raise CliError('requested source Bundle ID differs from the IPA',ExitCode.INVALID_IPA,'ipa_identity_mismatch')
    final=original if bundle_id_mode=='exact' else original+'.'+selected
    if dry_run:
        stage("component_plan", "Planning optional component handling without signing or installing")
        component_plan=plan_components(ipa,info.app_directory,strip_watch,strip_extensions,strict_components,deadline=deadline)
        return {'command':'sideload','state':'planned','team_id':selected,'team_selection':reason,
                'source_bundle_id':original,'final_bundle_id':final,**component_plan,'device_selection':device_reason,
                'message':f'Plan: team={selected} ({reason}), bundle={final}; no signing or installation',
                'signing':'not_attempted','installation':'not_attempted','device':'not_verified',
                'refresh':'not_requested'},ExitCode.OK
    if output is None:
        from .refresh import _root
        base=_root(Path.home()/'Library/Application Support/SideloadlyCmd/prepared')
        output=Path(tempfile.mkdtemp(prefix='sideload-',dir=base))/'prepared'
    prepared=prepare(ipa,output,account,selected,original,bundle_id_mode,target,strip_watch,_remaining(deadline),offline=offline,strip_extensions=strip_extensions,strict_components=strict_components)
    try:
        result=install_prepared(output,target,selected,prepared['final_bundle_id'],_remaining(deadline))
    except CliError as error:
        return {'command':'sideload','state':'prepared_install_unverified','prepared':str(output),
                'source_bundle_id':original,'final_bundle_id':prepared['final_bundle_id'],'team_id':selected,
                'team_selection':reason,'device_selection':device_reason,'device_app':'unverified','category':error.category,
                'message':error.message,'removed_components':prepared.get('removed_components',[]),'warnings':prepared.get('warnings',[]),'refresh':'not_registered_by_native_cli','exit_code':int(error.code)},error.code
    result={**result,'command':'sideload','prepared':str(output),'source_bundle_id':original,
            'team_selection':reason,'device_selection':device_reason,'refresh':'not_registered_by_native_cli',
            'removed_components':prepared.get('removed_components',[]),'warnings':prepared.get('warnings',[])}
    code=ExitCode(result.get('exit_code',0 if result.get('device_app')=='version_and_build_verified' else 3))
    if code==ExitCode.OK and result.get('device_app')=='version_and_build_verified':
        result['state']='installed_and_verified'
        result['message']=f"Installed {prepared['final_bundle_id']}; device version verified. Prepared files: {output}. Automatic refresh is not enabled."
    if result.get('warnings'):
        result['message']=result.get('message','')+' Component warnings: '+'; '.join(result['warnings'])
    return result,code
